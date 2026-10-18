import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DatasetTab } from "./DatasetTab";
import type { DatasetInfo } from "../api/types";

vi.mock("../api/client", () => ({ uploadDataset: vi.fn(), resetDataset: vi.fn(), rejudgeDataset: vi.fn(),
                                  fetchRecords: vi.fn(async () => []), importRecord: vi.fn(), openRecord: vi.fn(),
                                  deleteRecord: vi.fn(), recordFileUrl: (id: string) => `/api/records/${id}/file`,
                                  AdminLoginRequiredError: class extends Error {} }));
import { fetchRecords, importRecord, openRecord, rejudgeDataset, resetDataset, uploadDataset } from "../api/client";
import type { OpenRecordResult, PlacementRecord } from "../api/types";

const FIXTURE: DatasetInfo = { dataset_id: "fixture-demo-100x20", version: "f".repeat(64),
  source: "fixture", synthetic: true, people: 100, projects: 20,
  activated_at: "2026-10-05T00:00:00+00:00" };

function pick() {
  fireEvent.change(screen.getByLabelText("묶음 zip 파일"),
                   { target: { files: [new File(["PK"], "bundle.zip")] } });
  fireEvent.click(screen.getByRole("button", { name: "검증 후 전환" }));
}

describe("DatasetTab", () => {
  beforeEach(() => { vi.mocked(uploadDataset).mockReset(); vi.mocked(resetDataset).mockReset(); });

  it("지금 쓰는 데이터셋과 가상 여부를 보여 준다", () => {
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    expect(screen.getByText(/fixture-demo-100x20/)).toBeInTheDocument();
    expect(screen.getByText(/가상 데이터/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "기본 데이터로 되돌리기" })).not.toBeInTheDocument();
  });

  it("검증 오류는 파일·행·열과 함께 그대로 보여 주고 전환을 알리지 않는다", async () => {
    vi.mocked(uploadDataset).mockResolvedValue({
      activated: false, detail: "검증 오류가 있어 전환하지 않았다.",
      report: {
        errors: [{ level: "error", file: "people.csv", row: 3, column: "career_grade",
                   message: "허용되지 않는 값" }],
        warnings: [{ level: "warning", file: "projects.csv", row: null, column: null,
                     message: "기간을 잘랐다" }],
        notes: ["숙련도: 대리 레벨"], row_counts: { "people.csv": 12 } } });
    const onSwitched = vi.fn();
    render(<DatasetTab active={FIXTURE} onSwitched={onSwitched} />);
    pick();
    expect(await screen.findByText(/전환하지 않았다/)).toBeInTheDocument();
    expect(screen.getByText("people.csv 3행 [career_grade]")).toBeInTheDocument();
    expect(screen.getByText(/허용되지 않는 값/)).toBeInTheDocument();
    expect(screen.getByText(/기간을 잘랐다/)).toBeInTheDocument();
    expect(screen.getByText("숙련도: 대리 레벨")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(onSwitched).not.toHaveBeenCalled();
  });

  it("zip 자체가 잘못되면(리포트 없음) 이유를 보여 준다", async () => {
    vi.mocked(uploadDataset).mockResolvedValue({ activated: false, detail: "zip 파일이 아니다",
                                                 report: null });
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    pick();
    expect(await screen.findByText(/zip 파일이 아니다/)).toBeInTheDocument();
  });

  it("413 같은 실패는 오류로 알린다", async () => {
    vi.mocked(uploadDataset).mockRejectedValue(new Error("업로드 실패(413): 20MiB까지"));
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    pick();
    expect(await screen.findByRole("alert")).toHaveTextContent("413");
  });

  it("업로드 데이터에서는 기본 데이터로 되돌릴 수 있다", async () => {
    vi.mocked(resetDataset).mockResolvedValue(FIXTURE);
    const onSwitched = vi.fn();
    render(<DatasetTab active={{ ...FIXTURE, source: "upload", synthetic: false }}
                       onSwitched={onSwitched} />);
    expect(screen.getByText(/실데이터/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "기본 데이터로 되돌리기" }));
    await waitFor(() => expect(onSwitched).toHaveBeenCalledWith(FIXTURE));
  });
});


