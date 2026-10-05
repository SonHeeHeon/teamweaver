import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import App from "./App";
import type { Meta, PlanEvent, WhatifResponse } from "./api/types";

// 실제 네트워크를 타지 않도록 클라이언트 모듈 전체를 목킹한다. postWhatif는
// 이 테스트가 언제 이행(resolve)할지 직접 통제해야 하므로 vi.fn으로 둔다
// (deferred 프라미스는 각 테스트 안에서 만든다).
vi.mock("./api/client", () => ({
  fetchMeta: vi.fn(),
  fetchSettings: vi.fn(),
  fetchActiveDataset: vi.fn(),
  fetchAdminStatus: vi.fn(async () => ({ token_required: false, login_required: false,
                                         protected: true, logged_in: false, expires_at: null })),
  adminLogin: vi.fn(),
  adminLogout: vi.fn(async () => {}),
  AdminLoginRequiredError: class extends Error {},
  DatasetChangedError: class extends Error {},
  uploadDataset: vi.fn(),
  resetDataset: vi.fn(),
  saveSettings: vi.fn(),
  SettingsConflictError: class extends Error {},
  streamOptimize: vi.fn(),
  postWhatif: vi.fn(),
  downloadReport: vi.fn(),
  applySwap: vi.fn(),
  loadPlanEdits: vi.fn(async () => ({ swaps: [], steps: [], updated_at: null, revision: 0 })),
  savePlanEdits: vi.fn(async () => ({ revision: 1 })),
  EditsConflictError: class extends Error {
    revision: number;
    constructor(r: number) { super("conflict"); this.revision = r; }
  },
}));

// NetworkGraph는 react-force-graph-2d를 통해 <canvas>를 그리는데 jsdom에는
// canvas 구현이 없다 -- NetworkGraph.test.tsx와 같은 방식으로 목킹한다.
vi.mock("react-force-graph-2d", () => ({
  default: () => <div data-testid="fg" />,
}));

import {
  fetchActiveDataset, fetchAdminStatus, fetchMeta, fetchSettings, saveSettings, streamOptimize,
  postWhatif, downloadReport, uploadDataset, DatasetChangedError, applySwap,
  loadPlanEdits, savePlanEdits, EditsConflictError, adminLogin, adminLogout, AdminLoginRequiredError,
} from "./api/client";
import type { SettingsResponse } from "./api/types";

const SETTINGS: SettingsResponse = {
  settings: { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
              time_limit: 120, gap: 0.05, max_concurrent_projects: 3, allocation_mode: "fixed" as const },
  defaults: { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
              time_limit: 120, gap: 0.05, max_concurrent_projects: 3, allocation_mode: "fixed" as const },
  bounds: { min_alloc: { min: 0.05, max: 1 }, clique_threshold_months: { min: 1, max: 24 },
            lam: { min: 0, max: 1 }, mu: { min: 0, max: 1 }, time_limit: { min: 5, max: 600 },
            gap: { min: 0, max: 0.2 }, max_concurrent_projects: { min: 1, max: 6 } },
  updated_at: null,
  load_error: null,
};

const FIXTURE_INFO = { dataset_id: "fixture-demo-100x20", version: "a".repeat(64),
                       source: "fixture" as const, synthetic: true, people: 3, projects: 1,
                       activated_at: "2026-10-05T00:00:00+00:00" };

const OPEN_ADMIN = { token_required: false, login_required: false, protected: true,
                     logged_in: false, expires_at: null };

beforeEach(() => {
  vi.mocked(fetchAdminStatus).mockReset().mockResolvedValue(OPEN_ADMIN);
  vi.mocked(fetchSettings).mockResolvedValue(SETTINGS);
  vi.mocked(fetchActiveDataset).mockResolvedValue(FIXTURE_INFO);
});

const META: Meta = {
  people: [
    { id: "p1", name: "김일번", grade: "중급", skills: {} },
    { id: "p2", name: "이이번", grade: "중급", skills: {} },
    { id: "p3", name: "박삼번", grade: "중급", skills: {} },
  ],
  projects: [
    { id: "j1", name: "프로젝트1", sector: "대내", phase: "실행",
      start_month: 0, end_month: 3, grade_headcount: { 중급: 1 }, monthly_budget: 1000 },
  ],
  skills: [],
  review_items: [],
  coworks: [],
  dataset_version: "a".repeat(64),
};

const PLAN_A: PlanEvent = {
  label: "A", entries: [{ person_id: "p1", project_id: "j1", alloc: 1 }],
  objective: 1, unfilled: [], fulfillment: 1, optimization_ratio: 1,
  index: 0, cached: false, plan_token: "tok-A",
};

const PLAN_B: PlanEvent = {
  label: "B", entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
  objective: 0.9, unfilled: [], fulfillment: 1, optimization_ratio: 0.9,
  index: 1, cached: false,
};

const ZERO_TERMS = { skill: 0, synergy: 0, overfamiliarity: 0, unfilled: 0, total: 0 };

