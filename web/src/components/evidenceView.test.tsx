import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { JustEvidence } from "../api/types";
import { bestSwaps, cellOf, orderedMembers, swapTone, swapVerdict, teamEntries } from "./evidenceView";
import { EvidenceGlance } from "./EvidenceGlance";

const req = (skill: string, level: number, min: number, status: "met" | "below" | "missing", months = 30, credited?: string) => ({
  skill, months, level, min_level: min, status, ...(credited ? { credited_from: { skill: credited, months: 40, ratio: 0.49 } } : {}),
});
const EV: JustEvidence = {
  project_id: "J001",
  requirements: [{ skill: "ETL", headcount: 2, min_level: 2, met_by: 2, status: "met" },
                 { skill: "금융 업무", headcount: 2, min_level: 3, met_by: 1, status: "short" }],
  members: [
    { person_id: "P2", name: "김경민", grade: "초급", skill_fit: 0.917, same_industry_projects: 5, same_industry_months: 22,
      requirements: [req("ETL", 3, 2, "met", 49), req("금융 업무", 2, 3, "below", 22)] },
    { person_id: "P1", name: null, grade: "특급", skill_fit: 1, same_industry_projects: 10, same_industry_months: 50,
      requirements: [req("ETL", 4, 2, "met", 64, "Spark"), req("금융 업무", 3, 3, "met", 50)] },
  ],
  alternatives: {
    P2: [{ person_id: "P9", name: "박유나", skill_fit: 0.9, delta_total: -0.21, delta_skill: 0, delta_synergy: -0.21, new_violations: 0 }],
    P1: [{ person_id: "P8", name: "박은지", skill_fit: 1, delta_total: -0.19, delta_skill: 0, delta_synergy: -0.19, new_violations: 0 }],
  },
};

describe("evidenceView -- 인사팀 소명 근거 한눈에(사용자 2026-10-21)", () => {
  it("표 줄은 등급 순, 이름이 없으면(실데이터) ID", () => {
    expect(orderedMembers(EV).map((m) => m.person_id)).toEqual(["P1", "P2"]);
  });
  it("칸: 충족·미달·없음, 인정 경력은 출처", () => {
    expect(cellOf(req("ETL", 3, 2, "met", 49))).toMatchObject({ mark: "✓ 레벨 3", sub: "49개월", tone: "ok", credited: null });
    expect(cellOf(req("금융 업무", 2, 3, "below", 22))).toMatchObject({ mark: "✗ 레벨 2", sub: "22개월 · 요구 3", tone: "bad" });
    expect(cellOf(req("Kafka", 0, 2, "missing", 0))).toMatchObject({ mark: "없음", tone: "none" });
    expect(cellOf(req("ETL", 4, 2, "met", 64, "Spark")).credited).toBe("Spark 40개월의 49% 인정");    // 깊이 2 = 0.7² (서버가 비율로)
  });
  it("대안 결론: 위반 없이 오름 · 오르지만 모두 위반 · 오르는 것 없음 · 대안 없음", () => {
    expect(swapVerdict(bestSwaps(EV)).kind).toBe("none_better");
    const up: JustEvidence = { ...EV, alternatives: { ...EV.alternatives,
      P2: [{ ...EV.alternatives.P2[0], delta_total: 0.02 }] } };
    const v = swapVerdict(bestSwaps(up));
    expect(v.kind).toBe("some_better");
    expect(v.better.map((r) => r.member.person_id)).toEqual(["P2"]);
    // 점수가 오르는데 위반이 생기는 대안만 있으면 "내려간다"가 아니라 그렇다고 말한다(폴백 리뷰 MUST -- 글의 불리한 사실과 맞게)
    const withViolation: JustEvidence = { ...EV, alternatives: { ...EV.alternatives,
      P2: [{ ...EV.alternatives.P2[0], delta_total: 0.27, new_violations: 3 }] } };
    const w = swapVerdict(bestSwaps(withViolation));
    expect(w.kind).toBe("better_only_with_violation");
    expect(w.violating.map((r) => r.member.person_id)).toEqual(["P2"]);
    expect(swapVerdict(bestSwaps({ ...EV, alternatives: {} })).kind).toBe("none");
  });
  it("막대 색: 위반 · 오름 · 내림 · 같음(±0.005 안)", () => {
    const a = EV.alternatives.P2[0];
    expect(swapTone({ ...a, new_violations: 1, delta_total: 0.3 })).toBe("violation");
    expect(swapTone({ ...a, delta_total: 0.02 })).toBe("up");
    expect(swapTone({ ...a, delta_total: -0.21 })).toBe("down");
    expect(swapTone({ ...a, delta_total: 0.001 })).toBe("same");
  });
  it("협업 지도 명단은 이 사업 팀만", () => {
    expect(teamEntries(EV)).toEqual([{ person_id: "P2", project_id: "J001" }, { person_id: "P1", project_id: "J001" }]);
  });
});

describe("EvidenceGlance", () => {
  it("충족표·사업 요구 줄·팀 협업 지도·대안 결론을 그린다", () => {
    const people = [{ id: "P1", name: "노민준" }, { id: "P2", name: "김경민" }] as never;
    render(<EvidenceGlance evidence={EV} meta={{ people, projects: [{ id: "J001", name: "은행" }] as never,
                                                 coworks: [{ a_id: "P1", b_id: "P2", co_months: 16, project_count: 1, review: 0.6 }] }} />);
    const table = screen.getByRole("table", { name: "요구 기술 충족표" });
    expect(within(table).getByText("✗ 레벨 2")).toBeTruthy();
    expect(within(table).getByText("P1")).toBeTruthy();                                // 실데이터 이름 없음 → ID
    expect(within(table).getByText("충족 1명 / 필요 2명")).toBeTruthy();
    const map = screen.getByRole("img", { name: "J001 팀 협업 관계" });
    expect(map.querySelector("line")?.getAttribute("data-tone")).toBe("good");
    expect(within(map as unknown as HTMLElement).getByText("P1")).toBeTruthy();       // 지도도 표와 같은 표기(meta 이름 아님)
    expect(screen.getByText(/규칙 위반 없이 점수가 오르는 경우는 없습니다/)).toBeTruthy();
  });
});
