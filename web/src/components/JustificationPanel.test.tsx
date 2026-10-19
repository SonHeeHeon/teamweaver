import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({ DatasetChangedError: class extends Error {}, postJustification: vi.fn() }));
import { postJustification } from "../api/client";
import { JustificationPanel } from "./JustificationPanel";
import { fallbackText, segmentsOf } from "./justification";
import type { Justification, Meta } from "../api/types";

const META = {
  people: [], skills: [], review_items: [], coworks: [], dataset_version: "v1",
  projects: [{ id: "P1", name: "차세대", sector: "x", phase: "x", start_month: 0, end_month: 5, grade_headcount: {}, monthly_budget: 1 },
             { id: "P2", name: "포털", sector: "x", phase: "x", start_month: 0, end_month: 5, grade_headcount: {}, monthly_budget: 1 }],
} as unknown as Meta;
const ENTRIES = [{ person_id: "p1", project_id: "P1", alloc: 1 }, { person_id: "p2", project_id: "P2", alloc: 1 }];

const base = (over: Partial<Justification>): Justification => ({
  project_id: "P1", method: "template", text: "DP1은 Java 5년[F1]입니다.", addendum: null, appended_adverse: [],
  facts: [{ id: "F1", kind: "SKL", text: "p1 Java 60개월", phrase: "Java 5년", adverse: false },
          { id: "F2", kind: "REQ", text: "요구 Python 미달", phrase: "요구 Python 미달", adverse: true }],
  fallback_reason: null, verification: null, usage: null, dataset_version: "v1", ai_available: true,
  ai_unavailable_reason: null, ...over,
});

function panel() {
  return render(<JustificationPanel meta={META} datasetVersion="v1" entries={ENTRIES} applied={null} weights={{}}
                                    params={null} planLabel="A" planToken="tok" onDatasetChanged={vi.fn()} />);
}