const STALE_RESULT: WhatifResponse = {
  objective_delta: 0.5,
  before: ZERO_TERMS,
  after: { ...ZERO_TERMS, skill: 0.5, total: 0.5 },
  new_violations: [],
  new_shortfalls: [],
  feasible: true,
  briefing: {
    rationale: "이 문장은 Plan A 기준으로 계산된 것이라 Plan B 아래 보이면 안 된다",
    risks: ["stale risk"],
    alternatives: ["stale alternative"],
  },
  fallback_used: false,
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

describe("App — 플랜 전환 중 진행 중이던 what-if 응답", () => {
  it("이전 플랜 기준 브리핑이 새로 선택한 플랜 아래 표시되지 않는다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
      yield { event: "plan" as const, data: PLAN_B };
    })());
    const pending = deferred<WhatifResponse>();
    vi.mocked(postWhatif).mockReturnValue(pending.promise);

    render(<App />);

    // 최적화 실행 -> Plan A, Plan B 도착
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    await screen.findByText("Plan B");

    // Plan A가 선택된 상태에서 스왑을 시작한다 (out=p1/j1, in=p3)
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));

    expect(postWhatif).toHaveBeenCalledTimes(1);
    await screen.findByText("브리핑 생성 중…");

    // 응답이 오기 전에 Plan B로 전환한다
    fireEvent.click(screen.getByText("Plan B"));

    // 대기 중이던 postWhatif가 이제야 이행된다 -- Plan A 기준 결과다.
    await act(async () => {
      pending.resolve(STALE_RESULT);
      await Promise.resolve();
    });

    // Plan B 아래에는 stale 결과가 절대 보이면 안 된다.
    await waitFor(() => {
      expect(screen.queryByText(/이 문장은 Plan A 기준으로 계산된 것/)).not.toBeInTheDocument();
    });
    // busy 상태로 멈춰 있지도 않아야 한다 (finally 가드 확인).
    expect(screen.queryByText("브리핑 생성 중…")).not.toBeInTheDocument();
    expect(screen.getByText(/인력을 교체하면/)).toBeInTheDocument();
  });
});


describe("App — 배치 설정(K8)이 계산과 교체 검토에 같은 기준으로 쓰인다", () => {
  function planStream() {
    return (async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })();
  }

  it("최적화에 저장된 설정을 milp_params로 보내고, 설정을 바꾼 뒤에도 교체 검토·PDF는 계산 당시 설정을 쓴다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue(planStream());
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    const changed = { ...SETTINGS.settings, min_alloc: 0.5 };
    vi.mocked(saveSettings).mockResolvedValue({ ...SETTINGS, settings: changed,
                                                updated_at: "2026-10-05T00:00:00+00:00" });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    expect(vi.mocked(streamOptimize).mock.calls[0][0].milp_params).toEqual(SETTINGS.settings);
    expect(screen.queryByText(/이전 설정/)).not.toBeInTheDocument();

    // 관리자가 최소 투입률을 50%로 바꾼다.
    fireEvent.click(screen.getByRole("button", { name: "배치 설정" }));
    fireEvent.change(await screen.findByLabelText(/최소 투입률/), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(saveSettings).toHaveBeenCalledWith(changed, null, null));

    // 결과 화면에는 "이전 설정으로 계산됨" 안내가 뜬다.
    fireEvent.click(screen.getByRole("button", { name: "What-if 대시보드" }));
    expect(await screen.findByText(/바뀐 설정: 최소 투입률 30%→50%/)).toBeInTheDocument();

    // 교체 검토는 플랜을 계산한 30% 기준으로 보낸다(50%가 아니다).
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    await waitFor(() => expect(postWhatif).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postWhatif).mock.calls[0][3]).toEqual(SETTINGS.settings);

    fireEvent.click(await screen.findByRole("button", { name: "PDF 내려받기" }));
    await waitFor(() => expect(downloadReport).toHaveBeenCalled());
    expect(vi.mocked(downloadReport).mock.calls[0][3]).toEqual(SETTINGS.settings);
  });

  it("계산 후 가중치를 바꿔도 교체 검토는 계산 당시 가중치를 쓴다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue({ ...META, skills: ["Java"] });
    vi.mocked(streamOptimize).mockReset().mockReturnValue(planStream());
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.click(screen.getByRole("button", { name: "요건 설정" }));
    fireEvent.change(screen.getByRole("slider"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "What-if 대시보드" }));
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    await waitFor(() => expect(postWhatif).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postWhatif).mock.calls[0][2]).toEqual({});
  });

  it("실행할 때마다 서버 설정을 다시 읽어 다른 관리자가 바꾼 값도 반영한다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    const other = { ...SETTINGS.settings, min_alloc: 0.4 };
    vi.mocked(fetchSettings).mockReset()
      .mockResolvedValueOnce(SETTINGS)                                  // 페이지 열 때
      .mockResolvedValueOnce({ ...SETTINGS, settings: other });         // 실행 직전
    vi.mocked(streamOptimize).mockReset().mockReturnValue(planStream());

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    expect(vi.mocked(streamOptimize).mock.calls[0][0].milp_params).toEqual(other);
    expect(screen.queryByText(/이전 설정/)).not.toBeInTheDocument();
  });

  it("첫 설정 로딩이 끝나기 전에 실행해도 모델 기본값으로 새지 않는다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    const never = new Promise<SettingsResponse>(() => {});
    vi.mocked(fetchSettings).mockReset()
      .mockReturnValueOnce(never)                                       // 첫 로딩은 끝나지 않음
      .mockResolvedValueOnce(SETTINGS);
    vi.mocked(streamOptimize).mockReset().mockReturnValue(planStream());

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    expect(vi.mocked(streamOptimize).mock.calls[0][0].milp_params).toEqual(SETTINGS.settings);
  });

  it("설정을 못 불러오면 경고하고 milp_params 없이(모델 기본값) 계산한다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    vi.mocked(fetchSettings).mockRejectedValue(new Error("down"));
    vi.mocked(streamOptimize).mockReset().mockReturnValue(planStream());

    render(<App />);
    expect(await screen.findByText(/배치 설정을 불러오지 못했다/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    expect(vi.mocked(streamOptimize).mock.calls[0][0]).not.toHaveProperty("milp_params");
  });
});


