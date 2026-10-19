import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("./workspaceApi", async (importOriginal) => {
  const real = await importOriginal<typeof import("./workspaceApi")>();
  return {
    ...real,
    fetchWorkspace: vi.fn(), fetchCandidates: vi.fn(), fetchSkillDictionary: vi.fn(), fetchProjectForm: vi.fn(),
    postProject: vi.fn(), patchStage: vi.fn(), dropProject: vi.fn(), postLock: vi.fn(), postApply: vi.fn(), postUndo: vi.fn(),
    postEmergency: vi.fn(), fetchStandby: vi.fn(),
  };
});
vi.mock("../api/client", () => ({
  DatasetChangedError: class extends Error {}, streamOperatingCompare: vi.fn(), postJustification: vi.fn(), API_BASE: "",
}));

import * as api from "./workspaceApi";
import { formProblems, emptyForm } from "./formRules";
import { ProjectForm } from "./ProjectForm";
import { NewProjectsScreen } from "./NewProjectsScreen";
import { EmergencyScreen } from "./EmergencyScreen";
import type { CandidateRow, Workspace } from "./workspaceApi";

const WS: Workspace = {
  dataset_version: "v1", dataset_id: "org", synthetic: true, editable: true, horizon_start: "2026-10", horizon_last: "2027-03",
  pipeline: [{ project_id: "J014", name: "보험사 B 고도화", start: "2026-11", end: "2027-03", monthly_budget: 6000, stage: "제안",
               predecessor: "J003", seats: { 고급: { need: 2, filled: 0 } }, team: [] }],
  ongoing: [{ project_id: "J003", name: "은행 A 데이터", start: "2026-10", end: "2026-12", monthly_budget: 9000, ends_in_horizon: true,
              locked_all: false, locked_count: 0, seats: {},
              team: [{ person_id: "P1", name: "김민준", grade: "고급", alloc: 1, locked: false },
                     { person_id: "P2", name: "이서연", grade: "중급", alloc: 1, locked: false }] }],
  bench: [{ person_id: "P9", name: "박지훈", grade: "고급" }], history: [],
};
const cand = (over: Partial<CandidateRow>): CandidateRow => ({
  person_id: "P9", name: "박지훈", grade: "고급", alloc: 1, source: "bench", pulled_from: [], delta_total: 101.2,
  delta: { unfilled: 100 }, unfilled_seats_delta: 1, delta_excl_unfilled: 1.2, new_violations: [], category: "bench",
  predecessor_team: false, ...over,
});

beforeEach(() => { vi.clearAllMocks(); });

describe("formRules", () => {
  it("단계마다 빈칸·순서·인원·중복 기술을 막는다", () => {
    const f = emptyForm("2026-10");
    expect(formProblems(f, 0)).toContain("사업명을 적는다");
    expect(formProblems({ ...f, name: "x", end_month: "2026-09" }, 0)).toContain("종료 달이 시작 달보다 앞이다");
    expect(formProblems({ ...f, name: "x", grades: { 고급: 0 } }, 2)).toContain("등급별 인원이 한 명 이상이어야 한다");
    expect(formProblems({ ...f, name: "x", skills: [{ name: "Java", min_months: 24, headcount: 1 },
                                                    { name: "java", min_months: 12, headcount: 1 }] }, 3))
      .toContain("같은 기술이 두 번 들어 있다");
    expect(formProblems({ ...f, name: "x" }, 4)).toEqual([]);
  });
});

