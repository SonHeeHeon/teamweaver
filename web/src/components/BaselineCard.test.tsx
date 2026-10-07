import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BaselineCard } from "./BaselineCard";
import type { BaselineResult } from "../api/types";

const sum = (q: number, fit: number, viol: Record<string, number> = {}) => ({
  total: q, quality: q, skill: q * 0.6, synergy: q * 0.4, overfamiliarity: 0, unfilled_seats: 0,
  avg_seat_fit: fit, assignments: 10, people: 10, violations: viol });

describe("BaselineCard — 단순 규칙 대비", () => {
  it("같은 평가 기준으로 나란히 보이고, 자리당 적합이 낮으면 숨기지 않고 알린다", async () => {
    const res: BaselineResult = { rule: "기술 1등 우선", optimized: sum(58.2, 0.61), baseline: sum(31.6, 0.7, { budget: 1 }),
      difference: { quality: 26.6 }, note: "계산상 개선(같은 평가 기준) -- 사업 효과는 검증 전(NOT_CALIBRATED)" };
    const load = vi.fn(async () => res);
    render(<BaselineCard entries={[]} load={load} />);
    fireEvent.click(screen.getByRole("button", { name: "단순 규칙과 비교" }));
    expect(await screen.findByText("+26.60")).toBeInTheDocument();
    expect(screen.getByText(/1건 \(budget 1\)/)).toBeInTheDocument();
    expect(screen.getByText(/자리당 평균 기술 적합은 이 배치가 더 낮다/)).toBeInTheDocument();
    expect(screen.getByText(/NOT_CALIBRATED/)).toBeInTheDocument();
  });
});
