import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  DatasetChangedError: class extends Error {},
  fetchOperatingState: vi.fn(), streamOperatingCompare: vi.fn(), postBaseline: vi.fn(),
  postStaffingCandidates: vi.fn(), postStaffingSimulate: vi.fn(), postStaffingBest: vi.fn(),
}));
import {
  fetchOperatingState, postStaffingBest, postStaffingCandidates, postStaffingSimulate, streamOperatingCompare,
} from "../api/client";
import { OperatingTab } from "./OperatingTab";
import type { Meta, OperatingEvent, OperatingState } from "../api/types";

const META = {
  people: [{ id: "p0", name: "김영", grade: "중급", skills: {} }, { id: "p1", name: "이일", grade: "중급", skills: {} },
           { id: "p3", name: "박삼", grade: "고급", skills: {} }],
  projects: [{ id: "P1", name: "진행", sector: "x", phase: "x", start_month: 0, end_month: 5, grade_headcount: {}, monthly_budget: 1 },
             { id: "P2", name: "신규", sector: "x", phase: "x", start_month: 1, end_month: 5, grade_headcount: {}, monthly_budget: 1 }],
  skills: [], review_items: [], coworks: [], dataset_version: "v1",
} as unknown as Meta;

const STATE: OperatingState = {
  dataset_version: "v1", available: true, scenario: "operating", bench: ["p3"], proposals: ["P2"],
  current: [{ person_id: "p0", project_id: "P1", alloc: 1, locked: true },
            { person_id: "p1", project_id: "P1", alloc: 1, locked: false }],
  projects: [{ id: "P1", name: "진행", grade_headcount: {}, monthly_budget: 1, start_month: 0, end_month: 5 },
             { id: "P2", name: "신규", grade_headcount: {}, monthly_budget: 1, start_month: 1, end_month: 5 }],
  expected_s_100: { "0": 0.5, "1": 1.2, "2": 11, "3": 28 }, n_people: 3,
  note: "계산상 개선(같은 평가 기준) -- 사업 효과는 검증 전(NOT_CALIBRATED)", hint: null,
};

const parts = (t: number) => ({ total: t, skill: t, synergy: 0, overfamiliarity: 0, unfilled: 0 });

