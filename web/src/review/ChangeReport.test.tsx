import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChangeReport, countLine, type ChangeReportData } from "./ChangeReport";
import { ChangeReportPanel, periodOf } from "./ChangeReportPanel";
import type { ChangeItem } from "./reviewApi";

const ITEM: ChangeItem = {
  id: "i1", at: "2026-10-22T01:00:00+00:00", kind: "apply_manual", label: "손 편성 확정", note: "J018 손 편성: 김하준", by: "admin",
  dataset_id: "org-n100", version: "v2", status: null, backfilled: false,
  people: { moved: [{ person_id: "P1", name: "김하준", grade: "고급", from: ["J004"], to: ["J018"] }],
            joined: [{ person_id: "P2", name: "한지우", grade: "중급", from: [], to: ["J018"] }], left: [] },
  counts: { moved: 1, joined: 1, left: 0 }, projects: { J004: "데이터 마트", J018: "문서 전환" },
  reasons: ["빼 온 자리 메우지 않음: 데이터 마트(J004) 중급 1자리(정원에서 뺌) — 사유 막바지"],
  review: [{ project_id: "J018", name: "문서 전환", exists: true, status: "draft", objections: 1 }], review_kind: true,
};
const UNDO: ChangeItem = { ...ITEM, id: "i2", kind: "undo", label: "되돌리기", note: "되돌리기: 손 편성 확정", status: null,
                           people: { moved: [], joined: [], left: [] }, counts: { moved: 0, joined: 0, left: 0 }, reasons: ["되돌린 변경: 손 편성 확정 — x"],
                           review: [], review_kind: false };

afterEach(() => vi.restoreAllMocks());

describe("변경 보고서(2026-10-22 사용자 '기간으로 잡고 … 인사팀 보고 사항들을 정리해서 함께 pdf에 묶는것')", () => {
  it("기간: 오늘·최근 7일·이번 달·전체·직접(끝 날 다음 날 0시까지)", () => {
    const now = new Date(2026, 9, 22, 15, 0, 0);
    expect(periodOf("today", "", "", now)).toEqual({ start: new Date(2026, 9, 22).toISOString(), end: new Date(2026, 9, 23).toISOString() });
    expect(periodOf("week", "", "", now).start).toBe(new Date(2026, 9, 16).toISOString());
    expect(periodOf("month", "", "", now).start).toBe(new Date(2026, 9, 1).toISOString());
    expect(periodOf("all", "", "", now)).toEqual({ start: null, end: null });
    expect(periodOf("custom", "2026-10-01", "2026-10-05", now)).toEqual({ start: new Date(2026, 9, 1).toISOString(), end: new Date(2026, 9, 6).toISOString() });
    expect(countLine({ moved: 1, joined: 0, left: 2 })).toBe("옮김 1명 · 빠짐 2명");
  });

  it("목록에서 한 건 또는 고른 것을 한 PDF로 요청한다", async () => {
    const calls: { url: string; body?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, body: init?.body as string | undefined });
      if (url.includes("/api/changes/report")) return new Response(new Blob(["%PDF-"]), { status: 200 });
      return new Response(JSON.stringify({ items: [UNDO, ITEM] }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    globalThis.URL.createObjectURL = vi.fn(() => "blob:x");
    globalThis.URL.revokeObjectURL = vi.fn();
    render(<ChangeReportPanel />);
    await screen.findByText("J018 손 편성: 김하준");
    expect(calls[0].url).toMatch(/\/api\/changes\?start=.*&end=/);                 // 기본 최근 7일
    expect(screen.getByText(/검토 자료 1\/1편 · 이의 1건/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "이 건 PDF" })[1]);
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/changes/report"))).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url.includes("/report"))!.body!).ids).toEqual(["i1"]);
    fireEvent.click(screen.getByLabelText(/되돌리기 .* 고르기/));                     // 되돌리기 빼고 묶기
    fireEvent.click(screen.getByRole("button", { name: "고른 1건 한 PDF로" }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/report"))).toHaveLength(2));
    expect(JSON.parse(calls.filter((c) => c.url.includes("/report"))[1].body!).ids).toEqual(["i1"]);
    fireEvent.click(screen.getByRole("button", { name: "전체" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api/changes?"))).toBe(true));
  });

  it("PDF 화면: 표지 요약·목차, 변경마다 사람 표·사유, 검토 자료 전문 또는 '만들지 않음'", () => {
    const data: ChangeReportData = {
      kind: "changes", generated_at: "2026-10-22T03:00:00+00:00", period: { start: "2026-10-15T15:00:00.000Z", end: "2026-10-22T15:00:00.000Z" },
      items: [{ ...ITEM, review_docs: [{ project_id: "J018", name: "문서 전환", doc: null }] },
              { ...UNDO, status: null, review_docs: [] }],
      totals: { changes: 2, by_kind: { "손 편성 확정": 1, "되돌리기": 1 }, moved: 1, joined: 1, left: 0, review_docs: 1, review_missing: 1,
                objections: 0, undone_or_cancelled: 0 },
    };
    render(<ChangeReport data={data} />);
    expect(screen.getByRole("heading", { name: "인력 배치 변경 보고서" })).toBeInTheDocument();
    expect(screen.getByText("손 편성 확정 1건 · 되돌리기 1건")).toBeInTheDocument();
    expect(screen.getByText(/옮김 1명 · 새로 1명 · 빠짐 0명/)).toBeInTheDocument();
    expect(screen.getByText(/1편 \(만들지 않음 1편\)/)).toBeInTheDocument();
    expect(screen.getByText("김하준 (고급)")).toBeInTheDocument();
    expect(screen.getByText("J004 데이터 마트")).toBeInTheDocument();
    expect(screen.getByText(/사유 막바지/)).toBeInTheDocument();
    expect(screen.getByText(/검토 자료를 만들지 않았습니다/)).toBeInTheDocument();
    expect(screen.getByText(/NOT_CALIBRATED/)).toBeInTheDocument();
  });
});
