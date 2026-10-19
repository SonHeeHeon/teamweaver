/** 프로젝트 동시 수행(2026-10-19, claude-a -- 사용자 결정: 사람 단위 허용, 사유 필수, 전일 투입 기본). */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("./workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./workspaceApi")>();
  return {
    ...real,
    fetchWorkspace: vi.fn(), fetchSkillDictionary: vi.fn(), fetchStandby: vi.fn(), postEmergency: vi.fn(), postApply: vi.fn(),
    fetchConcurrencyCandidates: vi.fn(), postConcurrency: vi.fn(),
  };
});
vi.mock("../api/client", () => ({
  DatasetChangedError: class extends Error {}, streamOperatingCompare: vi.fn(), postJustification: vi.fn(), API_BASE: "",
}));

import * as api from "./workspaceApi";
import { ConcurrencyPanel } from "./ConcurrencyPanel";
import { EmergencyScreen } from "./EmergencyScreen";
import { AssignmentTable } from "../components/AssignmentTable";
import { SettingsTab } from "../components/SettingsTab";
import type { CandidateRow, Workspace } from "./workspaceApi";
import type { PlacementSettings, SettingsResponse } from "../api/types";

const WS: Workspace = {
  dataset_version: "v1", dataset_id: "org", synthetic: true, editable: true, horizon_start: "2026-10", horizon_last: "2027-03",
  pipeline: [],
  ongoing: [{ project_id: "J003", name: "은행 A 데이터", start: "2026-10", end: "2027-03", monthly_budget: 9000, ends_in_horizon: false,
              locked_all: false, locked_count: 0, seats: {},
              team: [{ person_id: "P1", name: "김민준", grade: "고급", alloc: 1, locked: false }] },
            { project_id: "J007", name: "금융사 마트", start: "2026-10", end: "2027-03", monthly_budget: 5000, ends_in_horizon: false,
              locked_all: false, locked_count: 0, seats: {},
              team: [{ person_id: "P2", name: "이서연", grade: "중급", alloc: 1, locked: false }] }],
  bench: [], history: [],
};

beforeEach(() => { vi.clearAllMocks(); });

describe("ConcurrencyPanel", () => {
  it("빈자리마다 후보를 보이고, 사유와 함께 허용하면 새 판을 알린다", async () => {
    vi.mocked(api.fetchConcurrencyCandidates).mockResolvedValue({ dataset_version: "v1", shortfalls: [
      { project_id: "J013", grade: "고급", missing: 1, budget_left: 2000, free_eligible: 0,
        candidates: [{ person_id: "P1", name: "김민준", grade: "고급", skill_fit: 0.81, monthly_cost: 1300, busy_with: ["J003"],
                       already_allowed: false, needs_more: true }] }] });
    vi.mocked(api.postConcurrency).mockResolvedValue({ workspace: WS });
    const granted = vi.fn();
    render(<ConcurrencyPanel datasetVersion="v1" entries={[{ person_id: "P1", project_id: "J003", alloc: 1 }]}
                             projectName={(id) => ({ J013: "신규 제안", J003: "은행 A 데이터" }[id] ?? id)} onGranted={granted} />);
    expect(await screen.findByText(/신규 제안 · 고급/)).toBeInTheDocument();
    expect(screen.getByText(/지금 은행 A 데이터/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "김민준 동시 수행 허용" }));
    fireEvent.change(screen.getByLabelText(/사유/), { target: { value: "기술 지원" } });
    fireEvent.change(screen.getByLabelText("메모"), { target: { value: "잠깐 봐줌" } });
    fireEvent.click(screen.getByRole("button", { name: "허용" }));
    await waitFor(() => expect(api.postConcurrency).toHaveBeenCalledWith("v1", "P1", true, "기술 지원", "잠깐 봐줌", 2));
    expect(granted).toHaveBeenCalledWith(expect.stringContaining("프로젝트 동시 수행을 허용했다(기술 지원)"));
  });

  it("이미 허용된 사람이 두 사업에 있으면 상한을 3으로 올려 보낸다(폴백 리뷰 P2)", async () => {
    vi.mocked(api.fetchConcurrencyCandidates).mockResolvedValue({ dataset_version: "v1", shortfalls: [
      { project_id: "J013", grade: "고급", missing: 1, budget_left: 2000, free_eligible: 0,
        candidates: [{ person_id: "P1", name: "김민준", grade: "고급", skill_fit: 0.81, monthly_cost: 1300, busy_with: ["J003", "J007"],
                       already_allowed: true, needs_more: true }] }] });
    vi.mocked(api.postConcurrency).mockResolvedValue({ workspace: WS });
    render(<ConcurrencyPanel datasetVersion="v1" entries={[]} projectName={(id) => id} onGranted={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "김민준 동시 수행 상한 3으로 올리기" }));
    fireEvent.click(screen.getByRole("button", { name: "허용" }));
    await waitFor(() => expect(api.postConcurrency).toHaveBeenCalledWith("v1", "P1", true, "인력 부족", "", 3));
  });
});

