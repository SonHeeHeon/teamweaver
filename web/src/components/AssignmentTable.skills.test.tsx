import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { AssignmentTable } from "./AssignmentTable";
import { creditedLine } from "./skillLabel";
import type { Person, Project } from "../api/types";

const P: Person = { id: "DP1", name: "최혜진", grade: "중급", skills: { Java: 2, Spring: 3, JavaScript: 1 },
  skill_credit: { Java: { source: "Spring", months: 18, depth: 1, own_months: 0 },
                  JavaScript: { source: "React", months: 2, depth: 1, own_months: 0 } } };
const J = { id: "J1", name: "포털", start_month: 0, end_month: 5 } as unknown as Project;

describe("인정 경력 구분 표시(사용자 결정 2026-10-11)", () => {
  it("기술을 펼치면 인정된 기술은 출처와 함께 구분해 보인다", () => {
    render(<AssignmentTable entries={[{ person_id: "DP1", project_id: "J1", alloc: 1 }]} people={[P]} projects={[J]} />);
    fireEvent.click(screen.getByRole("button", { name: "최혜진 기술 보기" }));
    expect(screen.getByText("Spring 레벨 3")).toBeInTheDocument();
    expect(screen.getByText("Java 레벨 2 (Spring 경력에서 인정)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "최혜진 기술 보기" })).toHaveTextContent("인정 포함");
  });

  it("PDF 줄에는 실무 수준(12개월+)이 된 인정만", () => {
    expect(creditedLine(P)).toBe("Java 18개월(Spring 경력에서 인정)");
  });
});
