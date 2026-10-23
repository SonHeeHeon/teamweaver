import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("./projectDocsApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./projectDocsApi")>();
  return { ...real, fetchDocSummary: vi.fn(), makeDocSummary: vi.fn() };
});
vi.mock("../api/client", () => ({ API_BASE: "" }));

import * as docsApi from "./projectDocsApi";
import { DocBriefDialog, localTime } from "./DocBrief";
import type { DocSummary, ProjectDoc, SummaryResponse } from "./projectDocsApi";

const doc = (over: Partial<ProjectDoc>): ProjectDoc => ({
  id: "n1", project_id: "J021", kind: "입찰공고문", filename: "입찰공고문.hwp", size: 400_000, uploaded_at: null, demo: false, ...over,
});
const SUM: DocSummary = {
  doc_type: "입찰공고문", relevance: "high", one_line: "안전보건공단 생성형 AI 데이터 구조화 연구 용역 공고",
  sections: [
    { key: "overview", title: "사업 개요", items: [] },
    { key: "budget", title: "예산·계약", items: [{ text: "사업예산은 부가세 포함 1억 원이다.", quote: "사업예산(부가가치세 포함) : 100,000,000원" }] },
    { key: "evaluation", title: "평가 방식", items: [{ text: "기술 70점·가격 30점으로 평가한다.", quote: "제안서 기술평가(70점)와 가격평가(30점)" }] },
  ],
  method: "ai", model: "gpt-6-luna", warnings: [], fallback_reason: null, usage: { latency_s: 9.1 }, dropped: [{ section: "budget", why: "근거 말이 자료에 없다" }], chars: 11178,
};
const resp = (over: Partial<SummaryResponse>): SummaryResponse => ({
  doc: doc({}), readable: true, summary: null, created_at: null, ai_destination: "external", ...over,
});

beforeEach(() => { vi.clearAllMocks(); });

describe("부서장용 자료 요약 창(2026-10-23 사용자 \"각각의 자료를 llm으로 잘 압축 요약한 … 팝업\")", () => {
  it("요약이 없으면 보내는 곳을 알리고 눌러서 만든다 → 칸·근거·검사에서 뺀 개수를 보인다", async () => {
    vi.mocked(docsApi.fetchDocSummary).mockResolvedValue(resp({}));
    vi.mocked(docsApi.makeDocSummary).mockResolvedValue(resp({ summary: SUM, created_at: "2026-10-23T11:00:00+00:00" }));
    render(<DocBriefDialog projectName="산업안전 비정형 데이터 구조화" docs={[doc({})]} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "자료 요약 — 산업안전 비정형 데이터 구조화" });
    expect(await within(dialog).findByText(/회사 밖 AI\(OpenAI 등\)로 갑니다/)).toBeInTheDocument();
    expect(docsApi.makeDocSummary).not.toHaveBeenCalled();                          // 열기만으로는 만들지 않는다
    fireEvent.click(within(dialog).getByRole("button", { name: "AI로 요약 만들기" }));
    await waitFor(() => expect(docsApi.makeDocSummary).toHaveBeenCalledWith("J021", "n1", expect.any(AbortSignal)));
    expect(await within(dialog).findByText("안전보건공단 생성형 AI 데이터 구조화 연구 용역 공고")).toBeInTheDocument();
    expect(within(dialog).queryByRole("region", { name: "사업 개요" })).toBeNull();     // 빈 칸은 숨긴다
    const budget = within(dialog).getByRole("region", { name: "예산·계약" });
    expect(budget.textContent).toContain("사업예산은 부가세 포함 1억 원이다.");
    expect(budget.textContent).toContain("사업예산(부가가치세 포함) : 100,000,000원");
    expect(dialog.textContent).toContain("근거·숫자를 확인하지 못한 항목 1개는 뺐습니다");
    expect(dialog.textContent).toContain("AI 요약(gpt-6-luna · 9.1초)");
  });

  it("저장된 요약은 바로 보이고, 탭을 바꾸면 그 자료를 불러온다 · 덜 중요한 자료와 못 읽는 형식", async () => {
    vi.mocked(docsApi.fetchDocSummary).mockImplementation(async (_p, id) =>
      id === "n1" ? resp({ summary: SUM, created_at: "2026-10-23T11:00:00+00:00" })
        : id === "c1" ? resp({ doc: doc({ id: "c1", kind: "기타" }), summary: { ...SUM, doc_type: "청렴계약 특수조건", relevance: "low", one_line: "" } })
          : resp({ doc: doc({ id: "z1", kind: "기타", filename: "묶음.zip" }), readable: false }));
    render(<DocBriefDialog projectName="P" docs={[doc({}), doc({ id: "c1", kind: "기타", filename: "청렴계약.hwp" }),
                                                  doc({ id: "z1", kind: "기타", filename: "묶음.zip" })]} onClose={vi.fn()} />);
    expect(await screen.findByText("안전보건공단 생성형 AI 데이터 구조화 연구 용역 공고")).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`저장 ${localTime("2026-10-23T11:00:00+00:00")}`))).toBeInTheDocument();   // 이 컴퓨터 시각으로
    fireEvent.click(screen.getByRole("tab", { name: /청렴계약/ }));
    expect(await screen.findByText("부서장이 볼 내용이 적은 자료")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /묶음\.zip/ }));
    expect(await screen.findByText(/ZIP 묶음\)은 요약할 수 없습니다/)).toBeInTheDocument();
    expect(docsApi.fetchDocSummary).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole("tab", { name: /입찰공고문/ }));
    expect(docsApi.fetchDocSummary).toHaveBeenCalledTimes(3);                         // 이미 불러온 탭은 다시 부르지 않는다
  });
});

