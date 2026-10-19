import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TeamRelations, reviewWord } from "./TeamRelations";

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
});