describe("ProjectForm", () => {
  it("이전 사업을 고르면 요구를 복사하고, 사전에서 고른 기술과 함께 등록한다", async () => {
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([{ name: "Kubernetes", aliases: ["쿠버네티스"] }]);
    vi.mocked(api.fetchProjectForm).mockResolvedValue({
      name: "은행 A 데이터", sector: "대외금융", start_month: "2026-10", end_month: "2026-12", monthly_budget: 9000, stage: "수주",
      predecessor: null, grades: { 고급: 3, 초급: 0 }, skills: [{ name: "ETL", min_months: 24, headcount: 2 }] });
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm start="2026-10" predecessors={[{ project_id: "J003", name: "은행 A 데이터", end: "2026-12" }]}
                        onSubmit={onSubmit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("사업명"), { target: { value: "은행 A 고도화" } });
    fireEvent.change(screen.getByLabelText("종료 달"), { target: { value: "2028-06" } });
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("이전 사업"), { target: { value: "J003" } });
    await waitFor(() => expect(api.fetchProjectForm).toHaveBeenCalledWith("J003"));
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    expect((screen.getByLabelText("고급") as HTMLInputElement).value).toBe("3");
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("기술 검색"), { target: { value: "쿠버" } });
    fireEvent.click(await screen.findByRole("button", { name: "+ Kubernetes" }));
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.click(screen.getByRole("button", { name: "등록" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const form = onSubmit.mock.calls[0][0];
    expect(form).toMatchObject({ name: "은행 A 고도화", predecessor: "J003", end_month: "2028-06", grades: { 고급: 3, 초급: 0 } });
    expect(form.skills.map((s: { name: string }) => s.name)).toEqual(["ETL", "Kubernetes"]);
  });
});

describe("NewProjectsScreen", () => {
  it("후보 세 갈래에서 넣은 사람을 확정하면 빼 오는 배치를 빼고 명단을 보낸다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J014", predecessor: "J003", note: "계산상",
      bench: [cand({})], ending: [],
      pull: [cand({ person_id: "P1", name: "김민준", source: "pull", pulled_from: ["J003"], category: "pull", predecessor_team: true,
                    delta_total: 0.4, delta: { unfilled: 0 }, unfilled_seats_delta: 0, delta_excl_unfilled: 0.4 })] });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: WS, changes: { added: [], removed: [], changed: [] } });
    const changed = vi.fn();
    const review = vi.fn();
    // App은 확정 응답 뒤에야 새 판을 받는다 -- 그 사이 옛 판("v0")을 들고 있어도 버튼이 남아야 한다(Codex 리뷰)
    const { rerender } = render(<NewProjectsScreen milpParams={null} datasetVersion="v0" onDatasetChanged={changed} onReviewDoc={review} />);
    expect(await screen.findByText("박지훈")).toBeInTheDocument();
    // 점수 변화를 나눠 보인다(사용자 2026-10-19): +101.20이 아니라 "1자리 메움"과 "+1.20"
    const row = screen.getByText("박지훈").closest("tr")!;
    expect(within(row).getByText("1자리 메움")).toBeInTheDocument();
    expect(within(row).getByText("+1.20")).toBeInTheDocument();
    expect(screen.queryByText("+101.20")).toBeNull();
    expect(screen.getByRole("columnheader", { name: "기술·협업 점수" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "박지훈 넣기" }));
    fireEvent.click(screen.getByRole("tab", { name: /진행 사업에서 빼 오기/ }));
    expect(screen.getByText("이전 팀원")).toBeInTheDocument();
    expect(within(screen.getByText("김민준").closest("tr")!).getByText("—")).toBeInTheDocument();      // 자리 변화 없음
    fireEvent.click(screen.getByRole("button", { name: "김민준 넣기" }));
    fireEvent.click(screen.getByRole("button", { name: "2명 확정" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    const [version, body] = vi.mocked(api.postApply).mock.calls[0];
    expect(version).toBe("v1");
    const keys = body.entries.map((e) => `${e.person_id}@${e.project_id}`);
    expect(keys).toEqual(expect.arrayContaining(["P9@J014", "P1@J014", "P2@J003"]));
    expect(keys).not.toContain("P1@J003");                                 // 빼 오는 배치는 빠진다
    expect(changed).toHaveBeenCalled();
    // 확정 뒤 인사팀 검토 자료로(claude-a 2026-10-19): 확정한 판과 그 사업
    fireEvent.click(await screen.findByRole("button", { name: "인사팀 검토 자료 만들기" }));
    expect(review).toHaveBeenCalledWith(WS.dataset_version, "J014");
    rerender(<NewProjectsScreen milpParams={null} datasetVersion={WS.dataset_version} onDatasetChanged={changed} onReviewDoc={review} />);
    expect(screen.getByRole("button", { name: "인사팀 검토 자료 만들기" })).toBeInTheDocument();
    // 맞춰진 뒤 옛 판으로 돌아가면(되돌리기) 버튼은 사라진다(Codex 리뷰 2라운드)
    rerender(<NewProjectsScreen milpParams={null} datasetVersion="v0" onDatasetChanged={changed} onReviewDoc={review} />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "인사팀 검토 자료 만들기" })).not.toBeInTheDocument());
  });

  it("진행 사업의 사람 한 명을 고정한다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J014", predecessor: null, note: "", bench: [], ending: [], pull: [] });
    vi.mocked(api.postLock).mockResolvedValue({ workspace: WS });
    render(<NewProjectsScreen milpParams={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /^은행 A 데이터 J003/ }));
    fireEvent.click(screen.getByRole("button", { name: "이서연 고정" }));
    await waitFor(() => expect(api.postLock).toHaveBeenCalledWith("v1", "J003", ["P2"], true));
  });

  it("바꾼 판을 서버에 남기지 못했으면 완료 알림에 함께 알린다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J014", predecessor: null, note: "", bench: [], ending: [], pull: [] });
    vi.mocked(api.postLock).mockResolvedValue({ workspace: WS, persisted: false, persist_error: "서버에 저장하지 못했다(디스크)" });
    render(<NewProjectsScreen milpParams={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /^은행 A 데이터 J003/ }));
    fireEvent.click(screen.getByRole("button", { name: "이서연 고정" }));
    expect(await screen.findByText(/주의: 서버에 저장하지 못했다\(디스크\)/)).toBeInTheDocument();
  });

  it("고칠 수 없는 데이터면 이유를 보인다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue({ ...WS, editable: false, reason: "기본 데이터는 고칠 수 없다" });
    render(<NewProjectsScreen milpParams={null} onDatasetChanged={vi.fn()} />);
    expect(await screen.findByText("기본 데이터는 고칠 수 없다")).toBeInTheDocument();
  });
});

