import { describe, expect, it } from "vitest";
import { formatAlloc, formatMonthly } from "./allocFormat";

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
