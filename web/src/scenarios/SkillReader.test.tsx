import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("./projectDocsApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./projectDocsApi")>();
  return { ...real, readRequirements: vi.fn() };
});
vi.mock("./workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./workspaceApi")>();
  return { ...real, fetchSkillDictionary: vi.fn() };
});
vi.mock("../api/client", () => ({ API_BASE: "" }));

import * as docsApi from "./projectDocsApi";
import * as api from "./workspaceApi";
import { SkillReader } from "./SkillReader";
import { ProjectForm } from "./ProjectForm";
import type { ReadResult } from "./projectDocsApi";

const pdf = (name = "RFP.pdf") => new File([new Uint8Array(100)], name, { type: "application/pdf" });
const RESULT: ReadResult = {
  skills: [
    { concept_id: "etl", name: "ETL", months: 60, headcount: 2, quote: "ETL 경력 5년 이상 2명", start: 0, end: 10, kind: "required" },
    { concept_id: "kafka", name: "Kafka", months: null, headcount: null, quote: "Kafka 경험자 우대", start: 20, end: 30, kind: "preferred" },
    { concept_id: "java", name: "Java", months: 96, headcount: 1, quote: "Java 8년 이상 1명", start: 40, end: 50, kind: "required" },
    { concept_id: "sec", name: "정보보안", months: null, headcount: null, quote: "이전 뒤 성능·보안 점검", start: 60, end: 70, kind: "required" },
  ],
  mentioned_not_required: [{ concept_id: "oracle", name: "Oracle", quote: "현재 Oracle로 운영 중" }],
  unmapped_terms: ["전자정부 표준프레임워크"], method: "rule", model: null, warnings: [], fallback_reason: "no_client",
  ai_available: false, usage: null, chars: 900,
};

beforeEach(() => { vi.clearAllMocks(); });

