import { describe, expect, it } from "vitest";
import { hasSplit, seatsText, signedPts, splitText } from "./scoreSplit";

describe("scoreSplit(점수 변화 나눠 보이기, 2026-10-19)", () => {
  it("자리 수를 메움·생김으로 읽는다", () => {
    expect(seatsText(1)).toBe("1자리 메움");
    expect(seatsText(-2)).toBe("2자리 생김");
    expect(seatsText(0)).toBe("—");
    expect(seatsText(undefined)).toBe("—");
  });
  it("한 줄 글은 자리 변화가 있을 때만 앞에 붙인다", () => {
    expect(splitText({ delta_total: 101.2, unfilled_seats_delta: 1, delta_excl_unfilled: 1.2 })).toBe("1자리 메움 · 기술·협업 +1.20");
    expect(splitText({ delta_total: -99.3, unfilled_seats_delta: -1, delta_excl_unfilled: 0.67 })).toBe("1자리 생김 · 기술·협업 +0.67");
    expect(splitText({ delta_total: 0.4, unfilled_seats_delta: 0, delta_excl_unfilled: 0.4 })).toBe("기술·협업 +0.40");
  });
  it("나눔이 없는 예전 행은 합계를 그대로 보인다", () => {
    expect(hasSplit({ delta_total: 3.1 })).toBe(false);
    expect(splitText({ delta_total: 3.1 })).toBe("점수 +3.10");
    expect(signedPts(-0.5)).toBe("-0.50");
    expect(signedPts(undefined)).toBe("–");
  });
});
