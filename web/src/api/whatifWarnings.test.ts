import { describe, expect, it } from "vitest";
import { swapWarnings } from "./whatifWarnings";
import type { WhatifResponse } from "./types";

const ZERO = { skill: 0, synergy: 0, overfamiliarity: 0, unfilled: 0, total: 0 };
const BASE: WhatifResponse = {
  objective_delta: 0, before: ZERO, after: ZERO, new_violations: [], new_shortfalls: [],
  feasible: true, briefing: { rationale: "", risks: [], alternatives: [] }, fallback_used: false,
};

describe("swapWarnings", () => {
  it("위반 메시지와 미충원을 한 목록으로 만든다", () => {
    expect(swapWarnings({
      ...BASE, feasible: false,
      new_violations: [{ code: "budget", location: "j1", actual: 6000, limit: 5000,
                         message: "j1 월 비용 6,000이 예산 5,000을 초과" }],
      new_shortfalls: [{ project_id: "j1", grade: "중급", missing: 1 }],
    })).toEqual(["j1 월 비용 6,000이 예산 5,000을 초과", "j1의 중급 1명 미충원"]);
  });

  it("교체 전부터 있던 위반만 있으면 그 사실을 알린다", () => {
    expect(swapWarnings({ ...BASE, feasible: false }))
      .toEqual(["교체 전 배치에도 제약 위반이 있다"]);
  });

  it("문제가 없으면 빈 목록", () => {
    expect(swapWarnings(BASE)).toEqual([]);
  });
});
