import { describe, expect, it } from "vitest";
import { describeChanges } from "./settingsFields";

const BASE = { min_alloc: 0.3, clique_threshold_months: 6, lam: 0.3, mu: 0.2,
               time_limit: 120, gap: 0.05, max_concurrent_projects: 3, allocation_mode: "fixed" as const, time_limit_auto: false };

describe("describeChanges", () => {
  it("바뀐 필드만 이전→현재로 나열한다(최소 투입률이 같아도 다른 변경을 보여 준다)", () => {
    expect(describeChanges(BASE, { ...BASE, time_limit: 60 })).toEqual(["계산 시간 한도 120초→60초"]);
    expect(describeChanges(BASE, { ...BASE, min_alloc: 0.3001 }))
      .toEqual(["최소 투입률 30%→30.01%"]);
    expect(describeChanges(BASE, BASE)).toEqual([]);
  });

  it("반복 협업 조회 기간 변경도 나열한다(없음 = 전체 이력)", () => {
    expect(describeChanges({ ...BASE, clique_window_months: null }, { ...BASE, clique_window_months: 36 }))
      .toEqual(["반복 협업 조회 기간 전체 이력→최근 3년"]);
  });
});
