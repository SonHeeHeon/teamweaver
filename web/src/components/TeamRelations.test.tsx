import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TeamRelations } from "./TeamRelations";
import { reviewWord } from "./relationTone";

describe("팀원과의 관계(협업 관계망 대신, 사용자 2026-10-20)", () => {
  const people = [{ id: "a", name: "가", grade: "중급", skills: {} }, { id: "b", name: "나", grade: "중급", skills: {} },
                  { id: "c", name: "다", grade: "중급", skills: {} }, { id: "d", name: "라", grade: "중급", skills: {} }];
  const projects = [{ id: "J1", name: "포털" }] as never;
  const entries = [{ person_id: "a", project_id: "J1", alloc: 1 }, { person_id: "c", project_id: "J1", alloc: 1 },
                   { person_id: "d", project_id: "J1", alloc: 1 }];
  const coworks = [{ a_id: "a", b_id: "c", co_months: 14, project_count: 0, review: 0.6 },
                   { a_id: "b", b_id: "d", co_months: 3, project_count: 0, review: -0.5 }];

  it("교체 대상을 고르기 전에는 안내만", () => {
    render(<TeamRelations people={people} projects={projects} coworks={coworks} entries={entries} pick={null} />);
    expect(screen.getByText("왼쪽에서 교체 대상을 고르면 보입니다.")).toBeInTheDocument();
  });

  it("빠질 사람·들어올 사람과 그 사업 팀원의 관계를 말로", () => {
    render(<TeamRelations people={people} projects={projects} coworks={coworks} entries={entries}
                          pick={{ outPersonId: "a", projectId: "J1", inPersonId: "b", from: null }} />);
    expect(screen.getByText("함께 14개월 · 평가 좋음")).toBeInTheDocument();
    expect(screen.getByText("함께 3개월 · 평가 나쁨")).toBeInTheDocument();
    expect(screen.getByText("함께 일한 적 없음: 다")).toBeInTheDocument();
    expect(reviewWord(0)).toBe("평가 보통");
    expect(reviewWord(null)).toBeNull();
  });

  it("관계 그림: 왼쪽 빠질 사람·오른쪽 들어올 사람에서 함께 일한 팀원으로 선(사용자 2026-10-21 '지식그래프로')", () => {
    const { container } = render(<TeamRelations people={people} projects={projects} coworks={coworks} entries={entries}
                                                pick={{ outPersonId: "a", projectId: "J1", inPersonId: "b", from: "J9" }} />);
    expect(screen.getByRole("img", { name: "빠질 사람·들어올 사람과 J1 팀원의 관계 그림" })).toBeInTheDocument();
    const lines = [...container.querySelectorAll("line[data-side]")].map((l) =>
      [l.getAttribute("data-side"), l.getAttribute("data-member"), l.getAttribute("data-tone")]);
    expect(lines).toEqual([["out", "c", "good"], ["in", "d", "bad"]]);   // 나쁨이 맨 위(마지막)
    expect(container.querySelector('line[data-tone="bad"]')!.getAttribute("stroke")).toBe("#dc2626");
    expect(screen.getByText("14개월")).toBeInTheDocument();
    expect(screen.getByText("· J9에서", { exact: false })).toBeInTheDocument();
    expect(container.querySelector('[data-summary="out"]')!.textContent).toBe("빠질 사람 가: 팀원 2명 중 1명과 함께 일함 · 평균 14개월");
    expect(container.querySelector('[data-summary="in"]')!.textContent).toBe("들어올 사람 나: 팀원 2명 중 1명과 함께 일함 · 평균 3개월 · 평가 나쁨 1명");
    expect(screen.getByText("목록으로 보기")).toBeInTheDocument();
  });

  it("들어올 사람을 고르기 전에는 오른쪽이 빈 자리, 평가가 없으면 색 대신 그 사실", () => {
    const plain = coworks.map((c) => ({ ...c, review: null }));
    const { container } = render(<TeamRelations people={people} projects={projects} coworks={plain} entries={entries}
                                                pick={{ outPersonId: "a", projectId: "J1", inPersonId: null, from: null }} />);
    expect(screen.getByText("고르면 보입니다")).toBeInTheDocument();
    expect(screen.getByText("들어올 사람을 고르면 오른쪽에 함께 보입니다.")).toBeInTheDocument();
    expect([...container.querySelectorAll("line[data-side]")].map((l) => l.getAttribute("data-tone"))).toEqual(["none"]);
    expect(screen.getByText(/선 색 없음/)).toBeInTheDocument();
    expect(container.querySelector('[data-member="d"][data-known="false"]')).not.toBeNull();   // 아무와도 모르는 팀원은 점선
  });
});