describe("자료에서 필요 기술 읽기(2026-10-23 사용자 \"자료를 넣으면 llm … 필요 기술에 대해 정의해서 등록해주고 사용자가 보고 … 수정 및 삭제\")", () => {
  it("자료가 없거나 글자를 못 읽는 형식이면 안내만 한다", () => {
    const { rerender } = render(<SkillReader docs={[]} current={[]} canonicalOf={(n) => n} onApply={vi.fn()} />);
    expect(screen.getByText(/① 기본에서 발주 자료/)).toBeInTheDocument();
    rerender(<SkillReader docs={[{ file: new File(["x"], "과업.hwp"), kind: "과업지시서" }]} current={[]}
                          canonicalOf={(n) => n} onApply={vi.fn()} />);
    expect(screen.getByText(/PDF·HWPX로 저장해 올리면/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("필수만 기본 체크하고, 이미 적은 기술(별칭)은 비교해 보이며, 고른 것만 넣는다", async () => {
    vi.mocked(docsApi.readRequirements).mockResolvedValue(RESULT);
    const onApply = vi.fn();
    render(<SkillReader docs={[{ file: pdf(), kind: "제안요청서(RFP)" }]} current={[{ name: "자바", min_months: 24, headcount: 1 }]}
                        canonicalOf={(n) => (n === "자바" ? "Java" : n)} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: "RFP.pdf 읽기" }));
    await waitFor(() => expect(docsApi.readRequirements).toHaveBeenCalledWith(expect.any(File), expect.any(AbortSignal)));   // 판 번호 없이(S3)
    const list = await screen.findByRole("list", { name: "읽은 필요 기술" });
    expect(within(list).getByRole("checkbox", { name: "ETL 넣기" })).toBeChecked();
    expect(within(list).getByRole("checkbox", { name: "Kafka 넣기" })).not.toBeChecked();      // 우대
    expect(within(list).getByRole("checkbox", { name: "Java 넣기" })).not.toBeChecked();       // 이미 적음(자바)
    expect(within(list).getByRole("checkbox", { name: "정보보안 넣기" })).not.toBeChecked();   // 경력·인원 없는 필수 = 과업 범위에서 추정
    expect(within(list).getByText("추정(경력·인원 언급 없음)")).toBeInTheDocument();
    expect(within(list).getByText(/이미 적음: 2년·1명/)).toBeInTheDocument();
    expect(list.textContent).toContain("경력 미기재(넣을 값 2년) · 인원 미기재(넣을 값 1명)");
    expect(within(list).getByText("근거: “ETL 경력 5년 이상 2명”")).toBeInTheDocument();
    expect(screen.getByText(/AI 키가 없어 AI 없이 사전의 기술 이름으로 찾았습니다/)).toBeInTheDocument();
    expect(screen.getByText("자료에 나오지만 요구로 보지 않음: Oracle")).toBeInTheDocument();
    expect(screen.getByText(/사전에 없는 말: 전자정부 표준프레임워크/)).toBeInTheDocument();
    fireEvent.click(within(list).getByRole("checkbox", { name: "Java 넣기" }));
    fireEvent.click(screen.getByRole("button", { name: "고른 2개 필요 기술에 넣기" }));
    expect(onApply).toHaveBeenCalledWith([{ name: "ETL", min_months: 60, headcount: 2 }, { name: "자바", min_months: 96, headcount: 1 }]);
  });

  it("읽는 동안 멈출 수 있고, 오류는 보인다", async () => {
    vi.mocked(docsApi.readRequirements).mockReturnValueOnce(new Promise(() => {}) as never);
    render(<SkillReader docs={[{ file: pdf(), kind: "사전규격" }]} current={[]} canonicalOf={(n) => n} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "RFP.pdf 읽기" }));
    expect(screen.getByText(/읽는 중/)).toBeInTheDocument();
    const signal = vi.mocked(docsApi.readRequirements).mock.calls[0][1]!;
    fireEvent.click(screen.getByRole("button", { name: "멈추기" }));
    expect(signal.aborted).toBe(true);
    expect(screen.getByRole("button", { name: "RFP.pdf 읽기" })).toBeInTheDocument();
    vi.mocked(docsApi.readRequirements).mockRejectedValueOnce(new Error("이 형식은 글자를 읽지 못한다"));
    fireEvent.click(screen.getByRole("button", { name: "RFP.pdf 읽기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("이 형식은 글자를 읽지 못한다");
  });

  it("등록 양식 ④에서 읽은 기술을 넣으면 목록에 합쳐지고, 1·2·3·5년 밖의 경력(8년)도 보인다", async () => {
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([{ name: "Java", aliases: ["자바"] }, { name: "ETL", aliases: [] }]);
    vi.mocked(docsApi.readRequirements).mockResolvedValue(RESULT);
    render(<ProjectForm start="2026-10" predecessors={[]} onSubmit={vi.fn()} onCancel={vi.fn()} datasetVersion="v1" />);
    fireEvent.change(screen.getByLabelText("사업명"), { target: { value: "보험사 D" } });
    fireEvent.change(screen.getByLabelText("발주 자료 파일"), { target: { files: [pdf()] } });
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("기술 검색"), { target: { value: "자바" } });
    fireEvent.click(await screen.findByRole("button", { name: "+ Java" }));     // 별칭으로 찾아 사전 대표 이름(Java)으로 들어간다
    fireEvent.click(screen.getByRole("button", { name: "RFP.pdf 읽기" }));
    const list = await screen.findByRole("list", { name: "읽은 필요 기술" });
    expect(within(list).getByRole("checkbox", { name: "Java 넣기" })).not.toBeChecked();
    fireEvent.click(within(list).getByRole("checkbox", { name: "Java 넣기" }));
    fireEvent.click(screen.getByRole("button", { name: "고른 2개 필요 기술에 넣기" }));
    const skills = screen.getByRole("list", { name: "필요 기술" });
    expect(within(skills).getAllByRole("listitem").map((li) => li.querySelector("b")?.textContent)).toEqual(["Java", "ETL"]);
    const java = within(skills).getAllByRole("listitem")[0];
    expect(within(java).getByRole("button", { name: "8년" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("ETL 빼기")).toBeInTheDocument();                  // 넣은 뒤엔 기존 칸에서 빼고 고친다
  });
});

describe("보내는 곳 안내·화면 이탈(폴백 리뷰 S2·NIT)", () => {
  it("읽기 전에 자료 글자가 갈 곳을 알린다(회사 밖이면 비밀유지 자료 경고)", () => {
    const docs = [{ file: pdf(), kind: "제안요청서(RFP)" as const }];
    const { rerender } = render(<SkillReader docs={docs} destination="external" current={[]} canonicalOf={(n) => n} onApply={vi.fn()} />);
    expect(screen.getByText(/회사 밖 AI\(OpenAI 등\)로 갑니다 — 비밀유지 자료/)).toBeInTheDocument();
    rerender(<SkillReader docs={docs} destination="onprem" current={[]} canonicalOf={(n) => n} onApply={vi.fn()} />);
    expect(screen.getByText(/사내 AI로 갑니다/)).toBeInTheDocument();
    rerender(<SkillReader docs={docs} destination="none" current={[]} canonicalOf={(n) => n} onApply={vi.fn()} />);
    expect(screen.getByText(/AI를 쓰지 않고/)).toBeInTheDocument();
    rerender(<SkillReader docs={docs} destination={null} current={[]} canonicalOf={(n) => n} onApply={vi.fn()} />);
    expect(screen.getByText(/회사 밖 AI\(OpenAI 등\)로 갑니다/)).toBeInTheDocument();       // 모르면 경고 쪽으로
  });

  it("단계를 떠나면(언마운트) 기다리던 읽기를 끊고, 자료를 바꾸면 이름으로 고른 파일을 읽는다", () => {
    vi.mocked(docsApi.readRequirements).mockReturnValue(new Promise(() => {}) as never);
    const two = [{ file: pdf("a.pdf"), kind: "사전규격" as const }, { file: pdf("b.pdf"), kind: "제안요청서(RFP)" as const }];
    const { unmount } = render(<SkillReader docs={two} current={[]} canonicalOf={(n) => n} onApply={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("읽을 자료"), { target: { value: "b.pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "읽기" }));
    const [file, signal] = vi.mocked(docsApi.readRequirements).mock.calls[0];
    expect(file.name).toBe("b.pdf");
    unmount();
    expect(signal!.aborted).toBe(true);
  });
});

