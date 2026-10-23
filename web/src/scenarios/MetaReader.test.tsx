import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("./projectDocsApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./projectDocsApi")>();
  return { ...real, readProjectMeta: vi.fn() };
});
vi.mock("../api/client", () => ({ API_BASE: "" }));

import * as docsApi from "./projectDocsApi";
import { MetaReader, proposalsOf } from "./MetaReader";
import { emptyForm } from "./formRules";
import type { MetaResult } from "./projectDocsApi";

const hwp = (name = "입찰공고문.hwp") => new File([new Uint8Array(10)], name);
const f = <T,>(value: T, quote: string, source: "ai" | "rule" = "ai", extra = {}) => ({ value, quote, start: 0, end: quote.length, source, ...extra });
const RESULT: MetaResult = {
  fields: {
    name: f("생성형 AI 기반 산업안전보건 비정형 데이터 정제·구조화 및 활용체계 구축 연구 용역", "입찰건명 : 생성형 AI …"),
    client: f("한국산업안전보건공단", "한국산업안전보건공단 계약담당자"),
    sector: f("대외공공", "한국산업안전보건공단"),
    duration_months: f(7, "계약체결일로부터 ~ 2026년 11월 13일까지"),
    period: f("계약체결일로부터 ~ 2026년 11월 13일까지", "계약체결일로부터 ~ 2026년 11월 13일까지"),
    budget: f(100_000_000, "사업예산(부가가치세 포함) : 100,000,000원", "ai", { vat_included: true }),
    notice_no: f("산안공고 제2026 - 68호", "입찰공고번호 : 산안공고 제2026 - 68호", "rule"),
    deadline: f("2026/04/28(화) 10:00", "제출 마감일시 : 2026/04/28(화) 10:00"),
  },
  method: "ai", model: "gpt-6-luna", warnings: [], fallback_reason: null, ai_available: true, usage: { latency_s: 6.2 }, chars: 11178,
};

beforeEach(() => { vi.clearAllMocks(); });

describe("계산 도우미", () => {
  it("월 예산 = 총액 ÷ 개월 ÷ 1만 원(1점 = 1만 원), 원 표기, 끝 달", () => {
    expect(docsApi.monthlyPoints(100_000_000, 6)).toBe(1667);
    expect(docsApi.monthlyPoints(100_000_000, 7)).toBe(1429);
    expect(docsApi.wonText(100_000_000)).toBe("1억 원");
    expect(docsApi.wonText(120_000_000)).toBe("1억 2,000만 원");
    expect(docsApi.wonText(95_000_000)).toBe("9,500만 원");
    expect(docsApi.endMonth("2026-12", 7)).toBe("2027-06");
    expect(docsApi.endMonth("2026-10", 1)).toBe("2026-10");
    expect(docsApi.wonText(199_995_000)).toBe("2억 원");                                            // 폴백 리뷰 NIT 6
  });

  it("제안: 기간은 지금 시작 달 기준, 이미 적은 칸은 already", () => {
    const base = { ...emptyForm("2026-10"), start_month: "2026-12", end_month: "2026-12" };
    const p = proposalsOf(RESULT, base, emptyForm("2026-10"));
    expect(p.map((x) => [x.key, x.shown, x.already])).toEqual([
      ["name", RESULT.fields.name!.value, false], ["sector", "대외공공", false],
      ["end_month", "7개월 → 2026-12 ~ 2027-06", false], ["monthly_budget", "1,429점", false]]);
    expect(p.find((x) => x.key === "monthly_budget")!.note).toContain("총 1억 원(부가세 포함) ÷ 7개월 = 월 1,429만 원 → 1,429점 (1점 = 1만 원 가정");
    const typed = { ...base, name: "내가 적은 이름", sector: "대내" as const, end_month: "2027-03", monthly_budget: 3000 };
    expect(proposalsOf(RESULT, typed, emptyForm("2026-10")).every((x) => x.already)).toBe(true);
    // 기간을 모르면 월 예산도 제안하지 않는다(총액만 참고)
    const noMonths = { ...RESULT, fields: { ...RESULT.fields, duration_months: null } };
    expect(proposalsOf(noMonths, base, emptyForm("2026-10")).map((x) => x.key)).toEqual(["name", "sector"]);
  });
});

