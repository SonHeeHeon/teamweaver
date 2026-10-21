import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { SkillMapRow } from "../api/types";
import { bandOrder, creditedMostly, distRows, median, niceStep, placeLabels, quadrantOf, radiusOf, summarize } from "./skillDistView";
import { SkillDistribution } from "./SkillDistribution";

const row = (skill: string, practical: number, avgYears: number, senior: number, category = "Data"): SkillMapRow => ({
  skill, category, holders: practical + 2, practical, expert: senior + 1, practical_free: 0, practical_credited: 0,
  demand_projects: 0, demand_headcount: 0, proposal_headcount: 0, proposal_gap: 0, coverage: null, scarce: false, demand: [],
  practical_people: [], levels: [2, practical - senior - 1, 1, senior, 0], senior, avg_months: avgYears * 12,
});
// 연말형 시연의 모양: 넓고 깊음(메타데이터 관리) · 넓고 얕음(금융 업무) · 좁고 깊음(BI Reporting) · 좁고 얕음(RPA)
const ROWS = [row("메타데이터 관리", 62, 4.6, 30), row("금융 업무", 91, 3.0, 12, "Domain"), row("BI Reporting", 26, 5.1, 12),
              row("RPA", 5, 1.2, 0, "Automation"), row("ETL", 64, 4.1, 18), { ...row("빈 기술", 0, 0, 0), avg_months: null }];

