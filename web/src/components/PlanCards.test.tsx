import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PlanCards } from "./PlanCards";
import type { PlanEvent } from "../api/types";

const plan = (label: string, ratio: number): PlanEvent => ({
  label, entries: [{ person_id: "p000", project_id: "j00", alloc: 1 }],
  objective: 12.3, unfilled: [], fulfillment: 0.71, optimization_ratio: ratio,
  index: 1, cached: false,
});

describe("PlanCards", () => {
  it("도착한 플랜만 그린다 (점진 렌더)", () => {
    render(<PlanCards plans={[plan("A", 0.93)]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("Plan A")).toBeInTheDocument();
    expect(screen.queryByText("Plan B")).not.toBeInTheDocument();
  });

  it("최적화율과 충족률을 퍼센트로 보인다", () => {
    render(<PlanCards plans={[plan("A", 0.928)]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("92.8%")).toBeInTheDocument();
    expect(screen.getByText("71.0%")).toBeInTheDocument();
  });

  it("카드를 누르면 선택을 알린다", () => {
    const onSelect = vi.fn();
    render(<PlanCards plans={[plan("A", 0.9), plan("B", 0.88)]} selected="A"
                      onSelect={onSelect} />);
    fireEvent.click(screen.getByText("Plan B"));
    expect(onSelect).toHaveBeenCalledWith("B");
  });

  it("미충원이 있으면 경고를 보인다", () => {
    const p = { ...plan("A", 0.9), unfilled: ["j00:중급:1명 미충원"] };
    render(<PlanCards plans={[p]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText(/미충원/)).toBeInTheDocument();
  });
});


describe("PlanCards — 시간 한도 도달 표시(claude-a 요청)", () => {
  it("시간 한도에서 멈춘 해에 배지를 단다", () => {
    render(<PlanCards plans={[{ ...plan("A", 0.9), time_limited: true }, plan("B", 0.8)]} selected="A"
                      onSelect={() => {}} />);
    expect(screen.getAllByText("시간 한도 도달(최선 증명 전)")).toHaveLength(1);
  });
});
