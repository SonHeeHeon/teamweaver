import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReviewDoc, sentences } from "./ReviewDoc";
import type { ReviewDocView } from "./reviewApi";

const LOSS = { key: "k-loss", kind: "MOVE_LOSS", text: "J010 사업: 점수 3.00 → 2.40, 요구 기술 Java 충족 2→1명(필요 2명)",
               adverse: true, owner: "P1", section: "move" as const };
const DOC: ReviewDocView = {
  confirm: { version: "v2", kind: "new", at: "2026-10-19T01:00:00+00:00", note: "손 편성 확정", before_version: "v1" },
  project_id: "J014", project_name: "물류 고도화", revision: 3, created_at: "2026-10-19T01:00:00+00:00",
  facts: [
    { key: "k-t", kind: "MOVE_TARGET", text: "이 확정으로 J014 사업 점수 0.00 → 1.20", adverse: false, owner: null, section: "move" },
    { key: "k-from", kind: "MOVE_FROM", text: "김하준: 결제(J010)에서 옮겨 옴 — 그 사업에서 빠짐", adverse: false, owner: "P1", section: "move" },
    LOSS,
    { key: "k-rev", kind: "REV", text: "오세린이 한지우를 평가: 부정, 항목 일정 관리", adverse: true, owner: "P2", section: "adverse" },
    { key: "k-mem", kind: "MEM", text: "한지우(중급) 투입률 1", adverse: false, owner: "P2", section: "appendix" },
  ],
  moves: { target: "J014", joined: [
    { person_id: "P1", grade: "고급", alloc: 1, origin: "moved", sources: [] },
    { person_id: "P2", grade: "중급", alloc: 1, origin: "bench", sources: [] },
  ] },
  team: ["P1", "P2"], names: { P1: "김하준", P2: "한지우" }, disclaimer: "계산 숫자는 … (NOT_CALIBRATED).",
  body_ai: "J014는 Spring 요구를 채운다. 팀 안 협업 쌍은 없다.", body_ai_method: "graphrag",
  body: "J014는 Spring 요구를 채운다. 김하준은 이전 단계 경험이 있다.",
  notes: [{ key: "gone", type: "objection", text: "옛 사실에 단 이의", orphan: true }],
  judgments: {}, situation_memo: "", status: "draft", exported_at: null, reverted: false, active: true, body_edited: true,
};

describe("ReviewDoc — 인사팀 검토 자료(A 문서형)", () => {
  it("문장 나누기", () => {
    expect(sentences("가. 나! 다?\n라")).toEqual(["가.", "나!", "다?", "라"]);
  });

  it("시스템 사실은 잠겨 있고 불리한 사실이 보이며, AI 원본에 없는 문장에만 '부서장 수정'이 붙는다", () => {
    render(<ReviewDoc doc={DOC} editable={false} />);
    expect(screen.getAllByText("🔒 시스템 사실 · 수정 불가").length).toBeGreaterThan(2);
    expect(screen.getByText(/Java 충족 2→1명/)).toBeInTheDocument();
    expect(screen.getAllByText("부서장 수정")).toHaveLength(1);
    expect(screen.getByText("김하준은 이전 단계 경험이 있다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "저장" })).not.toBeInTheDocument();
    expect(screen.getByText(/근거 사실이 바뀐 해명·이의/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();      // 인쇄: 입력 칸 없음
  });

  it("해명을 달고 판단·합의를 적어 저장하면 편집만 보낸다(사실은 보내지 않는다)", async () => {
    const onSave = vi.fn(async () => {});
    render(<ReviewDoc doc={DOC} editable onSave={onSave} />);
    const card = screen.getByText(/Java 충족 2→1명/).closest("div")!;
    fireEvent.click(within(card).getByRole("button", { name: "해명 달기" }));
    fireEvent.change(within(card).getByLabelText("해명"), { target: { value: "막바지라 한 명이면 된다" } });
    fireEvent.click(within(card).getByRole("button", { name: "해명 추가" }));
    fireEvent.change(screen.getByLabelText("김하준 판단"), { target: { value: "Java 작업이 거의 끝났다" } });
    fireEvent.change(screen.getByLabelText("김하준 합의 상태"), { target: { value: "requested" } });
    expect(screen.getByText(/합의가 아직 요청 중/)).toHaveTextContent("김하준");
    expect(screen.queryByLabelText("한지우 판단")).not.toBeInTheDocument();    // 대기 인력은 판단 칸 없음
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const patch = (onSave.mock.calls[0] as unknown as [any])[0];
    expect(patch).not.toHaveProperty("facts");
    expect(patch.revision).toBe(3);
    expect(patch.notes).toContainEqual({ key: "k-loss", type: "reply", text: "막바지라 한 명이면 된다" });
    expect(patch.judgments.P1).toEqual({ text: "Java 작업이 거의 끝났다", agreement: { status: "requested", memo: "" } });
  });

  it("이의는 사유가 없으면 추가할 수 없다", () => {
    render(<ReviewDoc doc={DOC} editable onSave={vi.fn()} />);
    const block = screen.getByText(/일정 관리/).closest("div")!;
    fireEvent.click(within(block).getByRole("button", { name: "이의 제기" }));
    expect(within(block).getByRole("button", { name: "이의 추가" })).toBeDisabled();
    fireEvent.change(within(block).getByLabelText("이의 사유(필수)"), { target: { value: "2024 과제 경력 누락" } });
    expect(within(block).getByRole("button", { name: "이의 추가" })).toBeEnabled();
  });

  it("긴급 투입은 상황 메모 칸이 있고, 되돌려진 확정은 고칠 수 없다", () => {
    const { rerender } = render(<ReviewDoc doc={{ ...DOC, confirm: { ...DOC.confirm, kind: "emergency" } }} editable onSave={vi.fn()} />);
    expect(screen.getByLabelText("상황 메모")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /투입 사유/ })).toBeInTheDocument();
    rerender(<ReviewDoc doc={{ ...DOC, reverted: true }} editable onSave={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("되돌려졌습니다");
    expect(screen.queryByRole("button", { name: "저장" })).not.toBeInTheDocument();
  });

  it("본문을 고쳐 반영하면 저장 본문이 바뀌고, PDF는 저장 전엔 잠긴다", async () => {
    const onSave = vi.fn(async () => {});
    render(<ReviewDoc doc={DOC} editable onSave={onSave} onPdf={vi.fn()} />);
    expect(screen.getByRole("button", { name: "PDF 내보내기" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "본문 고치기" }));
    fireEvent.change(screen.getByLabelText("본문"), { target: { value: "완전히 새로 쓴 사유." } });
    fireEvent.click(screen.getByRole("button", { name: "본문 반영" }));
    expect(screen.getByRole("button", { name: "PDF 내보내기" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect((onSave.mock.calls[0] as unknown as [any])[0].body).toBe("완전히 새로 쓴 사유.");
  });

  it("AI 초안으로 되돌리면 빈 본문을 보내고, 저장 전에는 초안 버튼이 잠긴다(폴백 리뷰 P2)", async () => {
    const onSave = vi.fn(async () => {});
    render(<ReviewDoc doc={DOC} editable onSave={onSave} onDraft={vi.fn()} />);
    expect(screen.getByRole("button", { name: "AI 초안 다시 만들기" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "본문 고치기" }));
    fireEvent.click(screen.getByRole("button", { name: "AI 초안으로 되돌리기" }));
    expect(screen.getByRole("button", { name: "AI 초안 다시 만들기" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect((onSave.mock.calls[0] as unknown as [any])[0].body).toBe("");
  });
});
