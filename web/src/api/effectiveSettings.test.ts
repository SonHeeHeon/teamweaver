import { describe, expect, it } from "vitest";
import { effectiveSettings, type SettingsResponse } from "./types";

const base = (auto: boolean | undefined, eff: number | undefined) => ({
  settings: { time_limit: 120, time_limit_auto: auto }, effective_time_limit: eff,
} as unknown as SettingsResponse);

describe("effectiveSettings — 자동 계산 시간", () => {
  it("자동이면 서버가 정한 시간을 계산 기준으로 쓴다", () => {
    expect(effectiveSettings(base(true, 30)).time_limit).toBe(30);
  });
  it("수동이면 저장된 시간을 그대로 쓴다", () => {
    expect(effectiveSettings(base(false, 30)).time_limit).toBe(120);
  });
  it("서버가 자동 값을 안 주면(이전 서버) 저장된 시간을 쓴다", () => {
    expect(effectiveSettings(base(true, undefined)).time_limit).toBe(120);
  });
});