describe("등록 양식 ① 자료에서 사업 정보 채우기(2026-10-23 사용자 \"프로젝트 이름, 기간, 예산 등을 대신 작성 … 사용자가 보고 고칠수 있도록\")", () => {
  it("읽으면 칸 제안·근거·참고 정보를 보이고, 고른 칸만 넣는다", async () => {
    vi.mocked(docsApi.readProjectMeta).mockResolvedValue(RESULT);
    const onApply = vi.fn();
    const form = { ...emptyForm("2026-10"), start_month: "2026-12", end_month: "2026-12" };
    render(<MetaReader docs={[{ file: hwp("제안요청서.hwp"), kind: "제안요청서(RFP)" }, { file: hwp(), kind: "입찰공고문" }]}
                       destination="external" form={form} defaults={emptyForm("2026-10")} onApply={onApply} />);
    expect((screen.getByLabelText("정보를 읽을 자료") as HTMLSelectElement).value).toBe("입찰공고문.hwp");   // 공고문 먼저
    expect(screen.getByText(/회사 밖 AI\(OpenAI 등\)로 갑니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "읽기" }));
    const list = await screen.findByRole("list", { name: "자료에서 읽은 사업 정보" });
    for (const label of ["사업명 넣기", "고객 구분 넣기", "기간 넣기", "월 예산 넣기"]) expect(within(list).getByRole("checkbox", { name: label })).toBeChecked();
    expect(within(list).getByText("근거: “사업예산(부가가치세 포함) : 100,000,000원”")).toBeInTheDocument();
    const info = screen.getByLabelText("참고 정보");
    expect(info.textContent).toContain("산안공고 제2026 - 68호");
    expect(info.textContent).toContain("2026/04/28(화) 10:00");
    expect(screen.getByText(/AI가 읽었습니다\(gpt-6-luna · 6\.2초\)/)).toBeInTheDocument();
    fireEvent.click(within(list).getByRole("checkbox", { name: "고객 구분 넣기" }));
    fireEvent.click(screen.getByRole("button", { name: "고른 3개 칸에 넣기" }));
    expect(onApply).toHaveBeenCalledWith({ name: RESULT.fields.name!.value, end_month: "2027-06", monthly_budget: 1429 });
  });

  it("AI 없이 규칙으로 읽은 결과·기간을 모를 때·읽을 수 없는 자료", async () => {
    vi.mocked(docsApi.readProjectMeta).mockResolvedValue({ ...RESULT, method: "rule", model: null, fallback_reason: "no_client", ai_available: false,
      fields: { ...RESULT.fields, duration_months: null, sector: null } });
    const { rerender } = render(<MetaReader docs={[{ file: hwp(), kind: "입찰공고문" }]} form={emptyForm("2026-10")} defaults={emptyForm("2026-10")} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "입찰공고문.hwp에서 읽기" }));
    expect(await screen.findByText(/AI 키가 없어 AI 없이 자료의 정해진 말로 찾았습니다/)).toBeInTheDocument();
    expect(screen.getByText(/기간 개월 수를 읽지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByLabelText("참고 정보").textContent).toContain("1억 원 — 기간 개월을 몰라 월 예산은 직접 적습니다");
    rerender(<MetaReader docs={[{ file: new File(["x"], "묶음.zip"), kind: "기타" }]} form={emptyForm("2026-10")} defaults={emptyForm("2026-10")} onApply={vi.fn()} />);
    expect(screen.queryByLabelText("자료에서 사업 정보 채우기")).toBeNull();
  });
});

describe("서버가 확인하지 못한 값은 기본 체크하지 않는다(폴백 리뷰 S1·S6)", () => {
  it("AI가 날짜로 센 기간·그 기간으로 나눈 월 예산·규칙으로만 찾은 사업명은 \"확인 필요\"", async () => {
    const r: MetaResult = { ...RESULT, fields: { ...RESULT.fields,
      duration_months: f(7, "계약체결일로부터 ~ 2026년 11월 13일까지", "ai", { computed: true }),
      name: f("가상 사업", "사업명 : 가상 사업", "rule") } };
    vi.mocked(docsApi.readProjectMeta).mockResolvedValue(r);
    const form = { ...emptyForm("2026-10"), start_month: "2026-12", end_month: "2026-12" };
    render(<MetaReader docs={[{ file: hwp(), kind: "입찰공고문" }]} destination="onprem" form={form} defaults={emptyForm("2026-10")} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "입찰공고문.hwp에서 읽기" }));
    const list = await screen.findByRole("list", { name: "자료에서 읽은 사업 정보" });
    expect(within(list).getByRole("checkbox", { name: "고객 구분 넣기" })).toBeChecked();
    for (const label of ["사업명 넣기", "기간 넣기", "월 예산 넣기"]) expect(within(list).getByRole("checkbox", { name: label })).not.toBeChecked();
    expect(within(list).getAllByText("확인 필요 — 맞으면 체크합니다")).toHaveLength(3);
    expect(within(list).getByText("AI 계산")).toBeInTheDocument();
    expect(within(list).getByText("규칙")).toBeInTheDocument();
    expect(list.textContent).toContain("자료에 개월 수가 없어 AI가 날짜로 셌습니다");
  });

  it("근거 말에 사업명 표지가 없는 사업명은 확인 필요", () => {
    const r: MetaResult = { ...RESULT, fields: { ...RESULT.fields, name: f("비정형 데이터 기반 AI 대전환", "비정형 데이터 기반 AI 대전환", "ai", { unlabeled: true }) } };
    const p = proposalsOf(r, emptyForm("2026-10"), emptyForm("2026-10")).find((x) => x.key === "name")!;
    expect(p.suggest).toBe(false);
    expect(p.note).toContain("사업명 표지");
  });

  it("읽을 자료를 바꾸면 옛 결과를 지운다", async () => {
    vi.mocked(docsApi.readProjectMeta).mockResolvedValue(RESULT);
    render(<MetaReader docs={[{ file: hwp(), kind: "입찰공고문" }, { file: hwp("제안요청서.hwp"), kind: "제안요청서(RFP)" }]}
                       form={emptyForm("2026-10")} defaults={emptyForm("2026-10")} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "읽기" }));
    await screen.findByRole("list", { name: "자료에서 읽은 사업 정보" });
    fireEvent.change(screen.getByLabelText("정보를 읽을 자료"), { target: { value: "제안요청서.hwp" } });
    expect(screen.queryByRole("list", { name: "자료에서 읽은 사업 정보" })).toBeNull();
  });
});