describe("App — 데이터셋 전환(K9)", () => {
  it("업로드로 전환되면 이전 플랜·가중치를 비우고 meta를 새로 읽는다", async () => {
    const NEW_META = { ...META, people: [{ id: "q1", name: "새사람", grade: "중급", skills: {} }] };
    vi.mocked(fetchMeta).mockReset().mockResolvedValueOnce(META).mockResolvedValueOnce(NEW_META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    const uploaded = { ...FIXTURE_INFO, dataset_id: "synthetic-n1", version: "b".repeat(64),
                       source: "upload" as const, people: 1 };
    vi.mocked(uploadDataset).mockResolvedValue({
      activated: true, dataset: uploaded,
      report: { errors: [], warnings: [], notes: [], row_counts: {} } });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");

    fireEvent.click(screen.getByRole("button", { name: "데이터" }));
    const input = await screen.findByLabelText("묶음 zip 파일");
    fireEvent.change(input, { target: { files: [new File(["PK"], "b.zip")] } });
    fireEvent.click(screen.getByRole("button", { name: "검증 후 전환" }));
    expect(await screen.findByText(/이 데이터로 전환했다/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMeta).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/synthetic-n1/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "What-if 대시보드" }));
    expect(screen.queryByText("Plan A")).not.toBeInTheDocument();
  });

  it("최적화가 진행 중일 때 전환되면 이전 데이터셋의 남은 플랜을 버린다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    const gate = deferred<void>();
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
      await gate.promise;
      yield { event: "plan" as const, data: PLAN_B };
    })());
    vi.mocked(uploadDataset).mockResolvedValue({
      activated: true, dataset: { ...FIXTURE_INFO, version: "c".repeat(64), source: "upload" },
      report: { errors: [], warnings: [], notes: [], row_counts: {} } });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.click(screen.getByRole("button", { name: "데이터" }));
    fireEvent.change(await screen.findByLabelText("묶음 zip 파일"),
                     { target: { files: [new File(["PK"], "b.zip")] } });
    fireEvent.click(screen.getByRole("button", { name: "검증 후 전환" }));
    await screen.findByText(/이 데이터로 전환했다/);
    await act(async () => { gate.resolve(); await Promise.resolve(); });
    fireEvent.click(screen.getByRole("button", { name: "What-if 대시보드" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Plan B")).not.toBeInTheDocument();
  });
});


describe("App — 다른 사용자가 데이터셋을 바꾼 경우(K9 리뷰 반영)", () => {
  it("계산 요청에 화면이 본 dataset_version을 싣는다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    expect(vi.mocked(streamOptimize).mock.calls[0][0].dataset_version).toBe(META.dataset_version);
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    await waitFor(() => expect(postWhatif).toHaveBeenCalled());
    expect(vi.mocked(postWhatif).mock.calls[0][4]).toBe(META.dataset_version);
  });

  it("409(dataset_changed)를 받으면 새 데이터로 다시 불러오고 알린다", async () => {
    const NEW_META = { ...META, dataset_version: "b".repeat(64),
                       people: [{ id: "q1", name: "새사람", grade: "중급", skills: {} }] };
    vi.mocked(fetchMeta).mockReset().mockResolvedValueOnce(META).mockResolvedValueOnce(NEW_META);
    vi.mocked(fetchActiveDataset).mockReset()
      .mockResolvedValueOnce(FIXTURE_INFO)
      .mockResolvedValueOnce({ ...FIXTURE_INFO, version: "b".repeat(64), source: "upload" });
    const rejected = (async function* () {
      yield* [] as never[];                 // 스트림 시작 전에 서버가 409를 준 상황
      throw new DatasetChangedError("바뀌었다");
    });
    vi.mocked(streamOptimize).mockReset().mockImplementation(() => rejected());
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    expect(await screen.findByText(/다른 사용자가 데이터셋을 바꿨다/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMeta).toHaveBeenCalledTimes(2));
  });

  it("서버가 관리자 토큰을 요구하면 입력칸을 보여 주고 업로드에 싣는다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(fetchAdminStatus).mockResolvedValue({ ...OPEN_ADMIN, token_required: true });
    vi.mocked(uploadDataset).mockReset().mockResolvedValue({
      activated: false, detail: "x", report: null });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "데이터" }));
    fireEvent.change(await screen.findByLabelText(/관리자 토큰/), { target: { value: "s3cret" } });
    fireEvent.change(screen.getByLabelText("묶음 zip 파일"),
                     { target: { files: [new File(["PK"], "b.zip")] } });
    fireEvent.click(screen.getByRole("button", { name: "검증 후 전환" }));
    await waitFor(() => expect(uploadDataset).toHaveBeenCalled());
    expect(vi.mocked(uploadDataset).mock.calls[0][1]).toBe("s3cret");
  });
});

