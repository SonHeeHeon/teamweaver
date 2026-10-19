import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

// 사업 수명 주기 표시(2026-10-19, 사용자 "24년에 시작해서 아직 진행중인 플젝 … 추가 과제가 있어 6달만 회사 인력은 2명만 추가(외주는 여러명)")
vi.mock("../scenarios/workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("../scenarios/workspaceApi")>();
  return { ...real, fetchWorkspace: vi.fn(), fetchCandidates: vi.fn(), fetchSkillDictionary: vi.fn(), fetchProjectForm: vi.fn() };
});
vi.mock("../api/client", () => ({
  DatasetChangedError: class extends Error {}, streamOperatingCompare: vi.fn(), postJustification: vi.fn(), API_BASE: "",
}));

import * as api from "../scenarios/workspaceApi";
import type { Workspace } from "../scenarios/workspaceApi";
import type { Meta, Project } from "../api/types";
import { lifecycleTags, projectPeriod, shortMonth } from "./lifecycle";
import { RequirementsTab } from "./RequirementsTab";
import { AssignmentTable } from "./AssignmentTable";
import { NewProjectsScreen } from "../scenarios/NewProjectsScreen";

const OPS: Project = { id: "J021", name: "[운영] 금융사 아 문서 전환", sector: "대외금융", phase: "실행", start_month: 0, end_month: 11,
                       grade_headcount: { 고급: 1 }, monthly_budget: 3000,
                       lifecycle: { stage: "운영", started: "2024-02", parent: null, external_headcount: null },
                       period: { start: "2024-02", end: "2028-10" } };
const ADDON: Project = { id: "J025", name: "[추가 과제] 금융사 아 클라우드 마이그레이션", sector: "대외금융", phase: "실행", start_month: 2,
                         end_month: 7, grade_headcount: { 고급: 1, 중급: 1 }, monthly_budget: 2100,
                         lifecycle: { stage: "추가 과제", started: "2026-12", parent: "J021", external_headcount: 3 } };
const DEV: Project = { id: "J004", name: "[실행] 금융사 마2 데이터 마트", sector: "대외금융", phase: "실행", start_month: 0, end_month: 5,
                       grade_headcount: { 중급: 2 }, monthly_budget: 4000,
                       lifecycle: { stage: "개발", started: "2026-02" }, period: { start: "2026-02", end: "2027-03" } };
const NEW: Project = { id: "J026", name: "금융사 바 데이터 마트", sector: "대외금융", phase: "제안", start_month: 9, end_month: 10,
                       grade_headcount: { 중급: 1 }, monthly_budget: 1500 };

describe("사업 이력 꼬리표", () => {
  it("이름 머리말과 같은 단계는 다시 쓰지 않고, 추가 과제는 상위 사업·외주(참고)를 보인다", () => {
    expect(lifecycleTags(OPS.lifecycle, OPS.name)).toEqual([]);
    expect(lifecycleTags(DEV.lifecycle, DEV.name)).toEqual(["개발"]);
    expect(lifecycleTags(ADDON.lifecycle, ADDON.name)).toEqual(["상위 J021", "외주 3명(참고)"]);
    expect(lifecycleTags({ stage: "추가 과제", parent: "J021", external_headcount: 0 }, "x")).toEqual(["추가 과제", "상위 J021"]);
    expect(lifecycleTags(null, "x")).toEqual([]);
  });

  it("계획 밖까지 이어지는 사업은 원래 기간과 계획 안 기간을 함께 보인다", () => {
    expect(shortMonth("2024-02")).toBe("24.2");
    expect(projectPeriod(OPS, "2026-10")).toBe("24.2~28.10월 · 계획 안 26.10~27.9월");
    expect(projectPeriod(NEW, "2026-10")).toBe("27.7~27.8월");
    expect(projectPeriod({ ...NEW, end_month: 9 }, "2026-10")).toBe("27.7월");
    expect(projectPeriod(OPS, null)).toBe("");
  });
});

const META: Meta = { people: [{ id: "P1", name: "김민준", grade: "고급", skills: {} }], projects: [OPS, ADDON, DEV, NEW],
                     skills: [], review_items: [], coworks: [], dataset_version: "a".repeat(64), horizon_start: "2026-10",
                     horizon_months: 12 };

describe("화면에 붙은 사업 이력", () => {
  it("연간 계획 사업 표에 기간 줄과 꼬리표가 있다", () => {
    render(<RequirementsTab meta={META} weights={{}} onWeightsChange={() => {}} onRun={() => {}} running={false} />);
    const row = screen.getByText("[추가 과제] 금융사 아 클라우드 마이그레이션").closest("tr")!;
    expect(within(row).getByText("상위 J021")).toBeInTheDocument();
    expect(within(row).getByText("외주 3명(참고)")).toBeInTheDocument();
    expect(within(row).getByText("26.12~27.5월")).toBeInTheDocument();
    expect(within(screen.getByText("[운영] 금융사 아 문서 전환").closest("tr")!).getByText("24.2~28.10월 · 계획 안 26.10~27.9월"))
      .toBeInTheDocument();
  });

  it("계획 시작 달을 모르면(예전 고정 데이터) 기간 줄을 그리지 않는다", () => {
    render(<RequirementsTab meta={{ ...META, horizon_start: null }} weights={{}} onWeightsChange={() => {}} onRun={() => {}}
                            running={false} />);
    expect(screen.queryByText(/계획 안/)).toBeNull();
    expect(screen.getByText("[추가 과제] 금융사 아 클라우드 마이그레이션")).toBeInTheDocument();
  });

  it("결과 표의 사업 머리에 원래 기간과 꼬리표가 붙는다", () => {
    render(<AssignmentTable entries={[{ person_id: "P1", project_id: "J004", alloc: 1 }]} people={META.people}
                            projects={META.projects} horizonStart="2026-10" />);
    expect(screen.getByText("26.2~27.3월 · 계획 안 26.10~27.3월")).toBeInTheDocument();
    expect(screen.getByLabelText("[실행] 금융사 마2 데이터 마트 사업 이력").textContent).toBe("개발");
  });

  it("진행 사업 목록에 실제 시작~종료와 단계가 보인다", async () => {
    const ws: Workspace = {
      dataset_version: "v1", dataset_id: "org", synthetic: true, editable: true, horizon_start: "2026-10", horizon_last: "2027-03",
      pipeline: [],
      ongoing: [{ project_id: "J004", name: "[실행] 금융사 마2 데이터 마트", start: "2026-02", end: "2027-03", monthly_budget: 4000,
                  ends_in_horizon: false, locked_all: false, locked_count: 0, seats: {}, addons: [],
                  lifecycle: { stage: "개발", started: "2026-02", parent: null, external_headcount: null },
                  team: [{ person_id: "P1", name: "김민준", grade: "고급", alloc: 1, locked: false }] }],
      bench: [], history: [],
    };
    vi.mocked(api.fetchWorkspace).mockResolvedValue(ws);
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    render(<NewProjectsScreen milpParams={null} datasetVersion="v1" onDatasetChanged={vi.fn()} />);
    expect(await screen.findByText("J004 · 2026-02~2027-03 · 1명")).toBeInTheDocument();
    expect(screen.getByLabelText("[실행] 금융사 마2 데이터 마트 사업 이력").textContent).toBe("개발");
  });
});