describe("EmergencyScreen 동시 수행으로 지원", () => {
  it("원래 사업을 둔 채 넣고, 확정에 증원과 허용(긴급 지원)을 함께 보낸다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    vi.mocked(api.fetchStandby).mockResolvedValue({ source: null, computed_at: null, projects: {} });
    const row: CandidateRow = { person_id: "P1", name: "김민준", grade: "고급", alloc: 1, source: "concurrent", pulled_from: [],
      delta_total: 1.2, delta: {}, new_violations: [], category: "pull", predecessor_team: false, monthly_cost: 1300, needs_grant: true,
      apply_hint: { headcount_add: { J007: { 고급: 1 } }, budget_add: { J007: 900 }, skills_add: {} } };
    vi.mocked(api.postEmergency).mockResolvedValue({ dataset_version: "v1", project_id: "J007", skills: [], added_requirements: [],
      best: { accepted: false }, candidates: { bench: [], spare: [row], pull: [] }, standby: [], apply_hint: null, note: "" });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: WS, changes: { added: ["P1@J007"], removed: [], changed: [] } });
    render(<EmergencyScreen onDatasetChanged={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("긴급 투입할 사업"), { target: { value: "J007" } });
    fireEvent.click(screen.getByRole("button", { name: "후보 찾기" }));
    fireEvent.click(await screen.findByRole("tab", { name: /동시 수행으로 지원 1/ }));
    expect(screen.getByText(/원래 사업 유지 · 허용 필요/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "김민준 동시 수행으로 지원" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    const [, body] = vi.mocked(api.postApply).mock.calls[0];
    expect(body).toMatchObject({ source: "emergency", headcount_add: { J007: { 고급: 1 } }, budget_add: { J007: 900 } });   // 서버가 계산한 증원 그대로
    expect(body.entries).toEqual(expect.arrayContaining([{ person_id: "P1", project_id: "J003", alloc: 1 },
                                                         { person_id: "P1", project_id: "J007", alloc: 1 }]));
    expect(body.concurrency_add).toEqual([expect.objectContaining({ person_id: "P1" })]);
  });
});

describe("EmergencyScreen 미리 허용된 사람의 지원", () => {
  it("허용이 이미 있으면 확정에 허용을 다시 적지 않는다(기존 사유 유지, Codex 리뷰)", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    vi.mocked(api.fetchStandby).mockResolvedValue({ source: null, computed_at: null, projects: {} });
    const row: CandidateRow = { person_id: "P1", name: "김민준", grade: "고급", alloc: 1, source: "concurrent", pulled_from: [],
      delta_total: 1.2, delta: {}, new_violations: [], category: "pull", predecessor_team: false, monthly_cost: 1300, needs_grant: false,
      apply_hint: { headcount_add: { J007: { 고급: 1 } }, budget_add: { J007: 900 }, skills_add: {} } };
    vi.mocked(api.postEmergency).mockResolvedValue({ dataset_version: "v1", project_id: "J007", skills: [], added_requirements: [],
      best: { accepted: false }, candidates: { bench: [], spare: [row], pull: [] }, standby: [], apply_hint: null, note: "" });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: WS, changes: { added: ["P1@J007"], removed: [], changed: [] } });
    render(<EmergencyScreen onDatasetChanged={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("긴급 투입할 사업"), { target: { value: "J007" } });
    fireEvent.click(screen.getByRole("button", { name: "후보 찾기" }));
    fireEvent.click(await screen.findByRole("tab", { name: /동시 수행으로 지원 1/ }));
    fireEvent.click(screen.getByRole("button", { name: "김민준 동시 수행으로 지원" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    const [, body] = vi.mocked(api.postApply).mock.calls[0];
    expect(body).toMatchObject({ headcount_add: { J007: { 고급: 1 } } });
    expect(body.concurrency_add).toBeUndefined();
  });
});

describe("AssignmentTable 동시 수행 표시", () => {
  it("같은 달 두 사업인 사람에게 '동시 수행'과 허용 사유를 붙인다", () => {
    render(<AssignmentTable
      entries={[{ person_id: "P1", project_id: "J003", alloc: 1 }, { person_id: "P1", project_id: "J007", alloc: 1 },
                { person_id: "P2", project_id: "J007", alloc: 1 }]}
      people={[{ id: "P1", name: "김민준", grade: "고급", skills: {}, max_concurrent: 2, concurrency: { reason: "기술 지원" } },
               { id: "P2", name: "이서연", grade: "중급", skills: {} }]}
      projects={[{ id: "J003", name: "은행 A", sector: "대외금융", phase: "실행", start_month: 0, end_month: 5, grade_headcount: {}, monthly_budget: 1 },
                 { id: "J007", name: "마트", sector: "대외금융", phase: "실행", start_month: 2, end_month: 5, grade_headcount: {}, monthly_budget: 1 }]} />);
    expect(screen.getAllByText("동시 수행 · 기술 지원")).toHaveLength(2);
    expect(screen.queryByText(/이서연.*동시 수행/)).not.toBeInTheDocument();
  });
});

describe("SettingsTab 전일 투입", () => {
  const settings: PlacementSettings = { min_alloc: 0.3, clique_threshold_months: 12, clique_window_months: 36, lam: 0.3, mu: 0.2,
    time_limit: 120, gap: 0.05, max_concurrent_projects: 3, allocation_mode: "full", time_limit_auto: true, solver_seeds: null };
  const data = { settings, defaults: settings, bounds: {}, updated_at: null, load_error: null,
                 recommended_time: null, effective_time_limit: 30, effective_solver_seeds: 1 } as unknown as SettingsResponse;
  it("전일 투입이면 투입률 나누기 칸을 숨기고, 예전 방식을 고르면 다시 보인다", () => {
    render(<SettingsTab data={data} onSave={vi.fn()} />);
    expect(screen.getByLabelText("전일 투입(100%)")).toBeChecked();
    expect(screen.queryByLabelText(/최소 투입률/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("기간 내내 한 비율"));
    expect(screen.getByLabelText(/최소 투입률/)).toBeInTheDocument();
  });
});