describe("skillDistView -- 조직 기술 분포(사용자 2026-10-22)", () => {
  it("실무 가능 1명 이상이고 분포가 온 기술만 그린다", () => {
    expect(distRows(ROWS).map((r) => r.skill)).not.toContain("빈 기술");
    expect(distRows([{ ...ROWS[0], levels: undefined }])).toEqual([]);           // 예전 서버 응답
  });
  it("가운데 값으로 네 칸, 같으면 넓은·깊은 쪽", () => {
    expect(median([1, 3, 2])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
    expect(quadrantOf(10, 40, 10, 40)).toBe("strong");
    expect(quadrantOf(5, 50, 10, 40)).toBe("expert");
    expect(quadrantOf(20, 30, 10, 40)).toBe("broad");
    expect(quadrantOf(5, 30, 10, 40)).toBe("weak");
  });
  it("요약: 강점은 5년+ 많은 순", () => {
    const s = summarize(ROWS);
    expect(s.groups.strong).toEqual(["메타데이터 관리", "ETL"]);
    expect(s.groups.broad).toEqual(["금융 업무"]);
    expect(s.groups.expert).toEqual(["BI Reporting"]);
    expect(s.groups.weak).toEqual(["RPA"]);
  });
  it("하위 기술 인정분이 절반 넘는 기술은 '(인정)'으로 구분(데이터 거버넌스: 실무 54명 전원 인정분)", () => {
    const gov = { ...row("데이터 거버넌스", 70, 4.4, 12), practical_credited: 70, senior_credited: 12 };
    expect(creditedMostly(gov)).toBe(true);
    expect(creditedMostly(row("ETL", 64, 4.1, 18))).toBe(false);
    expect(summarize([...ROWS, gov]).groups.strong).toContain("데이터 거버넌스(인정)");
  });
  it("눈금 간격은 1·2·5×10^k, 원 크기는 조직 최댓값 기준", () => {
    expect(niceStep(91, 8)).toBe(20);
    expect(niceStep(1648, 8)).toBe(500);                                                         // 3,000명 규모에서도 눈금 4~5개
    expect(niceStep(12, 8)).toBe(2);
    expect(niceStep(0)).toBe(1);
    expect(radiusOf(30, 30)).toBe(22);
    expect(radiusOf(0, 30)).toBe(4);
    expect(radiusOf(850, 850)).toBe(22);                                                         // 규모가 커져도 같은 크기 한도
    expect(radiusOf(0, 0)).toBe(4);
  });
  it("경력 띠는 5년+ → 3년+ → 실무 가능 순", () => {
    expect(bandOrder(ROWS).map((r) => r.skill)).toEqual(["메타데이터 관리", "ETL", "금융 업무", "BI Reporting", "RPA"]);
  });
  it("이름표는 겹치면 다른 자리, 자리가 없으면 생략", () => {
    const pts = [{ key: "a", x: 100, y: 100, r: 10, text: "AAAA" }, { key: "b", x: 100, y: 100, r: 10, text: "BBBB" },
                 { key: "c", x: 100, y: 100, r: 10, text: "CCCC" }, { key: "d", x: 100, y: 100, r: 10, text: "DDDD" },
                 { key: "e", x: 100, y: 100, r: 10, text: "EEEE" }];
    const at = placeLabels(pts, { w: 400, h: 400 });
    expect(at.get("a")?.anchor).toBe("start");
    expect(at.get("b")?.anchor).toBe("end");
    expect(at.size).toBeLessThan(5);                                            // 같은 자리에 다섯 개는 다 못 놓는다
    expect(placeLabels([{ key: "z", x: 395, y: 200, r: 3, text: "가나다라" }], { w: 400, h: 400 }).get("z")?.anchor).toBe("end");   // 오른쪽 끝이면 왼쪽에
    // 축 눈금 자리(x0 왼쪽)·고정 글자 자리(blocked)에는 놓지 않는다
    // 오른쪽을 막아 두면 x0가 없을 때는 왼쪽(축 눈금 자리)에 놓이고, x0가 있으면 왼쪽에는 놓지 않는다(자리가 없으면 생략)
    const rightBlocked = [{ x: 62, y: 190, w: 200, h: 20 }];
    const pt = [{ key: "y", x: 60, y: 200, r: 3, text: "가나" }];
    expect(placeLabels(pt, { w: 400, h: 400, blocked: rightBlocked }).get("y")?.anchor).toBe("end");
    expect(placeLabels(pt, { w: 400, h: 400, x0: 56, blocked: rightBlocked }).get("y")?.anchor).not.toBe("end");
    const blocked = placeLabels([{ key: "q", x: 100, y: 100, r: 5, text: "AB" }], { w: 400, h: 400, blocked: [{ x: 100, y: 80, w: 100, h: 40 }] }).get("q");
    expect(blocked?.anchor).toBe("end");
  });
});

describe("SkillDistribution", () => {
  it("요약·사분면·경력 띠를 그리고, 분포가 없으면 아무것도 그리지 않는다", () => {
    const { container, rerender } = render(<SkillDistribution rows={ROWS} />);
    expect(screen.getByText(/상대적 강점\(넓고 깊음\)/).parentElement).toHaveTextContent("메타데이터 관리 · ETL");
    expect(screen.getByText(/이 조직 기술 5개끼리 견준 가운데 값/)).toBeInTheDocument();                // 상대 비교라고 적는다
    expect(container.querySelectorAll("circle[data-skill]")).toHaveLength(5);
    expect(screen.getByRole("img", { name: /메타데이터 관리 경력 분포: 1년 미만 2명/ })).toBeInTheDocument();
    rerender(<SkillDistribution rows={[{ ...ROWS[0], levels: undefined }]} />);
    expect(screen.queryByRole("region", { name: "조직 기술 분포" })).toBeNull();
  });
  it("상위 16개 뒤는 '전체 보기'로", () => {
    const many = Array.from({ length: 20 }, (_, i) => row(`기술${i}`, 10 + i, 3, i));
    render(<SkillDistribution rows={many} />);
    expect(screen.getAllByRole("img", { name: /경력 분포/ })).toHaveLength(16);
    fireEvent.click(screen.getByRole("button", { name: "전체 20개 보기" }));
    expect(screen.getAllByRole("img", { name: /경력 분포/ })).toHaveLength(20);
  });
});