describe("DatasetTab — 저장(K13)", () => {
  it("부팅 때 복원에 실패했으면 이유를 보여 준다", () => {
    render(<DatasetTab active={{ ...FIXTURE, restore_error: "해시가 다르다" }} onSwitched={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("해시가 다르다");
  });

  it("전환은 됐지만 서버 저장에 실패했으면 알린다", async () => {
    vi.mocked(uploadDataset).mockResolvedValue({
      activated: true, dataset: { ...FIXTURE, source: "upload" }, persisted: false,
      persist_error: "서버에 저장하지 못했다(재기동하면 기본 데이터로 돌아간다)",
      report: { errors: [], warnings: [], notes: [], row_counts: {} } });
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    pick();
    expect(await screen.findByText(/재기동하면 기본 데이터로 돌아간다/)).toBeInTheDocument();
  });
});


describe("DatasetTab — 시연 데이터·오류 문구(claude-a 요청)", () => {
  it("시작 데이터 묶음은 '시작 데이터'로, 서버가 준 오류 문장은 그대로 보인다", () => {
    render(<DatasetTab active={{ ...FIXTURE, source: "demo-bundle",
      restore_error: "시연 데이터 묶음을 읽지 못해 기본 데이터로 시작했다: x" } as any} onSwitched={vi.fn()} />);
    expect(screen.getByText(/시작 데이터/)).toBeInTheDocument();
    expect(screen.getByText("시연 데이터 묶음을 읽지 못해 기본 데이터로 시작했다: x")).toBeInTheDocument();
    expect(screen.queryByText(/저장된 업로드 데이터를 복원하지 못해/)).not.toBeInTheDocument();
  });
});



describe("DatasetTab — 평가 사유 LLM 판정(2026-10-06 LLM 통일)", () => {
  it("판정 정보와 다음 업로드의 글이 갈 곳(외부 OpenAI)을 보인다", () => {
    render(<DatasetTab active={{ ...FIXTURE, source: "upload", synthetic: false, review_judge: "llm",
      judge_model: "gpt-6-luna", judge_host: "api.openai.com", judge_location: "openai", judge_external: true,
      judge_endpoint: { host: "api.openai.com", location: "openai", external: true, model: "gpt-6-luna" } }} onSwitched={vi.fn()} />);
    expect(screen.getByText(/평가 글 판정: LLM gpt-6-luna · 외부\(OpenAI\)/)).toBeInTheDocument();
    expect(screen.getByText(/TEAMWEAVER_REVIEW_ALLOW_EXTERNAL=1/)).toBeInTheDocument();
  });

  it("사내 LLM이면 호스트를 보이고 외부 경고는 없다", () => {
    render(<DatasetTab active={{ ...FIXTURE, review_judge: "fixture",
      judge_endpoint: { host: "llm.corp.local", location: "onprem", external: false, model: "qwen" } }} onSwitched={vi.fn()} />);
    expect(screen.getByText(/가상 데이터 생성 때 LLM이 매긴 값/)).toBeInTheDocument();
    expect(screen.getByText("사내 LLM llm.corp.local")).toBeInTheDocument();
    expect(screen.queryByText(/TEAMWEAVER_REVIEW_ALLOW_EXTERNAL/)).not.toBeInTheDocument();
  });

  it("사내로 확인되지 않은 주소를 사내라고 단정하지 않는다", () => {
    render(<DatasetTab active={{ ...FIXTURE, review_judge: "fixture",
      judge_endpoint: { host: "my.openrouter.ai", location: "unknown", external: true, model: "x" } }} onSwitched={vi.fn()} />);
    expect(screen.getByText("사내인지 확인되지 않은 주소 my.openrouter.ai")).toBeInTheDocument();
  });

  it("다시 판정했는데 그대로면(같은 버전) 계산 결과를 비우지 않는다", async () => {
    const still = { ...FIXTURE, source: "upload" as const, review_judge: "items" as const, judge_error: "또 503" };
    vi.mocked(rejudgeDataset).mockResolvedValue(still);
    const onSwitched = vi.fn();
    render(<DatasetTab active={{ ...still, judge_error: "503" }} onSwitched={onSwitched} />);
    fireEvent.click(screen.getByRole("button", { name: "판정 다시 시도" }));
    expect(await screen.findByText("또 503")).toBeInTheDocument();
    expect(onSwitched).not.toHaveBeenCalled();
  });

  it("LLM 판정에 실패했으면 이유와 '판정 다시 시도'를 보이고, 누르면 전환을 알린다", async () => {
    const fixed = { ...FIXTURE, source: "upload" as const, review_judge: "llm" as const, version: "n".repeat(64) };
    vi.mocked(rejudgeDataset).mockResolvedValue(fixed);
    const onSwitched = vi.fn();
    render(<DatasetTab active={{ ...FIXTURE, source: "upload", review_judge: "items",
      judge_error: "LLM 판정에 실패해 평가 사유 대신 항목 점수를 썼다: LLM API 오류(HTTP 503)" }} onSwitched={onSwitched} />);
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 503");
    fireEvent.click(screen.getByRole("button", { name: "판정 다시 시도" }));
    await waitFor(() => expect(onSwitched).toHaveBeenCalledWith(fixed));
  });

  it("서버가 실데이터 외부 전송을 허용했으면 그 사실을 보인다", () => {
    render(<DatasetTab active={{ ...FIXTURE, review_judge: "fixture",
      judge_endpoint: { host: "api.openai.com", location: "openai", external: true, model: "m", external_allowed: true } }}
      onSwitched={vi.fn()} />);
    expect(screen.getByText(/실데이터의 평가 원문도 이곳으로 보내도록 허용돼 있다/)).toBeInTheDocument();
  });

  it("외부 전송 미허용으로 판정하지 않았으면 다시 시도 버튼 없이 이유를 보인다", () => {
    render(<DatasetTab active={{ ...FIXTURE, source: "upload", synthetic: false, review_judge: "blocked",
      judge_error: "실데이터의 평가 사유를 외부(OpenAI)로 보내지 않아 항목 점수를 썼다." }} onSwitched={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("외부(OpenAI)로 보내지 않아");
    expect(screen.queryByRole("button", { name: "판정 다시 시도" })).not.toBeInTheDocument();
    expect(screen.getByText(/외부 전송이 허용되지 않아 판정하지 않음/)).toBeInTheDocument();
  });
});


const REC: PlacementRecord = { id: "a".repeat(16), title: "2026년 10월 연초 편성 · 100명", kind: "planning",
  exported_at: "2026-10-12T10:00:00+0900", imported_at: "2026-10-18T10:00:00+0900", dataset_id: "org-n100",
  people: 100, projects: 20, horizon_start: "2026-10", horizon_months: 6, synthetic: true, plans: 4, operating: false,
  open: false, score_changed: [] };

describe("DatasetTab — 편성 기록(2026-10-18, 시연 데이터 고르기를 대체)", () => {
  beforeEach(() => { vi.mocked(fetchRecords).mockReset(); vi.mocked(importRecord).mockReset(); vi.mocked(openRecord).mockReset(); });

  it("시연 데이터 고르기는 없고, 기록이 없으면 불러오기를 안내한다", async () => {
    vi.mocked(fetchRecords).mockResolvedValue([]);
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    expect(await screen.findByText("아직 불러온 편성 기록이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText(/시연 데이터 고르기/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "기록 파일 불러오기" })).toBeInTheDocument();
  });

  it("목록에 제목·종류·규모·계획 기간을 보인다", async () => {
    vi.mocked(fetchRecords).mockResolvedValue([REC, { ...REC, id: "b".repeat(16), kind: "operating", title: "운영 중 기록" }]);
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    expect(await screen.findByText(REC.title)).toBeInTheDocument();
    expect(screen.getByText("연초 계획")).toBeInTheDocument();
    expect(screen.getByText("운영 중")).toBeInTheDocument();
    expect(screen.getAllByText("2026-10 ~ 2027-03")).toHaveLength(2);
  });

  it("기록 파일을 불러오면 확인 결과를 알리고 목록을 새로 읽는다", async () => {
    vi.mocked(fetchRecords).mockResolvedValueOnce([]).mockResolvedValue([REC]);
    vi.mocked(importRecord).mockResolvedValue({ record: REC, check: { plans: 4, score_changed: [], operating: false, skipped: [] } });
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("편성 기록 파일"), { target: { files: [new File(["{}"], "r.json")] } });
    expect(await screen.findByText(/서버가 다시 채점해 기록과 같음을 확인했습니다/)).toBeInTheDocument();
    expect(await screen.findByText(REC.title)).toBeInTheDocument();
  });

  it("열기는 한 번 더 확인하고, 열면 결과를 App에 넘긴다", async () => {
    vi.mocked(fetchRecords).mockResolvedValue([REC]);
    const res = { dataset: { ...FIXTURE, source: "record" }, record: REC,
                  request: { weights: {}, milp_params: {}, n_alternatives: 3 }, operating_params: null,
                  check: { plans: 4, score_changed: [], operating: false, skipped: [] } } as unknown as OpenRecordResult;
    vi.mocked(openRecord).mockResolvedValue(res);
    const onRecordOpened = vi.fn();
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} onRecordOpened={onRecordOpened} />);
    fireEvent.click(await screen.findByRole("button", { name: "열기" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("작업 데이터와 그 결과가 이 기록으로 바뀝니다");
    expect(openRecord).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole("button", { name: "열기" }).at(-1)!);
    await waitFor(() => expect(onRecordOpened).toHaveBeenCalledWith(res));
  });

  it("편성안이 없으면 '지금 작업 내보내기'는 누를 수 없다", async () => {
    vi.mocked(fetchRecords).mockResolvedValue([]);
    const { rerender } = render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} onExportCurrent={null} />);
    expect(screen.getByRole("button", { name: "지금 작업 내보내기" })).toBeDisabled();
    const onExport = vi.fn();
    rerender(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} onExportCurrent={onExport} />);
    fireEvent.click(screen.getByRole("button", { name: "지금 작업 내보내기" }));
    expect(onExport).toHaveBeenCalled();
  });
});
