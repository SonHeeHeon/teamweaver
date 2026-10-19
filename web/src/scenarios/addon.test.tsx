import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// 진행 사업에 추가 과제 붙이기(2026-10-19, 사용자 예: "운영에 클라우드 마이그레이션 … 6달만 회사 인력은 2명만 추가(외주는 여러명)")
vi.mock("./workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./workspaceApi")>();
  return { ...real, fetchWorkspace: vi.fn(), fetchCandidates: vi.fn(), fetchSkillDictionary: vi.fn(), fetchProjectForm: vi.fn(),
           postProject: vi.fn() };
});
vi.mock("../api/client", () => ({
  DatasetChangedError: class extends Error {}, streamOperatingCompare: vi.fn(), postJustification: vi.fn(), API_BASE: "",
}));

import * as api from "./workspaceApi";
import { ProjectForm } from "./ProjectForm";
import { NewProjectsScreen } from "./NewProjectsScreen";
import type { ProjectForm as Form, Workspace } from "./workspaceApi";

const ADDON: Form = { name: "은행 A 데이터 추가 과제", sector: "대외금융", start_month: "2026-11", end_month: "2027-03",
                      monthly_budget: 2100, stage: "예정", predecessor: null, parent: "J001", external_headcount: 0,
                      grades: { 특급: 0, 고급: 1, 중급: 1, 초급: 0 }, skills: [{ name: "ETL", min_months: 24, headcount: 1 }] };
const PARENTS = [{ project_id: "J001", name: "은행 A 데이터", start: "2026-10", end: "2027-03" }];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
});

describe("추가 과제", () => {
  it("상위 사업을 고르면 미리 채우고, 외주 인원과 함께 등록한다", async () => {
    vi.mocked(api.fetchProjectForm).mockResolvedValue(ADDON);
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm start="2026-10" predecessors={[]} parents={PARENTS} onSubmit={onSubmit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("시작 달"), { target: { value: "2026-11" } });
    fireEvent.change(screen.getByLabelText("종료 달"), { target: { value: "2027-03" } });
    fireEvent.change(screen.getByLabelText("사업명"), { target: { value: "클라우드 마이그레이션" } });
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("상위 사업(추가 과제)"), { target: { value: "J001" } });
    await waitFor(() => expect(api.fetchProjectForm).toHaveBeenCalledWith("J001", { start: "2026-11" }));
    expect(await screen.findByRole("heading", { name: "추가 과제 등록" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/외주 인원/), { target: { value: "6" } });
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByText(/J001\(추가 과제\) · 외주 6명/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "등록" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ name: "클라우드 마이그레이션", parent: "J001", predecessor: null,
                                                     external_headcount: 6, start_month: "2026-11", end_month: "2027-03" });
  });

  it("상위 사업 기간을 벗어나면 다음으로 가지 못한다", async () => {
    vi.mocked(api.fetchProjectForm).mockResolvedValue(ADDON);
    render(<ProjectForm start="2026-10" predecessors={[]} parents={PARENTS} initialParent="J001" onSubmit={vi.fn()} onCancel={vi.fn()} />);
    await waitFor(() => expect((screen.getByLabelText("사업명") as HTMLInputElement).value).toBe("은행 A 데이터 추가 과제"));
    fireEvent.change(screen.getByLabelText("종료 달"), { target: { value: "2027-06" } });
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    expect(screen.getByRole("alert").textContent).toContain("상위 사업 기간(2026-10~2027-03) 안이어야 한다");
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("진행 사업 행에서 추가 과제를 붙이고, 신규 사업 목록에 상위 사업·외주가 보인다", async () => {
    const ws: Workspace = {
      dataset_version: "v1", dataset_id: "org", synthetic: true, editable: true, horizon_start: "2026-10", horizon_last: "2027-03",
      pipeline: [{ project_id: "J016", name: "J001 클라우드 마이그레이션", start: "2026-11", end: "2027-03", monthly_budget: 2100,
                   stage: "예정", predecessor: null, parent: "J001", external_headcount: 6, seats: {}, team: [] }],
      ongoing: [{ project_id: "J001", name: "은행 A 데이터", start: "2026-10", end: "2027-03", monthly_budget: 9000,
                  ends_in_horizon: false, locked_all: false, locked_count: 0, seats: {}, addons: ["J016"],
                  team: [{ person_id: "P1", name: "김민준", grade: "고급", alloc: 1, locked: false }] }],
      bench: [], history: [],
    };
    vi.mocked(api.fetchWorkspace).mockResolvedValue(ws);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J016", predecessor: null, parent: "J001", note: "", bench: [],
      ending: [], pull: [{ person_id: "P1", name: "김민준", grade: "고급", alloc: 1, source: "pull", pulled_from: ["J001"],
                           delta_total: 3, delta: {}, new_violations: [], category: "pull", predecessor_team: false, parent_team: true }] });
    vi.mocked(api.fetchProjectForm).mockResolvedValue(ADDON);
    render(<NewProjectsScreen milpParams={null} datasetVersion="v1" onDatasetChanged={vi.fn()} />);
    const list = await screen.findByRole("list", { name: "신규 사업 목록" });
    expect(within(list).getByText("추가 과제 — 상위 J001 · 외주 6명(참고)")).toBeInTheDocument();
    expect(screen.getByText("추가 과제 J016")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /진행 사업에서 빼 오기/ }));
    expect(await screen.findByText("상위 사업 팀원")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "은행 A 데이터에 추가 과제 붙이기" }));
    await waitFor(() => expect(api.fetchProjectForm).toHaveBeenCalledWith("J001", { start: "2026-10" }));
    expect(await screen.findByRole("heading", { name: "추가 과제 등록" })).toBeInTheDocument();
  });
});

describe("미리 채우기 대기 중 잠금(Codex 리뷰 2026-10-19)", () => {
  it("상위 사업 응답이 오기 전에는 입력이 잠겨 늦은 응답이 고친 값을 덮지 못한다", async () => {
    let resolve: (v: Form) => void = () => {};
    vi.mocked(api.fetchProjectForm).mockReturnValueOnce(new Promise<Form>((r) => { resolve = r; }));
    render(<ProjectForm start="2026-10" predecessors={[]} parents={PARENTS} initialParent="J001" onSubmit={vi.fn()} onCancel={vi.fn()} />);
    const sector = screen.getByLabelText("고객 구분") as HTMLSelectElement;
    expect(sector).toBeDisabled();
    expect(screen.getByRole("button", { name: "복사하는 중…" })).toBeDisabled();
    resolve(ADDON);
    await waitFor(() => expect(sector).not.toBeDisabled());
    expect((screen.getByLabelText("사업명") as HTMLInputElement).value).toBe("은행 A 데이터 추가 과제");
  });
});
