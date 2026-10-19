import { describe, expect, it } from "vitest";
import { formatAlloc, formatMonthly, horizonRange, monthLabel } from "./allocFormat";

describe("formatAlloc", () => {
  it("never shows more than the value", () => {
    expect(formatAlloc(0.205999)).toBe("20.5%");
    expect(formatAlloc(0.206)).toBe("20.6%");
  });
  it("keeps whole percents short", () => {
    expect(formatAlloc(0.23)).toBe("23%");
    expect(formatAlloc(0.29)).toBe("29%");
    expect(formatAlloc(1)).toBe("100%");
  });
});

describe("formatMonthly", () => {
  it("summarises consecutive equal months", () => {
    expect(formatMonthly({ "0": 0.2, "1": 0.2, "2": 0.2, "3": 1, "4": 1, "5": 1 })).toBe("1~3월 20%, 4~6월 100%");
    expect(formatMonthly({ "2": 0.5, "3": 0.3 })).toBe("3월 50%, 4월 30%");
    expect(formatMonthly(undefined)).toBe("");
  });
});

describe("계획 기간의 실제 달(2026-10-19 계획기간 1년 확장)", () => {
  it("시작 달을 알면 달 순번을 실제 달로, 해를 넘겨도", () => {
    expect(monthLabel(0, "2026-10")).toBe("26.10월");
    expect(monthLabel(3, "2026-10")).toBe("27.1월");
    expect(monthLabel(11, "2026-10")).toBe("27.9월");
    expect(monthLabel(11, null)).toBe("12월");
    expect(formatMonthly({ "2": 0.2, "3": 0.2, "4": 1 }, "2026-10")).toBe("26.12~27.1월 20%, 27.2월 100%");
  });
  it("계획 기간 범위", () => {
    expect(horizonRange("2026-10", 12)).toBe("2026-10 ~ 2027-09 · 12개월");
    expect(horizonRange("2026-10", 6)).toBe("2026-10 ~ 2027-03 · 6개월");
    expect(horizonRange(null, 6)).toBe("");
  });
});
