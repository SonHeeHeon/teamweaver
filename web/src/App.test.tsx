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
  fetchAdminStatus: vi.fn(async () => ({ token_required: false })),
  DatasetChangedError: class extends Error {},
  uploadDataset: vi.fn(),
  resetDataset: vi.fn(),
  saveSettings: vi.fn(),
  SettingsConflictError: class extends Error {},
  streamOptimize: vi.fn(),
  postWhatif: vi.fn(),
  downloadReport: vi.fn(),
}));

// NetworkGraph는 react-force-graph-2d를 통해 <canvas>를 그리는데 jsdom에는
// canvas 구현이 없다 -- NetworkGraph.test.tsx와 같은 방식으로 목킹한다.
vi.mock("react-force-graph-2d", () => ({
  default: () => <div data-testid="fg" />,
}));

import {
  fetchActiveDataset, fetchAdminStatus, fetchMeta, fetchSettings, saveSettings, streamOptimize,
  postWhatif, downloadReport, uploadDataset, DatasetChangedError,
} from "./api/client";
import type { SettingsResponse } from "./api/types";

const SETTINGS: SettingsResponse = {
  settings: { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
              time_limit: 120, gap: 0.05 },
  defaults: { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
              time_limit: 120, gap: 0.05 },
  bounds: { min_alloc: { min: 0.05, max: 1 }, clique_threshold_months: { min: 1, max: 24 },
            lam: { min: 0, max: 1 }, mu: { min: 0, max: 1 }, time_limit: { min: 5, max: 600 },
            gap: { min: 0, max: 0.2 } },
  updated_at: null,
  load_error: null,
};

const FIXTURE_INFO = { dataset_id: "fixture-demo-100x20", version: "a".repeat(64),
                       source: "fixture" as const, synthetic: true, people: 3, projects: 1,
                       activated_at: "2026-10-05T00:00:00+00:00" };

beforeEach(() => {
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
  index: 0, cached: false,
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
    vi.mocked(fetchAdminStatus).mockResolvedValueOnce({ token_required: true });
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
