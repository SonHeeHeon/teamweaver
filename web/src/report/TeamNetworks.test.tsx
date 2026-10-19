import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TeamNetworks, teamCards } from "./TeamNetworks";
import type { Cowork, Person, Project } from "../api/types";

const P = (id: string, name: string): Person => ({ id, name, grade: "중급", skills: {} });
const J = (id: string, name: string): Project => ({ id, name, sector: "대내", phase: "실행", start_month: 0, end_month: 5,
  grade_headcount: {}, monthly_budget: 0 });
const people = [P("p1", "김일번"), P("p2", "이이번"), P("p3", "박삼번"), P("p4", "최사번"), P("p5", "정오번")];
const projects = [J("J002", "결제 고도화"), J("J001", "데이터 마트")];
const entries = [
  { person_id: "p1", project_id: "J002" }, { person_id: "p2", project_id: "J002" }, { person_id: "p3", project_id: "J002" },
  { person_id: "p4", project_id: "J001" }, { person_id: "p5", project_id: "J001" },
];
const coworks: Cowork[] = [
  { a_id: "p1", b_id: "p2", co_months: 12, project_count: 0, review: 0.6 },
  { a_id: "p2", b_id: "p1", co_months: 12, project_count: 0, review: 0.6 },   // 양방향 중복이 와도 한 줄
  { a_id: "p1", b_id: "p3", co_months: 4, project_count: 0, review: -0.5 },
  { a_id: "p3", b_id: "p4", co_months: 30, project_count: 0, review: 0.9 },   // 다른 사업 -- 그리지 않는다
];

describe("teamCards", () => {
  it("사업 코드 순, 팀 안 쌍만, 평균 개월·나쁜 쌍·아는 사람 없는 팀원", () => {
    const cards = teamCards(entries, people, projects, coworks);
    expect(cards.map((c) => c.id)).toEqual(["J001", "J002"]);
    const j2 = cards[1];
    expect(j2.pairs).toBe(3);
    expect(j2.ties.map((t) => [t.a, t.b, t.tone])).toEqual([["p1", "p2", "good"], ["p1", "p3", "bad"]]);
    expect(j2.avgMonths).toBe(8);
    expect(j2.bad).toBe(1);
    expect(j2.members.every((m) => m.known)).toBe(true);
    const j1 = cards[0];
    expect(j1.ties).toEqual([]);                                     // p4–p5는 함께 일한 적 없음, p3–p4는 사업이 다름
    expect(j1.members.map((m) => m.known)).toEqual([false, false]);
  });

  it("평가 없는 쌍은 none", () => {
    const [card] = teamCards(entries.slice(0, 2), people, projects,
                             [{ a_id: "p1", b_id: "p2", co_months: 6, project_count: 0, review: null }]);
    expect(card.ties[0].tone).toBe("none");
  });
});

describe("TeamNetworks", () => {
  it("사업마다 카드 하나, 나쁜 평가는 빨간 선, 아는 사람 없는 팀원은 속 빈 원, 요약에 나쁜 쌍 사업", () => {
    const { container } = render(<TeamNetworks entries={entries} people={people} projects={projects} coworks={coworks} />);
    expect(screen.getByTestId("team-J001")).toBeInTheDocument();
    expect(screen.getByTestId("team-J002")).toBeInTheDocument();
    const lines = container.querySelectorAll('[data-testid="team-J002"] line');
    expect([...lines].map((l) => l.getAttribute("data-tone"))).toEqual(["good", "bad"]);   // 나쁨이 맨 위(마지막)
    expect(container.querySelector('[data-testid="team-J002"] line[data-tone="bad"]')!.getAttribute("stroke")).toBe("#dc2626");
    expect(container.querySelectorAll('[data-testid="team-J001"] line')).toHaveLength(0);
    const hollow = container.querySelectorAll('[data-testid="team-J001"] circle[data-known="false"]');
    expect(hollow).toHaveLength(2);
    expect(hollow[0].getAttribute("fill")).toBe("#ffffff");
    expect(screen.getByTestId("team-network-summary")).toHaveTextContent("팀 안 4쌍 중 2쌍이 예전에 함께 일한 사이다");
    expect(screen.getByTestId("team-network-summary")).toHaveTextContent("J002(1쌍)");
    expect(screen.getByTestId("team-J002")).toHaveTextContent("아는 사이 2/3쌍 · 평균 8개월 · 평가 나쁨 1쌍");
    expect(screen.getByTestId("team-J001")).toHaveTextContent("아는 사람 없음 2명");
    expect(screen.getByText("평가 나쁨")).toBeInTheDocument();                                // 색 범례
  });

  it("평가가 하나도 실리지 않으면(실데이터) 색 범례 대신 안내", () => {
    const plain = coworks.map((c) => ({ ...c, review: null }));
    render(<TeamNetworks entries={entries} people={people} projects={projects} coworks={plain} />);
    expect(screen.queryByText("평가 나쁨")).toBeNull();
    expect(screen.getByText(/선 색 없음/)).toBeInTheDocument();
    expect(screen.getByTestId("team-network-summary")).not.toHaveTextContent("평가 나쁜");
  });

  it("혼자 배치된 사업", () => {
    render(<TeamNetworks entries={[{ person_id: "p1", project_id: "J001" }]} people={people} projects={projects} coworks={[]} />);
    expect(screen.getByTestId("team-J001")).toHaveTextContent("혼자 배치");
  });

  it("13명부터는 한 줄을 다 쓰는 넓은 카드(작은 원에서는 이름이 겹친다)", () => {
    const many = Array.from({ length: 13 }, (_, i) => P(`q${i}`, `사람${i}`));
    const big = many.map((p) => ({ person_id: p.id, project_id: "J009" }));
    const small = many.slice(0, 12).map((p) => ({ person_id: p.id, project_id: "J008" }));
    render(<TeamNetworks entries={[...big, ...small]} people={many} projects={projects} coworks={[]} />);
    expect(screen.getByTestId("team-J009").className).toContain("col-span-3");
    expect(screen.getByTestId("team-J008").className).not.toContain("col-span-3");
    expect(screen.getByTestId("team-J009").querySelector("svg")!.getAttribute("width")).toBe("640");
  });

  it("빈 명단", () => {
    render(<TeamNetworks entries={[]} people={people} projects={projects} coworks={[]} />);
    expect(screen.getByText("배치된 사람이 없다.")).toBeInTheDocument();
  });
});
