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
    expect(screen.getByText("점선 = 하위 기술 경력에서 일부 인정한 레벨")).toBeInTheDocument();
  });

  it("PDF 줄에는 실무 수준(12개월+)이 된 인정만", () => {
    expect(creditedLine(P)).toBe("Java 18개월(Spring 경력에서 인정)");
  });
});

describe("배치 명단 압축(사용자 2026-10-20)", () => {
  const people: Person[] = [
    { id: "A", name: "가나다", grade: "고급", skills: { Java: 3 } }, { id: "B", name: "라마바", grade: "초급", skills: {} },
    { id: "C", name: "사아자", grade: "중급", skills: {} }];
  const projects = [
    { id: "J2", name: "긴 이름의 차세대 시스템", start_month: 0, end_month: 2, grade_headcount: { 고급: 1, 초급: 1 } },
    { id: "J1", name: "포털", start_month: 1, end_month: 5, grade_headcount: { 중급: 2 } },
  ] as unknown as Project[];
  const entries = [{ person_id: "A", project_id: "J2", alloc: 1 }, { person_id: "B", project_id: "J2", alloc: 1 },
                   { person_id: "C", project_id: "J1", alloc: 1 }];
  const order = () => screen.getAllByRole("list").map((l) => l.getAttribute("aria-label"));

  it("100%는 보이지 않고, 사업 코드에 마우스를 올리면 이름, 정원 대비 인원과 빈자리", () => {
    render(<AssignmentTable entries={entries} people={people} projects={projects} unfilled={["J1:중급:1명 미충원"]} />);
    expect(screen.queryByText("100%")).toBeNull();
    expect(screen.getByLabelText("J2 긴 이름의 차세대 시스템")).toHaveAttribute("title", "긴 이름의 차세대 시스템");
    expect(screen.getByText("2/2명")).toBeInTheDocument();
    expect(screen.getByText("빈자리 1")).toBeInTheDocument();
  });

  it("정렬을 바꾸면 사업 순서가 바뀌고, 이름을 찾으면 어디 있는지 알려 준다", () => {
    render(<AssignmentTable entries={entries} people={people} projects={projects} unfilled={["J1:중급:1명 미충원"]} />);
    expect(order()).toEqual(["J1 배치 명단", "J2 배치 명단"]);
    fireEvent.change(screen.getByLabelText("사업 정렬 기준"), { target: { value: "size" } });
    expect(order()).toEqual(["J2 배치 명단", "J1 배치 명단"]);
    fireEvent.change(screen.getByLabelText("사업 정렬 기준"), { target: { value: "end" } });
    expect(order()).toEqual(["J2 배치 명단", "J1 배치 명단"]);
    fireEvent.change(screen.getByLabelText("배치 명단에서 사람 찾기"), { target: { value: "사아" } });
    expect(screen.getByText("J1에 있음")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("배치 명단에서 사람 찾기"), { target: { value: "없는" } });
    expect(screen.getByText("배치 명단에 없음")).toBeInTheDocument();
  });

  it("기술 창은 바깥을 누르면 닫힌다", () => {
    render(<AssignmentTable entries={entries} people={people} projects={projects} />);
    fireEvent.click(screen.getByRole("button", { name: "가나다 기술 보기" }));
    expect(screen.getByRole("dialog", { name: "가나다 기술" })).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog", { name: "가나다 기술" })).toBeNull();
  });
});
