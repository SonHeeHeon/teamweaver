import { describe, expect, it } from "vitest";
import { stepBody } from "./types";

describe("적용 단계 본문(저장·PDF·소명 요청)", () => {
  it("예전 교체는 모양 그대로, 데려오기·맞바꾸기는 칸을 지킨다(Codex high 2026-10-20)", () => {
    const base = { out_person_id: "a", in_person_id: "b", project_id: "J1" };
    expect(stepBody({ ...base, objective_delta: 1, feasible: true, warnings: [] } as never)).toEqual(base);
    expect(stepBody({ ...base, in_from_project_id: "J2", exchange: true })).toEqual({ ...base, in_from_project_id: "J2", exchange: true });
    expect(stepBody({ ...base, in_from_project_id: "J2" })).toEqual({ ...base, in_from_project_id: "J2" });
  });
});
