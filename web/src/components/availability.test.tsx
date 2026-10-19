import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SwapControl } from "./SwapControl";
import { blockedReason } from "./availability";
import { UnavailableList } from "../scenarios/UnavailableList";
import type { AssignEntry, Person, Project } from "../api/types";

// 2026-10-19 사용자 지시: 그 달에 육아휴직이면 교체 투입에 넣을 수 없다 -- 보이되 비활성, 사유는 빨간 글씨
const full = Array(12).fill(1);
const leave = [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];          // 26.12~ 육아휴직
const people: Person[] = [
  { id: "a", name: "가", grade: "중급", skills: {}, availability: full },
  { id: "b", name: "나", grade: "고급", skills: {}, availability: leave,
    leave: { kind: "육아휴직", start: "2026-12", months: 12 } },
  { id: "c", name: "다", grade: "초급", skills: {}, availability: full },
];
const proj = (id: string, s: number, e: number): Project =>
  ({ id, name: id, sector: "대내", phase: "실행", start_month: s, end_month: e, grade_headcount: {}, monthly_budget: 1 } as Project);
const projects = [proj("early", 0, 1), proj("late", 6, 9)];
const entries: AssignEntry[] = [{ person_id: "a", project_id: "early", alloc: 1 }, { person_id: "a", project_id: "late", alloc: 1 }];

describe("투입 불가 인력", () => {
  it("사유 문장은 겹치는 달만", () => {
    expect(blockedReason(people[1], [6, 7, 8, 9], "2026-10")).toBe("육아휴직 — 27.4~27.7월 투입 불가");
    expect(blockedReason(people[1], [0, 1], "2026-10")).toBeNull();
    expect(blockedReason(people[0], [6], "2026-10")).toBeNull();
  });

  it("휴직과 겹치는 사업의 교체 투입에서는 고를 수 없고 사유가 빨갛게 보인다", () => {
    const onSwap = vi.fn();
    render(<SwapControl people={people} entries={entries} onSwap={onSwap} busy={false} projects={projects} horizonStart="2026-10" />);
    // 대상을 고르기 전: 일부 달만 휴직이라 막지는 않고 안내만
    expect(screen.getByLabelText("휴직 달이 있는 인력").textContent).toContain("나: 육아휴직 — 26.12~27.9월 투입 불가");
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "a::late" } });
    const opt = [...screen.getByLabelText("교체 투입").querySelectorAll("option")].find((o) => o.value === "b")!;
    expect(opt.disabled).toBe(true);
    expect(opt.textContent).toContain("육아휴직 — 27.4~27.7월 투입 불가");
    const list = screen.getByLabelText("투입 불가 인력");
    expect(list.className).toContain("text-red-600");
    expect(list.textContent).toContain("나: 육아휴직");
    // 휴직 전 사업이면 넣을 수 있다
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "a::early" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "b" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑/ }));
    expect(onSwap).toHaveBeenCalledWith({ out_person_id: "a", in_person_id: "b", project_id: "early" });
  });

  it("이미 고른 사람이 새 대상 사업과 겹치면 브리핑을 막고 이유를 보인다", () => {
    render(<SwapControl people={people} entries={entries} onSwap={() => {}} busy={false} projects={projects} horizonStart="2026-10" />);
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "a::early" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "b" } });
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "a::late" } });
    expect(screen.getByRole("alert").textContent).toContain("교체 투입할 수 없다");
    expect(screen.getByRole("button", { name: /브리핑/ })).toBeDisabled();
  });

  it("신규 사업·긴급 투입 화면의 투입 불가 목록은 넣기 버튼이 막혀 있다", () => {
    render(<UnavailableList rows={[{ person_id: "b", name: "나", grade: "고급", months: [6], reason: "육아휴직 — 27.4월 투입 불가" }]} />);
    expect(screen.getByRole("button", { name: "나 넣기(투입 불가)" })).toBeDisabled();
    expect(screen.getByText("육아휴직 — 27.4월 투입 불가").className).toContain("text-red-600");
  });
});