describe("OperatingTab", () => {
  beforeEach(() => { vi.mocked(fetchOperatingState).mockResolvedValue(STATE); });

  it("운영 중 데이터가 아니면 안내만 보인다", async () => {
    vi.mocked(fetchOperatingState).mockResolvedValue({ ...STATE, available: false, hint: "현재 배치가 없는 데이터다." });
    render(<OperatingTab meta={META} params={null} onDatasetChanged={vi.fn()} />);
    expect(await screen.findByText("현재 배치가 없는 데이터다.")).toBeInTheDocument();
  });

  it("K별 비교: 빈자리와 배치 품질을 나눠 보이고 이동 그림을 그린다", async () => {
    vi.mocked(streamOperatingCompare).mockReturnValue((async function* (): AsyncGenerator<OperatingEvent> {
      yield { event: "start" as const, data: { ks: [0, 1], n_people: 3, expected_s_100: {}, note: "" } };
      yield { event: "row" as const, data: { k: 0, elapsed_s: 0.5, accepted: true, termination: "Optimal", objective: -190,
        best_bound: -190, quality: 10, unfilled_seats: 2, quality_gain_vs_k0: 0, quality_gain_pct_vs_k0: 0,
        unfilled_change_vs_k0: 0, diff: { kept: 2, moved: [], joined: [] }, project_change_vs_k0: {}, violations: [], entries: [] } };
      yield { event: "row" as const, data: { k: 1, elapsed_s: 1.2, accepted: true, termination: "Optimal", objective: -88,
        best_bound: -88, quality: 12, unfilled_seats: 1, quality_gain_vs_k0: 2, quality_gain_pct_vs_k0: 20,
        unfilled_change_vs_k0: -1,
        diff: { kept: 1, moved: [{ person_id: "p1", from: "P1", to: ["P2"] }],
                joined: [{ person_id: "p3", project_id: "P1", from_bench: true }] },
        project_change_vs_k0: { P2: 1.5 }, violations: [], entries: [] } };
      yield { event: "done" as const, data: { elapsed_s: 2, count: 2 } };
    })());
    render(<OperatingTab meta={META} params={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "K별 비교 실행" }));
    expect(await screen.findByText("+2.00 (+20.0%)")).toBeInTheDocument();
    expect(screen.getByText("1 (-1)")).toBeInTheDocument();
    expect(vi.mocked(streamOperatingCompare).mock.calls[0][0]).toMatchObject({ dataset_version: "v1", ks: [0, 1, 2, 3] });
    fireEvent.click(screen.getByRole("button", { name: "K=1 이동 보기" }));
    expect(screen.getByText(/진행\(P1\) → 신규\(P2\)/)).toBeInTheDocument();
    expect(screen.getByText(/\(대기 인력\)/)).toBeInTheDocument();
    expect(screen.getByText("+1.50")).toBeInTheDocument();
  });

  it("보강: 후보를 넣어 보고 잠긴 사람은 뺄 수 없으며, 재평가에 등급 정원·예산을 함께 보낸다", async () => {
    vi.mocked(postStaffingCandidates).mockResolvedValue({ note: "", candidates: [{
      person_id: "p3", grade: "고급", alloc: 1, source: "bench", pulled_from: [], delta_total: 1.25,
      delta: { ...parts(1.25), overfamiliarity: -0.2 }, skill_fit: 0.8, team_synergy: 0.3, project_total_after: 3,
      monthly_cost: 1200.4, budget_added: 1201, new_violations: [] }] });
    vi.mocked(postStaffingSimulate).mockResolvedValue({ before: parts(1), after: parts(2), delta: parts(1),
      violations: [], new_violations: [], entries: [], note: "" });
    render(<OperatingTab meta={META} params={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "후보 보기" }));
    expect(await screen.findByText("+1.25")).toBeInTheDocument();
    expect(screen.getByText(/김영\(p0\) · 투입 100%/).textContent).toContain("(잠김)");
    fireEvent.click(screen.getByRole("button", { name: "박삼(p3) 넣어 보기" }));
    fireEvent.click(screen.getByRole("button", { name: "이일(p1) 빼 보기" }));     // p1(잠기지 않음)
    fireEvent.click(screen.getByRole("button", { name: "재평가" }));
    await waitFor(() => expect(postStaffingSimulate).toHaveBeenCalled());
    expect(vi.mocked(postStaffingSimulate).mock.calls[0][1]).toEqual({
      project_id: "P1", adds: [{ person_id: "p3", project_id: "P1", alloc: 1 }], removes: [["p1", "P1"]],
      extra_seats: { "고급": 1 }, budget_add: 1201 });
    expect(await screen.findByText("변화(계산상)")).toBeInTheDocument();
  });

  it("최선 n명: 결과 이동과 추가 비용을 보인다", async () => {
    vi.mocked(postStaffingBest).mockResolvedValue({ accepted: true, termination: "Optimal", added_cost: 2400,
      diff: { kept: 2, moved: [], joined: [{ person_id: "p3", project_id: "P1", from_bench: true }] },
      before: parts(1), after: parts(3), delta: parts(2), violations: [], entries: [], note: "" });
    render(<OperatingTab meta={META} params={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "최선 조합 계산" }));
    expect(await screen.findByText(/추가 월 비용 2,400/)).toBeInTheDocument();
    expect(vi.mocked(postStaffingBest).mock.calls[0][1]).toMatchObject({ project_id: "P1", n: 1, pull_budget: 0 });
  });
});


