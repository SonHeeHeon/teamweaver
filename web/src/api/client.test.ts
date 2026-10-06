import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { applySwap, downloadReport, fetchMeta, postWhatif, saveSettings, SettingsConflictError, uploadDataset } from "./client";
import type { PlanEvent, WhatifResponse } from "./types";

describe("downloadReport", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("What-if 경고를 PDF 요청에 함께 싣는다", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, blob: async () => new Blob() }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    const plan: PlanEvent = { label: "A", entries: [], objective: 1, unfilled: [],
                              fulfillment: 1, optimization_ratio: 1, index: 0, cached: false };
    const zero = { skill: 0, synergy: 0, overfamiliarity: 0, unfilled: 0, total: 0 };
    const whatif: WhatifResponse = {
      objective_delta: -100, before: zero, after: zero, feasible: true, new_violations: [],
      new_shortfalls: [{ project_id: "j1", grade: "중급", missing: 1 }],
      briefing: { rationale: "", risks: [], alternatives: [] }, fallback_used: false,
    };

    await downloadReport(plan, whatif,
                         { out_person_id: "p1", in_person_id: "p2", project_id: "j1" });

    const sent = JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1]
      .body as string);
    expect(sent.swap_violations).toEqual(["j1의 중급 1명 미충원"]);
    expect(sent.objective_delta).toBe(-100);
  });
});

const META = {
  people: [{ id: "p000", name: "김나윤", grade: "중급", skills: { React: 2 } }],
  projects: [{ id: "j00", name: "A", sector: "대내", phase: "실행",
               start_month: 0, end_month: 3, grade_headcount: { 중급: 2 },
               monthly_budget: 5000 }],
  skills: ["React"],
  review_items: ["문서화"],
  coworks: [{ a_id: "p000", b_id: "p001", co_months: 9, project_count: 0 }],
};

describe("fetchMeta", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => META })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("타입이 붙은 Meta를 반환한다", async () => {
    const meta = await fetchMeta();
    expect(meta.people[0].id).toBe("p000");
    expect(meta.coworks[0].co_months).toBe(9);
  });

  it("HTTP 오류를 조용히 삼키지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 })));
    await expect(fetchMeta()).rejects.toThrow(/500/);
  });
});


describe("milp_params 전달(K8)", () => {
  afterEach(() => vi.unstubAllGlobals());
  const params = { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
                   time_limit: 120, gap: 0.05, max_concurrent_projects: 3, allocation_mode: "fixed" as const, time_limit_auto: false };
  const swap = { out_person_id: "p1", in_person_id: "p2", project_id: "j1" };

  it("postWhatif는 받은 설정을 milp_params로 보내고, null이면 아예 빼서 서버 기본값을 쓴다", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }));
    vi.stubGlobal("fetch", fetchMock);
    await postWhatif([], swap, {}, params);
    await postWhatif([], swap, {}, null);
    const bodies = fetchMock.mock.calls.map(
      (c) => JSON.parse(((c as unknown as [string, RequestInit])[1].body) as string));
    expect(bodies[0].milp_params).toEqual(params);
    expect(bodies[1]).not.toHaveProperty("milp_params");
  });

  it("downloadReport는 계산 기준 설정을 PDF 요청에 싣는다", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, blob: async () => new Blob() }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    const plan = { label: "A", entries: [], objective: 1, unfilled: [], fulfillment: 1,
                   optimization_ratio: 1, index: 0, cached: false };
    await downloadReport(plan, null, null, params);
    const sent = JSON.parse(((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1]
      .body) as string);
    expect(sent.milp_params).toEqual(params);
  });
});


describe("saveSettings", () => {
  afterEach(() => vi.unstubAllGlobals());
  const params = { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
                   time_limit: 120, gap: 0.05, max_concurrent_projects: 3, allocation_mode: "fixed" as const, time_limit_auto: false };

  it("읽은 시점(based_on)을 함께 보내고 409는 충돌 오류로 구분한다", async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 409,
                                           json: async () => ({ detail: "먼저 저장했다" }) }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(saveSettings(params, "2026-10-05T00:00:00+00:00"))
      .rejects.toBeInstanceOf(SettingsConflictError);
    const sent = JSON.parse(((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1]
      .body) as string);
    expect(sent).toEqual({ settings: params, based_on: "2026-10-05T00:00:00+00:00" });
  });
});


describe("uploadDataset", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("zip을 원본 본문으로 보내고, 200·422는 리포트로 돌려주며 그 밖은 throw한다", async () => {
    const body = { activated: false, report: null, detail: "zip 파일이 아니다" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ status: 422, json: async () => body })
      .mockResolvedValueOnce({ status: 413, json: async () => ({ detail: "20MiB" }) });
    vi.stubGlobal("fetch", fetchMock);
    const file = new Blob(["PK"]);
    await expect(uploadDataset(file)).resolves.toEqual(body);
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(init.body).toBe(file);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/zip");
    await expect(uploadDataset(file)).rejects.toThrow("413");
  });
});


describe("applySwap", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("검토와 같은 기준(설정·데이터셋 버전)을 실어 보낸다", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    vi.stubGlobal("fetch", fetchMock);
    const params = { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
                     time_limit: 120, gap: 0.05, max_concurrent_projects: 3, allocation_mode: "fixed" as const, time_limit_auto: false };
    await applySwap([], { out_person_id: "a", in_person_id: "b", project_id: "j" }, { Java: 4 },
                    params, "v".repeat(64));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/plans\/apply-swap$/);
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({ weights: { Java: 4 }, milp_params: params,
                                 dataset_version: "v".repeat(64) });
  });
});


describe("postBaseline — 같은 조건(가중치)으로 채점", () => {
  it("플랜을 계산한 가중치를 함께 보낸다", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ rule: "", optimized: {}, baseline: {},
      difference: {}, note: "" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { postBaseline } = await import("./client");
    await postBaseline({ dataset_version: "v", milp_params: null }, [], { Java: 5 });
    const body = JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    expect(body.weights).toEqual({ Java: 5 });
    vi.unstubAllGlobals();
  });
});