describe("App — 교체 적용(K10)", () => {
  const APPLIED = {
    entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
    evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
    objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
    optimization_ratio: 0.7, unfilled: [], warnings: [],
  };
  const CLEAN_REVIEW = { ...STALE_RESULT, briefing: { rationale: "검토 브리핑", risks: [],
                                                      alternatives: [] } };

  async function reviewSwap() {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(postWhatif).mockReset().mockResolvedValue(CLEAN_REVIEW);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    await screen.findByText("검토 브리핑");
  }

  it("적용하면 같은 기준으로 서버에 보내고, 명단·이력이 바뀌며 PDF에 실린다", async () => {
    vi.mocked(applySwap).mockReset().mockResolvedValue(APPLIED);
    await reviewSwap();
    fireEvent.click(screen.getByRole("button", { name: "이 교체 적용" }));
    expect(await screen.findByText(/교체 1건 적용/)).toBeInTheDocument();
    const args = vi.mocked(applySwap).mock.calls[0];
    expect(args[0]).toEqual(PLAN_A.entries);
    expect(args[1]).toEqual({ out_person_id: "p1", in_person_id: "p3", project_id: "j1" });
    expect(args[3]).toEqual(SETTINGS.settings);
    expect(args[4]).toBe(META.dataset_version);
    expect(screen.queryByText("검토 브리핑")).not.toBeInTheDocument();   // 검토 결과는 소비됨

    fireEvent.click(screen.getByRole("button", { name: "PDF 내려받기" }));
    await waitFor(() => expect(downloadReport).toHaveBeenCalled());
    const call = vi.mocked(downloadReport).mock.calls.at(-1)!;
    expect(call[0].entries).toEqual(APPLIED.entries);
    expect(call[0].objective).toBe(1.5);
    // PDF에는 원 명단과 교체 순서만 보낸다 -- 수치는 서버가 다시 계산한다.
    expect(call[5]?.base).toEqual(PLAN_A.entries);
    expect(call[5]?.swaps).toEqual([{ out_person_id: "p1", in_person_id: "p3", project_id: "j1" }]);
    expect(call[6]).toEqual({ weights: {}, planToken: "tok-A" });
  });

  it("마지막 적용 취소로 원래 명단으로 돌아간다", async () => {
    vi.mocked(applySwap).mockReset().mockResolvedValue(APPLIED);
    vi.mocked(downloadReport).mockReset();
    await reviewSwap();
    fireEvent.click(screen.getByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    fireEvent.click(screen.getByRole("button", { name: "마지막 적용 취소" }));
    expect(screen.queryByText(/교체 1건 적용/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "PDF 내려받기" }));
    await waitFor(() => expect(downloadReport).toHaveBeenCalled());
    const call = vi.mocked(downloadReport).mock.calls.at(-1)!;
    expect(call[0].entries).toEqual(PLAN_A.entries);
    expect(call[5]).toBeNull();
  });

  it("두 번 적용 후 한 번 취소하면 첫 적용 뒤 명단으로 돌아간다", async () => {
    const SECOND = { ...APPLIED, entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
                     objective: 2.0, warnings: [] };
    vi.mocked(applySwap).mockReset().mockResolvedValueOnce(APPLIED).mockResolvedValueOnce(SECOND);
    vi.mocked(downloadReport).mockReset();
    await reviewSwap();
    fireEvent.click(screen.getByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p3::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p2" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    await screen.findByText("검토 브리핑");
    fireEvent.click(screen.getByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 2건 적용/);
    fireEvent.click(screen.getByRole("button", { name: "마지막 적용 취소" }));
    await screen.findByText(/교체 1건 적용/);
    fireEvent.click(screen.getByRole("button", { name: "PDF 내려받기" }));
    await waitFor(() => expect(downloadReport).toHaveBeenCalled());
    const call = vi.mocked(downloadReport).mock.calls.at(-1)!;
    expect(call[0].entries).toEqual(APPLIED.entries);
    expect(call[0].objective).toBe(1.5);
    expect(call[5]?.swaps).toHaveLength(1);
    expect(call[5]?.base).toEqual(PLAN_A.entries);
  });

  it("계산 뒤 설정을 바꿔도 적용은 계산 당시 설정으로 한다", async () => {
    vi.mocked(applySwap).mockReset().mockResolvedValue(APPLIED);
    const changed = { ...SETTINGS.settings, min_alloc: 0.5 };
    vi.mocked(saveSettings).mockReset().mockResolvedValue(
      { ...SETTINGS, settings: changed, updated_at: "2026-10-05T01:00:00+00:00" });
    await reviewSwap();
    fireEvent.click(screen.getByRole("button", { name: "배치 설정" }));
    fireEvent.change(await screen.findByLabelText(/최소 투입률/), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "What-if 대시보드" }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    expect(vi.mocked(applySwap).mock.calls[0][3]).toEqual(SETTINGS.settings);
  });

  it("검토한 뒤 교체 선택을 바꾸면 이전 검토 결과는 적용할 수 없다", async () => {
    vi.mocked(applySwap).mockReset().mockResolvedValue(APPLIED);
    await reviewSwap();
    expect(screen.getByText("검토한 교체: 김일번 → 박삼번 (j1)")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p2" } });
    expect(screen.queryByRole("button", { name: "이 교체 적용" })).not.toBeInTheDocument();
    expect(screen.queryByText("검토 브리핑")).not.toBeInTheDocument();
    expect(applySwap).not.toHaveBeenCalled();
  });

  it("적용 중 다른 플랜으로 가면 늦은 응답을 버리고 버튼 잠금도 풀린다", async () => {
    const pending = deferred<typeof APPLIED>();
    vi.mocked(applySwap).mockReset().mockReturnValue(pending.promise);
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
      yield { event: "plan" as const, data: PLAN_B };
    })());
    vi.mocked(postWhatif).mockReset().mockResolvedValue(CLEAN_REVIEW);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan B");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    await screen.findByText("검토 브리핑");
    fireEvent.click(screen.getByRole("button", { name: "이 교체 적용" }));
    await screen.findByRole("button", { name: "적용 중…" });
    fireEvent.click(screen.getByText("Plan B"));
    await act(async () => { pending.resolve(APPLIED); await Promise.resolve(); });
    fireEvent.click(screen.getByText("Plan A"));
    expect(screen.queryByText(/교체 1건 적용/)).not.toBeInTheDocument();   // 늦은 응답은 버려짐
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    expect(await screen.findByRole("button", { name: "이 교체 적용" })).toBeEnabled();
  });

  it("다음 검토는 적용된 명단을 기준으로 한다", async () => {
    vi.mocked(applySwap).mockReset().mockResolvedValue(APPLIED);
    await reviewSwap();
    fireEvent.click(screen.getByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p3::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p2" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    await waitFor(() => expect(postWhatif).toHaveBeenCalledTimes(2));
    expect(vi.mocked(postWhatif).mock.calls[1][0]).toEqual(APPLIED.entries);
  });
});


