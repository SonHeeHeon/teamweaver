import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { SkillMapRow } from "../api/types";
import { barOf, barScale, centerLines, graphMode, relationModel } from "./skillMapView";
import { SkillRelation } from "./SkillRelation";

const base: SkillMapRow = {
  skill: "Private Cloud", category: "Cloud", holders: 20, practical: 4, expert: 3, practical_free: 2, practical_credited: 1,
  demand_projects: 3, demand_headcount: 4, proposal_headcount: 3, proposal_gap: 1, coverage: 1, scarce: false,
  demand: [{ project_id: "J016", project: "고도화", proposal: false, headcount: 1, min_months: 36, qualified_people: 3 },
           { project_id: "J020", project: "추가 과제", proposal: true, headcount: 1, min_months: 24, qualified_people: 4 },
           { project_id: "J019", project: "신규", proposal: true, headcount: 2, min_months: 24, qualified_people: 4 }],
  practical_people: [
    { id: "P1", name: "이윤아", credited: false, months: 40, expert: true, free: true, on: [] },
    { id: "P2", name: "황유찬", credited: false, months: 91, expert: true, free: true, on: [] },
    { id: "P3", name: null, credited: true, months: 20, expert: false, free: false, on: ["J016"], credited_from: [{ skill: "OpenStack", months: 29, ratio: 0.7 }] },
    { id: "P4", name: "정현정", credited: false, months: 75, expert: true, free: false, on: ["J016"] },
  ],
  broader: ["Cloud 인프라"], narrower: [{ skill: "OpenStack", practical: 5 }],
};

describe("skillMapView -- 조직 기술 지도 막대·관계 그림(사용자 2026-10-21)", () => {
  it("막대 눈금은 수요 쪽(필요·신규 제안·비어 있음) × 1.5 -- 실무 가능이 넘치면 자르고 넘침 표시", () => {
    const rows = [base, { ...base, skill: "SQL", practical: 91, practical_free: 13, demand_headcount: 15, proposal_headcount: 0 }];
    expect(barScale(rows)).toBe(23);                                        // ceil(15 × 1.5)
    expect(barOf(base, 8)).toEqual({ practical: 0.5, free: 0.25, need: 0.5, proposal: 0.375, overflow: false });
    expect(barOf(rows[1], 23).overflow).toBe(true);
    expect(barOf(rows[1], 23).practical).toBe(1);
    expect(barScale([])).toBe(4);
  });
  it("사람별 칸(claude-a)이 데이터에 있으면 모든 행을 그림으로(실무 가능 0명인 행도)", () => {
    expect(graphMode([base, { ...base, practical_people: [] }])).toBe(true);
    expect(graphMode([{ ...base, practical_people: [{ id: "P1", name: null, credited: false }] }])).toBe(false);
  });
  it("요구 사업은 신규 제안 먼저, 비어 있는 사람은 숙련·경력 순, 배치 중인 사람은 사업별로 센다", () => {
    const m = relationModel(base);
    expect(m.demands.map((d) => d.project_id)).toEqual(["J019", "J020", "J016"]);
    expect(m.free.map((p) => p.id)).toEqual(["P2", "P1"]);
    expect(m.busy).toEqual([{ project: "J016", count: 2, expert: 1, credited: 1 }]);
    expect(m.gap).toEqual({ need: 3, free: 2, short: 1 });
    expect(m.narrower).toEqual(["OpenStack(실무 5)"]);
  });
  it("인정 표시는 표와 같은 판정(credited)일 때만, 비율은 서버가 준 값(credit^깊이)", () => {
    const people = [{ id: "P5", name: "김", credited: true, months: 19, free: true, credited_from: [{ skill: "Spring Boot", months: 41, ratio: 0.49 }] },
                    { id: "P6", name: "이", credited: false, months: 50, free: true, credited_from: [{ skill: "Spring", months: 30, ratio: 0.7 }] }];
    const m = relationModel({ ...base, practical_people: people });
    expect(m.free.find((p) => p.id === "P5")?.credit).toBe("Spring Boot 41개월의 49% 인정");
    expect(m.free.find((p) => p.id === "P6")?.credit).toBeNull();          // 본인 경력만으로 실무 가능 -- 인정 아님
  });
  it("많으면 잘라 '더'로, 사업을 모르면 따로 묶고, 문자열 하위 기술도 받는다", () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ id: `F${i}`, name: null, credited: false, months: 20 + i, free: true, on: [] }));
    const lost = { id: "X", name: null, credited: false, months: 30, free: false };
    const m = relationModel({ ...base, practical_people: [...many, lost], narrower: ["Spring"],
      demand: Array.from({ length: 7 }, (_, i) => ({ project_id: `J${i}`, project: "p", proposal: false, headcount: 1, min_months: 12, qualified_people: 1 })) });
    expect([m.free.length, m.moreFree, m.demands.length, m.moreDemands]).toEqual([6, 3, 5, 2]);
    expect(m.busy).toEqual([{ project: "(사업 모름)", count: 1, expert: 0, credited: 0 }]);   // 배치 중인데 사업 칸이 없음
    const noField = relationModel({ ...base, practical_people: [{ id: "Y", name: null, credited: false }] });
    expect(noField.busy).toEqual([{ project: "(정보 없음)", count: 1, expert: 0, credited: 0 }]);   // free 칸 자체가 없음
    expect(m.narrower).toEqual(["Spring"]);
  });
  it("가운데 원 글자: 길면 띄어쓰기에서 두 줄", () => {
    expect(centerLines("Spark")).toEqual(["Spark"]);
    expect(centerLines("Data Lake & Data Catalog")).toEqual(["Data Lake &", "Data Catalog"]);
    expect(centerLines("비정형문서처리OCR기술")).toHaveLength(2);
    expect(centerLines("접근통제·인증관리체계(IAM)")).toEqual(["접근통제·인증관리체계", "(IAM)"]);   // 단어 중간이 아니라 '(' 앞
  });
});

describe("SkillRelation", () => {
  it("요구 사업 ↔ 기술(계보) ↔ 사람과 부족 결론을 그린다", () => {
    render(<SkillRelation row={base} />);
    const svg = screen.getByRole("img", { name: "Private Cloud 기술 관계" });
    expect(svg.querySelectorAll("[data-node='demand']")).toHaveLength(3);
    expect(svg.querySelectorAll("[data-node='free']")).toHaveLength(2);
    expect(svg.querySelectorAll("[data-node='busy']")).toHaveLength(1);
    expect(svg.querySelector("[data-node='broader']")?.textContent).toContain("Cloud 인프라");
    expect(screen.getByText(/1명 부족/)).toBeTruthy();
  });
});
