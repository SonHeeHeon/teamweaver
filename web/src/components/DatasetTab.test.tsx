import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DatasetTab } from "./DatasetTab";
import type { DatasetInfo } from "../api/types";

vi.mock("../api/client", () => ({ uploadDataset: vi.fn(), resetDataset: vi.fn(),
                                  AdminLoginRequiredError: class extends Error {} }));
import { resetDataset, uploadDataset } from "../api/client";

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