describe("App — 적용 교체 저장·복원(K13)", () => {
  const STEP = {
    entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
    evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
    objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
    optimization_ratio: 0.7, unfilled: [], warnings: [],
    swap: { out_person_id: "p1", in_person_id: "p3", project_id: "j1" },
  };

  it("최적화 결과가 오면 저장해 둔 교체를 찾아 명단을 복원한다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue(
      { swaps: [STEP.swap], steps: [STEP], updated_at: null, revision: 1 });
    vi.mocked(downloadReport).mockReset();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    expect(await screen.findByText(/저장해 둔 적용 교체 1건을 불러왔다/)).toBeInTheDocument();
    expect(loadPlanEdits).toHaveBeenCalledWith("tok-A");
    expect(screen.getByText(/교체 1건 적용/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "PDF 내려받기" }));
    await waitFor(() => expect(downloadReport).toHaveBeenCalled());
    expect(vi.mocked(downloadReport).mock.calls.at(-1)![0].entries).toEqual(STEP.entries);
  });

  it("적용·취소할 때마다 원 플랜과 교체 목록을 저장한다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue({ swaps: [], steps: [], updated_at: null, revision: 0 });
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    vi.mocked(savePlanEdits).mockReset().mockResolvedValue({ revision: 1 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    await waitFor(() => expect(savePlanEdits).toHaveBeenCalledTimes(1));
    const [token, body] = vi.mocked(savePlanEdits).mock.calls[0];
    expect(token).toBe("tok-A");
    expect(body).toMatchObject({ plan_label: "A", base_entries: PLAN_A.entries,
                                 dataset_version: META.dataset_version, milp_params: SETTINGS.settings,
                                 swaps: [STEP.swap] });
    fireEvent.click(screen.getByRole("button", { name: "마지막 적용 취소" }));
    await waitFor(() => expect(savePlanEdits).toHaveBeenCalledTimes(2));
    expect(vi.mocked(savePlanEdits).mock.calls[1][1].swaps).toEqual([]);
  });

  it("저장에 실패하면 새로고침 때 사라질 수 있다고 알린다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue({ swaps: [], steps: [], updated_at: null, revision: 0 });
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    vi.mocked(savePlanEdits).mockReset().mockRejectedValue(new Error("disk full"));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    expect(await screen.findByText(/서버에 저장하지 못했다/)).toBeInTheDocument();
  });
});