describe("EmergencyScreen", () => {
  it("사업을 고르면 대기 명단이 바로 뜨고, 최선 조합을 증원 값과 함께 확정한다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    vi.mocked(api.fetchStandby).mockResolvedValue({ source: "nightly", computed_at: "t",
      projects: { J003: [{ person_id: "P9", name: "박지훈", grade: "고급", alloc: 1, source: "bench", delta_total: 3.1 }] } });
    const hint = { headcount_add: { J003: { 고급: 1 } }, budget_add: { J003: 900 }, skills_add: {} };
    vi.mocked(api.postEmergency).mockResolvedValue({ dataset_version: "v1", project_id: "J003", skills: [], added_requirements: [],
      best: { accepted: true, entries: [{ person_id: "P9", project_id: "J003", alloc: 1 }], added: [{ person_id: "P9", name: "박지훈" }],
              delta: { total: 3.1, unfilled: 0 }, unfilled_seats_delta: 0, delta_excl_unfilled: 3.1, new_violations: [],
              diff: { moved: [{ person_id: "P8", name: "김소율", from: "J008", to: ["J003"] }] } },
      candidates: { bench: [cand({})], spare: [], pull: [] }, standby: [], apply_hint: hint, note: "" });
    vi.mocked(api.postApply).mockResolvedValue({ workspace: WS, changes: { added: ["P9@J003"], removed: [], changed: [] } });
    const review = vi.fn();
    render(<EmergencyScreen onDatasetChanged={vi.fn()} onReviewDoc={review} />);
    fireEvent.change(await screen.findByLabelText("긴급 투입할 사업"), { target: { value: "J003" } });
    expect(await screen.findByText("밤사이 미리 뽑아 둔 명단")).toBeInTheDocument();
    expect(screen.getByText(/100% · 점수 \+3\.10/)).toBeInTheDocument();            // 나눔 없는 예전 밤 명단은 합계 그대로
    fireEvent.change(screen.getByLabelText(/무슨 일인지/), { target: { value: "  운영 이관 중 장애  " } });
    fireEvent.click(screen.getByRole("button", { name: "후보 찾기" }));
    expect(await screen.findByText(/고급 \+1/)).toBeInTheDocument();
    expect(screen.getByText(/기술·협업 \+3\.10 · 추가 월 비용/)).toBeInTheDocument();
    expect(screen.getByText("빼 오는 사람: 김소율(J008→J003)")).toBeInTheDocument();   // 사번이 아니라 이름(부서장 점검 f7)
    expect(within(screen.getByText("박지훈", { selector: "td" }).closest("tr")!).getByText("1자리 메움")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "이 조합 확정" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "확정" }));
    await waitFor(() => expect(api.postApply).toHaveBeenCalled());
    const [, body] = vi.mocked(api.postApply).mock.calls[0];
    expect(body).toMatchObject({ source: "emergency", headcount_add: hint.headcount_add, budget_add: hint.budget_add,
                                 situation: "운영 이관 중 장애" });                      // 검토 자료 상황 메모의 처음 값(부서장 점검 f6)
    fireEvent.click(await screen.findByRole("button", { name: "인사팀 검토 자료 만들기" }));
    expect(review).toHaveBeenCalledWith(WS.dataset_version, "J003");
  });
});

