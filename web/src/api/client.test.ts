import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fetchMeta } from "./client";

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
