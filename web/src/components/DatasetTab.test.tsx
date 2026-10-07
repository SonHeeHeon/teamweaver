import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DatasetTab } from "./DatasetTab";
import type { DatasetInfo } from "../api/types";

vi.mock("../api/client", () => ({ uploadDataset: vi.fn(), resetDataset: vi.fn(), rejudgeDataset: vi.fn(),
                                  fetchDemos: vi.fn(async () => []), chooseDemo: vi.fn(),
                                  AdminLoginRequiredError: class extends Error {} }));
import { chooseDemo, fetchDemos, rejudgeDataset, resetDataset, uploadDataset } from "../api/client";

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
  it("시연 묶음은 '시연 데이터(실제 형식)'로, 서버가 준 오류 문장은 그대로 보인다", () => {
    render(<DatasetTab active={{ ...FIXTURE, source: "demo-bundle",
      restore_error: "시연 데이터 묶음을 읽지 못해 기본 데이터로 시작했다: x" } as any} onSwitched={vi.fn()} />);
    expect(screen.getByText(/시연 데이터\(실제 형식\)/)).toBeInTheDocument();
    expect(screen.getByText("시연 데이터 묶음을 읽지 못해 기본 데이터로 시작했다: x")).toBeInTheDocument();
    expect(screen.queryByText(/저장된 업로드 데이터를 복원하지 못해/)).not.toBeInTheDocument();
  });
});



describe("DatasetTab — 평가 사유 LLM 판정(2026-10-06 LLM 통일)", () => {
  it("판정 정보와 다음 업로드의 글이 갈 곳(외부 OpenAI)을 보인다", () => {
    render(<DatasetTab active={{ ...FIXTURE, source: "upload", synthetic: false, review_judge: "llm",
      judge_model: "gpt-6-luna", judge_host: "api.openai.com", judge_location: "openai", judge_external: true,
      judge_endpoint: { host: "api.openai.com", location: "openai", external: true, model: "gpt-6-luna" } }} onSwitched={vi.fn()} />);
    expect(screen.getByText(/평가 사유\(글\) 판정: LLM gpt-6-luna · 외부\(OpenAI\)/)).toBeInTheDocument();
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


describe("DatasetTab — 시연 데이터 고르기", () => {
  it("운영 중 시연 데이터를 골라 전환한다", async () => {
    vi.mocked(fetchDemos).mockResolvedValue([
      { name: "org-n100", dataset_id: "a", people: 100, projects: 13, scenario: "planning", synthetic: true },
      { name: "org-n100-operating", dataset_id: "b", people: 100, projects: 15, scenario: "operating", synthetic: true }]);
    const next = { ...FIXTURE, source: "demo-bundle" as const, version: "d".repeat(64) };
    vi.mocked(chooseDemo).mockResolvedValue(next);
    const onSwitched = vi.fn();
    render(<DatasetTab active={FIXTURE} onSwitched={onSwitched} />);
    fireEvent.change(await screen.findByLabelText("시연 데이터"), { target: { value: "org-n100-operating" } });
    expect(screen.getByText(/운영 중\(대부분 배치됨\) · 100명 · 사업 15건/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "이 시연 데이터로 전환" }));
    expect(chooseDemo).not.toHaveBeenCalled();                       // 업로드 보관본을 지우므로 한 번 더 확인
    fireEvent.click(screen.getByRole("button", { name: "전환 확인" }));
    await waitFor(() => expect(onSwitched).toHaveBeenCalledWith(next));
    expect(vi.mocked(chooseDemo).mock.calls[0][0]).toBe("org-n100-operating");
  });

  it("목록은 장면 제목으로 보이고, 고른 묶음의 설명을 보인다", async () => {
    vi.mocked(fetchDemos).mockResolvedValue([
      { name: "org-n100", dataset_id: "a", people: 100, projects: 13, scenario: "planning", synthetic: true,
        title: "연초 계획 · 100명 전체 배치", description: "사업 13개를 처음부터 편성한다." },
      { name: "org-n100-operating", dataset_id: "b", people: 100, projects: 15, scenario: "operating", synthetic: true,
        title: "운영 중 · 90명 배치 중, 대기 10명", description: "막 일이 끝난 10명으로 신규 제안 2개를 편성한다." }]);
    render(<DatasetTab active={FIXTURE} onSwitched={vi.fn()} />);
    const select = await screen.findByLabelText("시연 데이터");
    expect(screen.getByRole("option", { name: /운영 중 · 90명 배치 중, 대기 10명 · 사업 15건 \(org-n100-operating\)/ }))
      .toBeInTheDocument();
    expect(screen.getByText("사업 13개를 처음부터 편성한다.")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "org-n100-operating" } });
    expect(screen.getByText("막 일이 끝난 10명으로 신규 제안 2개를 편성한다.")).toBeInTheDocument();
  });

  it("처음 고른 항목은 지금 켜진 시연 묶음이다", async () => {
    vi.mocked(fetchDemos).mockResolvedValue([
      { name: "org-n100", dataset_id: "a", people: 100, projects: 13, scenario: "planning", synthetic: true,
        title: "연초 계획", description: "처음부터 편성." },
      { name: "org-n100-operating", dataset_id: "b", people: 100, projects: 15, scenario: "operating", synthetic: true,
        title: "운영 중", description: "신규 제안 편성." }]);
    render(<DatasetTab active={{ ...FIXTURE, source: "demo-bundle", demo_name: "org-n100-operating" }} onSwitched={vi.fn()} />);
    expect(await screen.findByText("신규 제안 편성.")).toBeInTheDocument();
    expect(screen.getByLabelText("시연 데이터")).toHaveValue("org-n100-operating");
  });
});