describe("OperatingTab — 리뷰 반영", () => {
  beforeEach(() => { vi.mocked(fetchOperatingState).mockResolvedValue(STATE); });

  it("빼 오기 후보를 넣었다 취소하면 그 빼기도 함께 사라진다(보이지 않는 빼기가 남지 않는다)", async () => {
    vi.mocked(postStaffingCandidates).mockResolvedValue({ note: "", candidates: [{
      person_id: "p3", grade: "고급", alloc: 1, source: "pull", pulled_from: ["P2"], delta_total: 0.5,
      delta: parts(0.5), skill_fit: 0.8, team_synergy: 0, project_total_after: 1, monthly_cost: 100,
      budget_added: 100, new_violations: [], unfilled_seats_delta: 1 }] });
    vi.mocked(postStaffingSimulate).mockReset().mockResolvedValue({ before: parts(1), after: parts(1), delta: parts(0),
      violations: [], new_violations: [], entries: [], note: "" });
    render(<OperatingTab meta={META} params={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "후보 보기" }));
    expect(await screen.findByText("-1")).toBeInTheDocument();                 // 빼 오면 원래 사업에 빈자리 1
    fireEvent.click(screen.getByRole("button", { name: "박삼(p3) 넣어 보기" }));
    expect(screen.getByText(/신규\(P2\)에서 빼 옴/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "박삼(p3) 넣기 취소" }));
    fireEvent.click(screen.getByRole("button", { name: "이일(p1) 빼 보기" }));
    fireEvent.click(screen.getByRole("button", { name: "재평가" }));
    await waitFor(() => expect(postStaffingSimulate).toHaveBeenCalled());
    expect(vi.mocked(postStaffingSimulate).mock.calls[0][1].removes).toEqual([["p1", "P1"]]);
  });

  it("직전 K의 해를 유지한 행에는 '최선 증명'을 띄우지 않는다", async () => {
    vi.mocked(streamOperatingCompare).mockReturnValue((async function* (): AsyncGenerator<OperatingEvent> {
      yield { event: "row", data: { k: 2, elapsed_s: 600, accepted: true, termination: "Optimal", objective: 5, best_bound: 5,
        quality: 5, unfilled_seats: 0, carried_from_k: 1, own: { termination: "time_limit_incumbent", objective: 4 },
        diff: { kept: 2, moved: [], joined: [] }, project_change_vs_k0: {}, violations: [], entries: [] } };
      yield { event: "done", data: { elapsed_s: 600, count: 1 } };
    })());
    render(<OperatingTab meta={META} params={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "K별 비교 실행" }));
    expect(await screen.findByText(/K=2 시간 한도로 더 나은 해를 찾지 못함 · K=1 해 유지/)).toBeInTheDocument();
    expect(screen.queryByText("최선 증명")).not.toBeInTheDocument();
  });

  it("미리 계산 행이면 시간 칸을 '미리 계산 때'로 밝히고, 다시 계산은 fresh로 다시 푼다", async () => {
    const row = { k: 0, elapsed_s: 0.4, accepted: true, termination: "Optimal", objective: 5, best_bound: 5,
      quality: 5, unfilled_seats: 0, diff: { kept: 2, moved: [], joined: [] }, project_change_vs_k0: {}, violations: [],
      entries: [], precomputed_at: "2026-10-06T14:40:50+00:00" };
    vi.mocked(streamOperatingCompare).mockReset().mockImplementation(() => (async function* (): AsyncGenerator<OperatingEvent> {
      yield { event: "start", data: { ks: [0], n_people: 3, expected_s_100: {}, note: "", precomputed_at: row.precomputed_at } };
      yield { event: "row", data: row };
      yield { event: "done", data: { elapsed_s: 0, count: 1, precomputed_at: row.precomputed_at } };
    })());
    render(<OperatingTab meta={META} params={null} onDatasetChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "K별 비교 실행" }));
    expect(await screen.findByText("(미리 계산 때)")).toBeInTheDocument();
    expect(screen.getAllByRole("status").some((el) => /미리 계산.*해 둔 것이다/.test(el.textContent ?? ""))).toBe(true);
    expect(vi.mocked(streamOperatingCompare).mock.calls[0][0].fresh).toBeUndefined();   // 버튼 이벤트로 켜지지 않는다
    fireEvent.click(screen.getByRole("button", { name: "다시 계산" }));
    await waitFor(() => expect(streamOperatingCompare).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamOperatingCompare).mock.calls[1][0].fresh).toBe(true);
  });
});
