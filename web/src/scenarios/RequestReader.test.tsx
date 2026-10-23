import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("./workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./workspaceApi")>();
  return { ...real, postEmergencyParse: vi.fn() };
});
import * as api from "./workspaceApi";
import { RequestReader } from "./RequestReader";

const TEXT = "운영 이관 중 장애 — k8s랑 배치 데이터 적재 아는 사람 두 명, 30대 남자, 고급 이상";
const PARSED = {
  text: TEXT, headcount: 2, grade: "고급" as const, grade_at_least: true, unmapped_terms: ["운영 이관"], question: null,
  warnings: ["사람 특성 말(30대, 남자)은 요구로 쓰지 않는다 -- 기술·인원·등급만 본다."], method: "ai" as const, fallback_reason: null,
  ai_available: true, usage: { latency_s: 3.2, cost_usd: 0.001 },
  skills: [{ id: "kubernetes", name: "Kubernetes", quote: "k8s", start: 13, end: 16, kind: "explicit" as const, source: "alias" as const },
           { id: "etl", name: "ETL", quote: "배치 데이터 적재", start: 18, end: 27, kind: "implied" as const, source: "ai" as const }],
};

beforeEach(() => vi.clearAllMocks());

describe("긴급 투입 상황을 말로(2026-10-22)", () => {
  it("읽은 요구를 근거와 함께 보이고, 미루어 본 기술은 꺼 둔 채 고른 것만 추천 요청에 넘긴다", async () => {
    vi.mocked(api.postEmergencyParse).mockResolvedValue(PARSED);
    const onApply = vi.fn();
    render(<RequestReader datasetVersion="v1" text={TEXT} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: "AI로 요구 읽기" }));
    expect(await screen.findByRole("region", { name: "AI가 읽은 요구" })).toBeInTheDocument();
    expect(vi.mocked(api.postEmergencyParse).mock.calls[0].slice(0, 2)).toEqual(["v1", TEXT]);
    expect(screen.getByRole("checkbox", { name: /Kubernetes/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /ETL/ })).not.toBeChecked();         // 미루어 본 기술은 부서장이 켠다
    expect(screen.getByText(/사람 특성 말/)).toBeInTheDocument();
    expect(screen.getByText(/사전에 없는 말: 운영 이관/)).toBeInTheDocument();
    expect(screen.getByText("k8s").tagName).toBe("MARK");                               // 원문 근거 밑줄
    fireEvent.click(screen.getByRole("button", { name: "이대로 추천 받기" }));
    expect(onApply).toHaveBeenLastCalledWith({ skills: ["Kubernetes"], n: 2, grade: "고급" });
    fireEvent.click(screen.getByRole("checkbox", { name: /ETL/ }));
    fireEvent.click(screen.getByRole("button", { name: "이대로 추천 받기" }));
    expect(onApply).toHaveBeenLastCalledWith({ skills: ["Kubernetes", "ETL"], n: 2, grade: "고급" });
  });

  it("같은 기술이 글에 두 번 나오면 밑줄은 둘, 칩·추천 요청은 하나(2026-10-23 D7 문장)", async () => {
    const text = "AI 에이전트 과업 범위 추가로 인력이 더 필요. AI 에이전트 개발 및 T2SQL 쪽 경험이 있는 사람";
    const agent = { id: "ai_agent", name: "AI 에이전트 프레임워크", quote: "AI 에이전트", kind: "explicit" as const, source: "alias" as const };
    vi.mocked(api.postEmergencyParse).mockResolvedValue({
      ...PARSED, text, headcount: null, grade: null, grade_at_least: false, unmapped_terms: ["T2SQL"], warnings: [],
      skills: [{ ...agent, start: 0, end: 7 }, { ...agent, start: 28, end: 35 },
               { id: "sql", name: "SQL", quote: "T2SQL", start: 41, end: 46, kind: "implied" as const, source: "ai" as const }] });
    const onApply = vi.fn();
    render(<RequestReader datasetVersion="v1" text={text} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: "AI로 요구 읽기" }));
    await screen.findByRole("region", { name: "AI가 읽은 요구" });
    expect(screen.getAllByText("AI 에이전트").filter((el) => el.tagName === "MARK")).toHaveLength(2);
    expect(screen.getAllByRole("checkbox", { name: /AI 에이전트 프레임워크/ })).toHaveLength(1);
    expect(screen.getByText(/사전에 없는 말: T2SQL/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "이대로 추천 받기" }));
    expect(onApply).toHaveBeenLastCalledWith({ skills: ["AI 에이전트 프레임워크"], n: null, grade: null });
  });

  it("AI가 없으면 사전으로 읽었다고 밝힌다", async () => {
    vi.mocked(api.postEmergencyParse).mockResolvedValue({ ...PARSED, method: "rule", fallback_reason: "no_client", usage: null,
                                                          skills: [PARSED.skills[0]] });
    render(<RequestReader datasetVersion="v1" text={TEXT} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "AI로 요구 읽기" }));
    await waitFor(() => expect(screen.getByText(/사전으로 읽은 요구 — AI 키가 없어/)).toBeInTheDocument());
  });
});