describe("App — 저장 요청 순서(K13 리뷰 M1)", () => {
  it("적용 직후 취소해도 저장 요청은 줄 서서 나가고 revision이 커진다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue({ swaps: [], steps: [], updated_at: null, revision: 0 });
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    const first = deferred<{ revision: number }>();
    vi.mocked(savePlanEdits).mockReset()
      .mockReturnValueOnce(first.promise).mockResolvedValue({ revision: 1 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    fireEvent.click(screen.getByRole("button", { name: "원래 플랜으로" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(savePlanEdits).toHaveBeenCalledTimes(1);          // 취소 저장은 앞 요청을 기다린다
    await act(async () => { first.resolve({ revision: 1 }); await Promise.resolve(); });
    await waitFor(() => expect(savePlanEdits).toHaveBeenCalledTimes(2));
    const [a, b] = vi.mocked(savePlanEdits).mock.calls.map((c) => c[1]);
    expect(a.swaps).toHaveLength(1);
    expect(b.swaps).toEqual([]);
    expect(a.expected_revision).toBe(0);
    expect(b.expected_revision).toBe(1);          // 앞 저장의 응답으로 받은 서버 번호
  });
});


describe("App — 복원과 로컬 적용의 경합(K13 리뷰 S1)", () => {
  it("저장분 조회가 늦게 와도 그사이 사용자가 적용한 명단을 덮지 않는다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    const slow = deferred<any>();
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockReturnValue(slow.promise);
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    vi.mocked(savePlanEdits).mockReset().mockResolvedValue({ revision: 1 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    const old = { ...STEP, entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
                  swap: { out_person_id: "p1", in_person_id: "p2", project_id: "j1" } };
    await act(async () => {
      slow.resolve({ swaps: [old.swap, old.swap], steps: [old, old], updated_at: null, revision: 2 });
      await Promise.resolve();
    });
    expect(screen.queryByText(/교체 2건 적용/)).not.toBeInTheDocument();
    expect(screen.getByText(/교체 1건 적용/)).toBeInTheDocument();
  });
});


describe("App — 다른 화면이 먼저 저장(서버 revision, Codex 3차)", () => {
  it("저장이 409(edits_changed)면 서버의 최신 저장분으로 화면을 맞추고 알린다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    const other = { ...STEP, entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
                    swap: { out_person_id: "p1", in_person_id: "p2", project_id: "j1" } };
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset()
      .mockResolvedValueOnce({ swaps: [], steps: [], updated_at: null, revision: 0 })
      .mockResolvedValue({ swaps: [other.swap, other.swap], steps: [other, other], updated_at: null,
                           revision: 3 });
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    vi.mocked(savePlanEdits).mockReset().mockRejectedValue(new EditsConflictError(3));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    expect(await screen.findByText(/다른 화면에서 먼저 저장해/)).toBeInTheDocument();
    expect(screen.getByText(/교체 2건 적용/)).toBeInTheDocument();
  });

  it("늦게 온 복원은 그사이 적용 후 취소한 상태를 되살리지 않는다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    const slow = deferred<any>();
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockReturnValue(slow.promise);
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    vi.mocked(savePlanEdits).mockReset().mockResolvedValue({ revision: 1 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    fireEvent.click(screen.getByRole("button", { name: "원래 플랜으로" }));
    const old = { ...STEP, swap: { out_person_id: "p1", in_person_id: "p3", project_id: "j1" } };
    await act(async () => {
      slow.resolve({ swaps: [old.swap], steps: [old], updated_at: null, revision: 1 });
      await Promise.resolve();
    });
    expect(screen.queryByText(/교체 1건 적용/)).not.toBeInTheDocument();
  });
});

describe("App — 적용 중 복원 도착(K13 리뷰 S-a)", () => {
  it("적용 응답이 오기 전에 저장분이 복원되면 옛 명단 기준 적용 결과는 버린다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    const restore = deferred<any>();
    const apply = deferred<any>();
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockReturnValue(restore.promise);
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockReturnValue(apply.promise);
    vi.mocked(savePlanEdits).mockReset().mockResolvedValue({ revision: 1 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    const saved = { ...STEP, entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
                    swap: { out_person_id: "p1", in_person_id: "p2", project_id: "j1" } };
    await act(async () => {
      restore.resolve({ swaps: [saved.swap], steps: [saved], updated_at: null, revision: 1 });
      await Promise.resolve();
    });
    await screen.findByText(/저장해 둔 적용 교체 1건을 불러왔다/);
    await act(async () => { apply.resolve({ ...STEP }); await Promise.resolve(); });
    // 복원이 도착하는 순간 진행 중이던 적용·검토를 무효화했다 -- 늦은 적용 결과는 쌓이지 않는다.
    expect(screen.queryByText(/교체 2건 적용/)).not.toBeInTheDocument();
    expect(screen.getByText(/교체 1건 적용/)).toBeInTheDocument();
    expect(savePlanEdits).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /^이 교체 적용/ })).not.toBeInTheDocument();
  });
});


describe("App — 재실행 뒤 revision이 섞이지 않는다(Opus 검증 M1)", () => {
  it("새 계산(다른 plan_token)의 첫 저장은 옛 플랜의 revision이 아니라 0을 보낸다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    const PLAN_A2 = { ...PLAN_A, plan_token: "tok-B" };
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset()
      .mockReturnValueOnce((async function* () { yield { event: "plan" as const, data: PLAN_A }; })())
      .mockReturnValueOnce((async function* () { yield { event: "plan" as const, data: PLAN_A2 }; })());
    const pendingB = deferred<any>();
    vi.mocked(loadPlanEdits).mockReset().mockImplementation(async (tok: string) =>
      tok === "tok-A" ? { swaps: [], steps: [], updated_at: null, revision: 3 } : pendingB.promise);
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    vi.mocked(savePlanEdits).mockReset().mockResolvedValue({ revision: 1 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    await waitFor(() => expect(loadPlanEdits).toHaveBeenCalledWith("tok-A"));
    fireEvent.click(screen.getByRole("button", { name: "요건 설정" }));
    fireEvent.click(screen.getByRole("button", { name: "최적화 실행" }));
    await waitFor(() => expect(loadPlanEdits).toHaveBeenCalledWith("tok-B"));
    fireEvent.change(await screen.findByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await waitFor(() => expect(savePlanEdits).toHaveBeenCalled());
    const [tok, body] = vi.mocked(savePlanEdits).mock.calls[0];
    expect(tok).toBe("tok-B");
    expect(body.expected_revision).toBe(0);
  });
});


describe("App — 충돌 뒤 줄 선 저장은 버린다(Opus 검증 S1)", () => {
  it("앞 저장이 409면 그 뒤에 줄 선 저장은 보내지 않고 서버 상태로 맞춘다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    let rejectFirst!: (e: unknown) => void;
    const first = { promise: new Promise<any>((_, rej) => { rejectFirst = rej; }) };
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue(
      { swaps: [], steps: [], updated_at: null, revision: 0 });
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValue({ ...STEP });
    vi.mocked(savePlanEdits).mockReset().mockReturnValueOnce(first.promise)
      .mockResolvedValue({ revision: 9 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    fireEvent.click(screen.getByRole("button", { name: "원래 플랜으로" }));   // 줄 선 두 번째 저장
    await act(async () => { rejectFirst(new EditsConflictError(4)); await Promise.resolve(); });
    await waitFor(() => expect(loadPlanEdits).toHaveBeenCalledTimes(2));   // 강제 복원
    await new Promise((r) => setTimeout(r, 20));
    expect(savePlanEdits).toHaveBeenCalledTimes(1);                        // 두 번째는 버렸다
  });
});


describe("App — 409 뒤 서버 상태로 맞추기 전에 한 적용(K13 남은 SHOULD)", () => {
  it("강제 복원이 끝나기 전에 적용한 옛 이력 기준 저장은 서버로 보내지 않는다", async () => {
    const STEP = {
      entries: [{ person_id: "p3", project_id: "j1", alloc: 1 }],
      evaluation: { objective: ZERO_TERMS, violations: [], shortfalls: [] },
      objective_delta: 0.5, feasible: true, objective: 1.5, fulfillment: 0.8,
      optimization_ratio: 0.7, unfilled: [], warnings: [],
    };
    const other = { ...STEP, entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
                    swap: { out_person_id: "p1", in_person_id: "p2", project_id: "j1" } };
    const restore = deferred<any>();
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
    })());
    vi.mocked(loadPlanEdits).mockReset()
      .mockResolvedValueOnce({ swaps: [], steps: [], updated_at: null, revision: 0 })
      .mockReturnValueOnce(restore.promise);
    vi.mocked(postWhatif).mockReset().mockResolvedValue(STALE_RESULT);
    vi.mocked(applySwap).mockReset().mockResolvedValueOnce({ ...STEP })
      .mockResolvedValue({ ...STEP, entries: [{ person_id: "p1", project_id: "j1", alloc: 1 }] });
    vi.mocked(savePlanEdits).mockReset().mockRejectedValueOnce(new EditsConflictError(3))
      .mockResolvedValue({ revision: 4 });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 1건 적용/);
    await waitFor(() => expect(loadPlanEdits).toHaveBeenCalledTimes(2));   // 409 → 강제 복원 대기 중
    // 복원이 끝나기 전에 한 번 더 검토·적용한다(화면에는 아직 옛 이력 1건이 보인다).
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p3::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p1" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));
    fireEvent.click(await screen.findByRole("button", { name: "이 교체 적용" }));
    await screen.findByText(/교체 2건 적용/);
    await act(async () => {
      restore.resolve({ swaps: [other.swap], steps: [other], updated_at: null, revision: 3 });
      await Promise.resolve();
    });
    expect(await screen.findByText(/다른 화면에서 먼저 저장해/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    // 첫 저장(409)만 나갔다 -- 옛 이력 + 새 교체로 서버의 최신 저장분을 덮지 않는다.
    expect(savePlanEdits).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/교체 1건 적용/)).toBeInTheDocument();     // 서버 상태(다른 화면의 1건)
  });
});


describe("App — 대안 부족 안내(C2)", () => {
  it("요청한 대안보다 적게 오면 '조건을 만족하는 대안 없음'을 알린다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue(
      { swaps: [], steps: [], updated_at: null, revision: 0 });
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
      yield { event: "done" as const, data: { count: 1, requested_alternatives: 3 } };
    })());
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    expect(await screen.findByText(/조건을 만족하는 대안 없음/)).toBeInTheDocument();
  });

  it("솔버가 시간 안에 못 끝내 끊겼으면 조건 미충족이 아니라 그 사실을 알린다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue(
      { swaps: [], steps: [], updated_at: null, revision: 0 });
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
      yield { event: "done" as const,
              data: { count: 1, requested_alternatives: 3, stop_reason: "time_limit" } };
    })());
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    expect(await screen.findByText(/시간 안에 끝나지 않아/)).toBeInTheDocument();
    expect(screen.queryByText(/조건을 만족하는 대안 없음/)).not.toBeInTheDocument();
  });

  it("요청한 만큼 오면 안내하지 않는다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(loadPlanEdits).mockReset().mockResolvedValue(
      { swaps: [], steps: [], updated_at: null, revision: 0 });
    vi.mocked(streamOptimize).mockReset().mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
      yield { event: "done" as const, data: { count: 1, requested_alternatives: 0 } };
    })());
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    expect(screen.queryByText(/조건을 만족하는 대안/)).not.toBeInTheDocument();
  });
});


