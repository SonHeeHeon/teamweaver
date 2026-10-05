import { describe, expect, it } from "vitest";
import { formatAlloc } from "./allocFormat";

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