describe("JustificationPanel", () => {
  beforeEach(() => vi.mocked(postJustification).mockReset());

  it("정해진 틀을 먼저 보이고 AI 글이 오면 바꾼다. 근거 칩을 누르면 사실을 강조하고, 서버 덧붙임은 따로 보인다", async () => {
    const add = "다만 다음 사항도 함께 확인이 필요합니다: 요구 Python 미달[F2].";
    vi.mocked(postJustification)
      .mockResolvedValueOnce(base({}))
      .mockResolvedValueOnce(base({ method: "graphrag", text: `DP1은 Java[F1]입니다.\n${add}`, addendum: add,
                                    appended_adverse: ["F2"] }));
    panel();
    expect(await screen.findByText("AI 구성 · 사실은 데이터 그대로")).toBeInTheDocument();
    expect(vi.mocked(postJustification).mock.calls.map((c) => c[0].ai)).toEqual([false, true]);
    expect(screen.getByText(/서버가 덧붙임 — AI 글이 빠뜨린 불리한 사실 1개/)).toBeInTheDocument();
    expect(screen.getByText("불리")).toBeInTheDocument();
    expect(screen.getByText("덧붙임")).toBeInTheDocument();
    expect(screen.queryByText(/\[F\d+\]/)).not.toBeInTheDocument();            // 근거 번호는 보이지 않는다(사용자 결정)
    fireEvent.click(screen.getByRole("button", { name: "근거 보기: 요구 Python 미달" }));
    expect(screen.getByTestId("fact-F2").className).toMatch(/ring-2/);
  });

  it("AI를 쓸 수 없으면 정해진 틀만, 이유를 사람 말로 보인다", async () => {
    vi.mocked(postJustification).mockResolvedValueOnce(base({ ai_available: false, ai_unavailable_reason: "external_blocked" }));
    panel();
    expect(await screen.findByText("정해진 틀")).toBeInTheDocument();
    expect(vi.mocked(postJustification)).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "다시 쓰기" })).toBeDisabled();
  });

  it("검사 탈락 사유를 사람 말로 바꾼다", () => {
    expect(fallbackText("verify:S2,S4")).toBe(
      "정해진 표현 밖의 말이 있어, 사실이 다른 사람·다른 주제 아래 놓여 AI 글을 쓰지 않았습니다(정해진 틀로 대신).");
    expect(fallbackText("external_blocked")).toMatch(/실데이터라 외부 AI로 보내지 않고/);
    expect(fallbackText(null)).toBeNull();
  });

  it("AI 글을 기다리는 동안 사업을 바꾸면 이전 사업의 늦은 응답을 버린다", async () => {
    let late: (j: Justification) => void = () => {};
    vi.mocked(postJustification)
      .mockResolvedValueOnce(base({}))
      .mockReturnValueOnce(new Promise((r) => { late = r; }))
      .mockResolvedValueOnce(base({ project_id: "P2", text: "P2 글[F1]입니다.", ai_available: false }));
    panel();
    await screen.findByRole("button", { name: "근거 보기: Java 5년" });
    fireEvent.change(screen.getByLabelText("소명 사업"), { target: { value: "P2" } });
    expect(await screen.findByText(/P2 글/)).toBeInTheDocument();
    late(base({ method: "graphrag", text: "P1의 늦은 AI 글[F1]입니다." }));
    await waitFor(() => expect(screen.queryByText(/늦은 AI 글/)).not.toBeInTheDocument());
    expect(vi.mocked(postJustification).mock.calls[2][0].project_id).toBe("P2");
  });

  it("첫 요청이 실패하면 다시 불러오기로 회복한다", async () => {
    vi.mocked(postJustification).mockRejectedValueOnce(new Error("잠깐 끊김"))
      .mockResolvedValueOnce(base({ ai_available: false }));
    panel();
    expect(await screen.findByRole("alert")).toHaveTextContent("잠깐 끊김");
    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));
    expect(await screen.findByRole("button", { name: "근거 보기: Java 5년" })).toBeInTheDocument();
  });

  it("서명 없는 플랜이면 요청하지 않고 이유를 보인다", () => {
    render(<JustificationPanel meta={META} datasetVersion="v1" entries={ENTRIES} applied={null} weights={{}}
                               params={null} planLabel="A" planToken={null} onDatasetChanged={vi.fn()} />);
    expect(screen.getByText(/서명 있는 플랜/)).toBeInTheDocument();
    expect(postJustification).not.toHaveBeenCalled();
  });

  it("근거 문구를 찾지 못하면 작은 '근거' 표시로 남긴다", () => {
    expect(segmentsOf("DP1은 Java 5년[F1]입니다.", [{ id: "F1", phrase: "Java 5년" }]))
      .toEqual([{ text: "DP1은 " }, { cite: "F1", label: "Java 5년" }, { text: "입니다." }]);
    expect(segmentsOf("요약[F9]", [])).toEqual([{ text: "요약" }, { cite: "F9", label: null }]);
    // 정해진 틀: "머리: 사실 한 줄. [F#]"
    expect(segmentsOf("요구 기술: Spark 2명 부족. [F5]\n", [{ id: "F5", text: "Spark 2명 부족" }]))
      .toEqual([{ text: "요구 기술: " }, { cite: "F5", label: "Spark 2명 부족" }, { text: ". " }, { text: "\n" }]);
  });

  it("사람별 덧붙임은 '확인 필요 n명 · m건'으로 묶고 사람마다 펼친다", async () => {
    const add = "다만 다음 사항도 함께 확인이 필요합니다: \n- 김민준: 요구 Python 미달[F2]";
    vi.mocked(postJustification).mockResolvedValueOnce(base({ method: "graphrag", ai_available: false,
      text: `글[F1]\n${add}`, addendum: add, appended_adverse: ["F2"],
      addendum_groups: [{ owner: "DP1", owner_label: "김민준", facts: ["F2"] }] }));
    panel();
    expect(await screen.findByText(/확인 필요 1명 · 1건/)).toBeInTheDocument();
    expect(screen.getByText("김민준 · 1건")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "근거 보기: 요구 Python 미달" })).toBeInTheDocument();
  });

  it("AI 글 대기 안내는 서버가 알려 준 모델별 예상 시간을 쓴다", async () => {
    let release: (j: Justification) => void = () => {};
    vi.mocked(postJustification).mockResolvedValueOnce(base({ ai_expected_s: 60 }))
      .mockReturnValueOnce(new Promise((r) => { release = r; }));
    panel();
    expect(await screen.findByText(/AI 글 쓰는 중…\(약 60초\)/)).toBeInTheDocument();
    release(base({ method: "graphrag" }));
  });

  it("요약이 있으면 먼저 보이고 전체 소명은 접는다(사용자 2026-10-20 \"너무 길어서 읽을수가 없어\")", async () => {
    vi.mocked(postJustification).mockResolvedValue(base({ method: "graphrag", summary: "요구 기술은 요구 Python 미달[F2]입니다.",
                                                           summary_method: "graphrag", ai_available: false }));
    panel();
    const box = await screen.findByLabelText("소명 요약");
    expect(box.textContent).toContain("핵심 요약 · AI 구성");
    expect(box.textContent).toContain("요구 Python 미달");
    const details = screen.getByText(/전체 소명 보기/).closest("details")!;
    expect(details.open).toBe(false);
  });
});
