import { describe, expect, it } from "vitest";
import { blockingViolations, swapWarnings } from "./whatifWarnings";
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

  it("등급이 바뀐 교체는 '등급 대체' 한 줄로 묶고 진짜 위반에서 뺀다(P3-1)", () => {
    const res: WhatifResponse = {
      ...BASE, feasible: false,
      new_violations: [{ code: "grade_over", location: "J001:특급", actual: 2, limit: 1, message: "J001의 특급 배치 2명이 요구 1명을 초과" }],
      new_shortfalls: [{ project_id: "J001", grade: "고급", missing: 1 }],
    };
    expect(swapWarnings(res)).toEqual(["등급 대체 — J001의 고급 자리 1개를 특급으로 채운다(이 안을 확정할 때 사유를 받고 그 사업 정원이 바뀐다)"]);
    expect(blockingViolations(res)).toEqual([]);
    // 짝이 없는 초과는 그대로 위반
    const lone = { ...res, new_shortfalls: [] };
    expect(swapWarnings(lone)).toEqual(["J001의 특급 배치 2명이 요구 1명을 초과"]);
    expect(blockingViolations(lone)).toHaveLength(1);
  });

  it("문제가 없으면 빈 목록", () => {
    expect(swapWarnings(BASE)).toEqual([]);
  });
});
