import { describe, expect, it } from "vitest";
import { bestLabel, gapToBest, qualityOf, unfilledSeats } from "./planScore";

describe("planScore -- 편성안 카드 점수(사용자 2026-10-22)", () => {
  it("빈자리 수는 미충원 표기의 명 수 합, 형식이 다르면 한 줄 1자리", () => {
    expect(unfilledSeats([])).toBe(0);
    expect(unfilledSeats(["J007:중급:2명 미충원", "J008:고급:1명 미충원"])).toBe(3);
    expect(unfilledSeats(["이상한 표기"])).toBe(1);
    expect(unfilledSeats(["J:00:중급:2명 미충원"])).toBe(2);                     // 사업 ID에 ':'가 있어도
  });
  it("배치 품질 = 종합 점수 + 감점 × 빈자리(감점이 없던 예전 응답은 100)", () => {
    expect(qualityOf({ label: "A", objective: -12.51, unfilled: ["J:중급:1명 미충원"], slack_penalty: 100 })).toBeCloseTo(87.49);
    expect(qualityOf({ label: "A", objective: -12.51, unfilled: ["J:중급:1명 미충원"] })).toBeCloseTo(87.49);
    expect(qualityOf({ label: "A", objective: 40, unfilled: ["J:중급:2명 미충원"], slack_penalty: 10 })).toBeCloseTo(60);
  });
  it("가장 높은 안은 종합 점수 기준(빈자리 적은 안이 먼저), 하나면 없음", () => {
    const a = { label: "A", objective: -12.51, unfilled: ["x:y:1명"] }, b = { label: "B", objective: -12.29, unfilled: ["x:y:1명"] };
    expect(bestLabel([a, b])).toBe("B");
    expect(bestLabel([a])).toBeNull();
    expect(bestLabel([{ label: "A", objective: 5, unfilled: [] }, { label: "B", objective: 5, unfilled: [] }])).toBe("A");   // 같으면 앞의 안
    expect(bestLabel([{ ...a, violations: 0 }, { ...b, violations: 1 }])).toBe("A");                   // 위반이 있는 안은 뺀다
    expect(bestLabel([{ ...a, violations: 2 }, { ...b, violations: 1 }])).toBeNull();
  });
  it("가장 높은 안과의 차이: 같은 빈자리·1% 안이면 사실상 같은 수준", () => {
    const best = { label: "B", objective: -12.29, unfilled: ["x:y:1명"] };
    const g = gapToBest({ label: "A", objective: -12.51, unfilled: ["x:y:1명"] }, best);
    expect(g.sameLevel).toBe(true);
    expect(g.pct).toBeCloseTo(0.22 / 87.71, 5);
    expect(gapToBest({ label: "C", objective: -15, unfilled: ["x:y:1명"] }, best).sameLevel).toBe(false);   // 2.7점 = 3.1%
    expect(gapToBest({ label: "D", objective: -112.4, unfilled: ["x:y:2명"] }, best)).toMatchObject({ moreUnfilled: 1, sameLevel: false });
    const hundred = { label: "B", objective: 100, unfilled: [] };
    expect(gapToBest({ label: "A", objective: 99, unfilled: [] }, hundred).sameLevel).toBe(true);        // 1% 경계는 같은 수준
    expect(gapToBest({ label: "A", objective: 98.99, unfilled: [] }, hundred).sameLevel).toBe(false);
    expect(gapToBest({ label: "B", objective: 12, unfilled: [] }, { label: "A", objective: 10, unfilled: [] }).sameLevel).toBe(false);   // 더 높은 안(위반 안)
    expect(gapToBest({ label: "B", objective: -0.5, unfilled: [] }, { label: "A", objective: 0, unfilled: [] }).sameLevel).toBe(false);  // 품질 0 근처
  });
});