describe("탭·멈추기 경합(폴백 리뷰 S8)", () => {
  it("불러오는 중에 탭을 바꿨다 돌아와도 끝난 결과가 보인다(예전엔 \"불러오는 중\"에 멈췄다)", async () => {
    let release!: (v: SummaryResponse) => void;
    vi.mocked(docsApi.fetchDocSummary).mockImplementation((_p, id) =>
      id === "n1" ? new Promise((r) => { release = r; }) : Promise.resolve(resp({ doc: doc({ id: "r1" }) })));
    render(<DocBriefDialog projectName="P" docs={[doc({}), doc({ id: "r1", kind: "제안요청서(RFP)", filename: "RFP.hwp" })]} onClose={vi.fn()} />);
    expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /RFP\.hwp/ }));
    expect(await screen.findByRole("button", { name: "AI로 요약 만들기" })).toBeInTheDocument();
    release(resp({ summary: SUM, created_at: "2026-10-23T11:00:00+00:00" }));
    fireEvent.click(screen.getByRole("tab", { name: /입찰공고문\.hwp/ }));
    expect(await screen.findByText("안전보건공단 생성형 AI 데이터 구조화 연구 용역 공고")).toBeInTheDocument();
  });

  it("A를 만드는 중에 B를 만들어도 A가 끝까지 보이고, 멈추기는 보고 있는 탭만 끊는다", async () => {
    vi.mocked(docsApi.fetchDocSummary).mockImplementation(async (_p, id) => resp({ doc: doc({ id }) }));
    const pending: Record<string, { resolve: (v: SummaryResponse) => void; signal: AbortSignal }> = {};
    vi.mocked(docsApi.makeDocSummary).mockImplementation((_p, id, signal) => new Promise((resolve) => { pending[id] = { resolve, signal: signal! }; }));
    render(<DocBriefDialog projectName="P" docs={[doc({}), doc({ id: "r1", kind: "제안요청서(RFP)", filename: "RFP.hwp" })]} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "AI로 요약 만들기" }));                 // A(n1)
    fireEvent.click(screen.getByRole("tab", { name: /RFP\.hwp/ }));
    fireEvent.click(await screen.findByRole("button", { name: "AI로 요약 만들기" }));                 // B(r1)
    expect(pending.n1.signal.aborted).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "멈추기" }));                                   // B만 끊는다
    expect(pending.r1.signal.aborted).toBe(true) ; expect(pending.n1.signal.aborted).toBe(false);
    expect(screen.getByRole("button", { name: "AI로 요약 만들기" })).toBeInTheDocument();
    pending.n1.resolve(resp({ summary: SUM, created_at: "2026-10-23T11:00:00+00:00" }));
    fireEvent.click(screen.getByRole("tab", { name: /입찰공고문\.hwp/ }));
    expect(await screen.findByText("안전보건공단 생성형 AI 데이터 구조화 연구 용역 공고")).toBeInTheDocument();
  });

  it("멈춘 뒤·불러오기 실패 뒤에는 다시 불러올 수 있고, AI가 없으면 버튼 이름이 다르다", async () => {
    let n = 0;
    vi.mocked(docsApi.fetchDocSummary).mockImplementation(async () => {
      n += 1;
      if (n === 1) throw new Error("잠시 실패");
      return n === 2 ? resp({ ai_destination: "none" }) : resp({ summary: SUM, created_at: "2026-10-23T11:00:00+00:00" });
    });
    vi.mocked(docsApi.makeDocSummary).mockImplementation(() => new Promise(() => {}));
    render(<DocBriefDialog projectName="P" docs={[doc({})]} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("잠시 실패");
    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));
    fireEvent.click(await screen.findByRole("button", { name: "요약 만들기(AI 없이)" }));
    fireEvent.click(screen.getByRole("button", { name: "멈추기" }));
    expect(screen.getByText(/서버가 이미 요약을 만들어 저장했을 수 있습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));
    expect(await screen.findByText("안전보건공단 생성형 AI 데이터 구조화 연구 용역 공고")).toBeInTheDocument();
  });

  it("탭은 좌우 화살표로 옮긴다", async () => {
    vi.mocked(docsApi.fetchDocSummary).mockImplementation(async (_p, id) => resp({ doc: doc({ id }) }));
    render(<DocBriefDialog projectName="P" docs={[doc({}), doc({ id: "r1", kind: "제안요청서(RFP)", filename: "RFP.hwp" })]} onClose={vi.fn()} />);
    const first = screen.getByRole("tab", { name: /입찰공고문\.hwp/ });
    expect(first).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: /RFP\.hwp/ })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(docsApi.fetchDocSummary).toHaveBeenCalledTimes(2));
  });
});

describe("localTime", () => {
  it("UTC ISO를 이 컴퓨터 시각으로, 못 읽으면 앞 16자", () => {
    const d = new Date("2026-10-23T10:33:00+00:00");
    expect(localTime("2026-10-23T10:33:00+00:00")).toBe(
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:33`);
    expect(localTime("nope-not-a-date")).toBe("nope-not-a-date");
  });
});
