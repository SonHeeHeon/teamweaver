import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { downloadReport, fetchMeta } from "./client";
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