describe("App — 관리자 로그인(K14)", () => {
  const LOCKED = { token_required: false, login_required: true, protected: true,
                   logged_in: false, expires_at: null };
  const IN = { ...LOCKED, logged_in: true, expires_at: 9999999999 };

  it("로그인 전에는 배치 설정·데이터 탭이 로그인 화면이고, 로그인하면 내용이 보인다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    let loggedIn = false;
    vi.mocked(fetchAdminStatus).mockReset().mockImplementation(async () => (loggedIn ? IN : LOCKED));
    vi.mocked(adminLogin).mockReset().mockImplementation(async () => { loggedIn = true; });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "관리자 로그인" }));
    expect(await screen.findByRole("heading", { name: "관리자 로그인" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "데이터" }));
    expect(screen.getByRole("heading", { name: "관리자 로그인" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "로그인" }));
    expect(await screen.findByRole("button", { name: "로그아웃" })).toBeInTheDocument();
    expect(screen.getByLabelText("묶음 zip 파일")).toBeInTheDocument();
  });

  it("요건 설정·결과 화면은 로그인 없이 쓴다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(fetchAdminStatus).mockReset().mockResolvedValue(LOCKED);
    render(<App />);
    expect(await screen.findByRole("button", { name: "최적화 실행" })).toBeInTheDocument();
  });

  it("로그아웃하면 다시 로그인 화면이 된다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    let loggedIn = true;
    vi.mocked(fetchAdminStatus).mockReset().mockImplementation(async () => (loggedIn ? IN : LOCKED));
    vi.mocked(adminLogout).mockReset().mockImplementation(async () => { loggedIn = false; });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "로그아웃" }));
    await waitFor(() => expect(adminLogout).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: "관리자 로그인" }));
    expect(await screen.findByRole("heading", { name: "관리자 로그인" })).toBeInTheDocument();
  });

  it("세션이 끝나 저장이 401이면 로그인 화면으로 보낸다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    let expired = false;
    vi.mocked(fetchAdminStatus).mockReset().mockImplementation(async () => (expired ? LOCKED : IN));
    vi.mocked(saveSettings).mockReset().mockImplementation(async () => {
      expired = true;
      throw new AdminLoginRequiredError("x");
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "배치 설정" }));
    fireEvent.change(await screen.findByLabelText(/최소 투입률/), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText(/세션이 끝났다/)).toBeInTheDocument();
    // 입력하던 값은 남아 있다(폼을 언마운트하지 않는다).
    expect((screen.getByLabelText(/최소 투입률/) as HTMLInputElement).value).toBe("40");
  });

  it("관리자 보호가 없는 서버면 경고를 띄운다", async () => {
    vi.mocked(fetchMeta).mockReset().mockResolvedValue(META);
    vi.mocked(fetchAdminStatus).mockReset().mockResolvedValue({ ...LOCKED, login_required: false,
                                                                 protected: false });
    render(<App />);
    expect(await screen.findByText(/관리자 비밀번호 미설정/)).toBeInTheDocument();
  });
});