describe("늦은 응답·판 바뀜(Codex 리뷰 2026-10-19)", () => {
  it("긴급 투입: 사업을 바꾼 뒤 늦게 온 이전 사업의 결과는 버린다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue({ ...WS, ongoing: [...WS.ongoing,
      { ...WS.ongoing[0], project_id: "J007", name: "금융사 마트" }] });
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    vi.mocked(api.fetchStandby).mockResolvedValue({ source: null, computed_at: null, projects: {} });
    let resolveA: (v: unknown) => void = () => {};
    vi.mocked(api.postEmergency).mockReturnValueOnce(new Promise((r) => { resolveA = r; }) as never);
    render(<EmergencyScreen onDatasetChanged={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("긴급 투입할 사업"), { target: { value: "J003" } });
    fireEvent.click(screen.getByRole("button", { name: "후보 찾기" }));
    fireEvent.change(screen.getByLabelText("긴급 투입할 사업"), { target: { value: "J007" } });
    resolveA({ dataset_version: "v1", project_id: "J003", skills: [], added_requirements: [],
      best: { accepted: true, entries: [], added: [{ person_id: "P9", name: "박지훈" }], delta: { total: 1 }, new_violations: [] },
      candidates: { bench: [], spare: [], pull: [] }, standby: [], apply_hint: null, note: "" });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: "이 조합 확정" })).not.toBeInTheDocument();
  });

  it("양식은 네 등급을 모두 숫자로 보내고, 늦은 이전 사업 복사는 무시한다", async () => {
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    let resolveCopy: (v: unknown) => void = () => {};
    vi.mocked(api.fetchProjectForm).mockReturnValueOnce(new Promise((r) => { resolveCopy = r; }) as never);
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm start="2026-10" predecessors={[{ project_id: "J003", name: "은행 A", end: "2026-12" }]}
                        onSubmit={onSubmit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("사업명"), { target: { value: "새 사업" } });
    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.change(screen.getByLabelText("이전 사업"), { target: { value: "J003" } });
    fireEvent.change(screen.getByLabelText("이전 사업"), { target: { value: "" } });     // 응답 전에 "없음"으로
    resolveCopy({ name: "은행 A", sector: "대내", start_month: "2026-10", end_month: "2026-12", monthly_budget: 1,
                  stage: "수주", predecessor: null, grades: { 특급: 9 }, skills: [] });
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "다음" }));
    fireEvent.click(screen.getByRole("button", { name: "등록" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ predecessor: null, grades: { 특급: 0, 고급: 1, 중급: 1, 초급: 0 } });
  });
});

describe("대체 리뷰 회귀(2026-10-19)", () => {
  it("긴급 투입: 검색 중 사업을 바꾸면 '찾는 중'에 멈추지 않는다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue({ ...WS, ongoing: [...WS.ongoing,
      { ...WS.ongoing[0], project_id: "J007", name: "금융사 마트" }] });
    vi.mocked(api.fetchSkillDictionary).mockResolvedValue([]);
    vi.mocked(api.fetchStandby).mockResolvedValue({ source: null, computed_at: null, projects: {} });
    vi.mocked(api.postEmergency).mockReturnValueOnce(new Promise(() => {}) as never);
    render(<EmergencyScreen onDatasetChanged={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("긴급 투입할 사업"), { target: { value: "J003" } });
    fireEvent.click(screen.getByRole("button", { name: "후보 찾기" }));
    expect(screen.getByRole("button", { name: "찾는 중…" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("긴급 투입할 사업"), { target: { value: "J007" } });
    expect(await screen.findByRole("button", { name: "후보 찾기" })).toBeEnabled();
  });

  it("판이 바뀌어 409를 받으면 작업 데이터를 다시 읽는다", async () => {
    vi.mocked(api.fetchWorkspace).mockResolvedValue(WS);
    vi.mocked(api.fetchCandidates).mockResolvedValue({ project_id: "J014", predecessor: null, note: "", bench: [], ending: [], pull: [] });
    vi.mocked(api.postLock).mockRejectedValue(new api.ApiError("바뀜", 409, { code: "dataset_changed" }));
    const changed = vi.fn();
    const review = vi.fn();
    render(<NewProjectsScreen milpParams={null} onDatasetChanged={changed} onReviewDoc={review} />);
    fireEvent.click(await screen.findByRole("button", { name: "은행 A 데이터 사업 고정" }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(vi.mocked(api.fetchWorkspace).mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
