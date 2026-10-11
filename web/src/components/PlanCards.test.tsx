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

  it("미리 계산 결과는 '미리 계산 · 시각'으로, 일반 캐시는 '저장된 결과'로 밝힌다", () => {
    const pre = { ...plan("A", 0.9), cached: true, precomputed_at: "2026-10-06T14:40:50+00:00" };
    const cached = { ...plan("B", 0.9), cached: true };
    render(<PlanCards plans={[pre, cached]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText(/^미리 계산 · \d+월 \d+일 \d\d:\d\d$/)).toBeInTheDocument();
    expect(screen.getByText("저장된 결과")).toBeInTheDocument();
    expect(screen.queryByText("캐시")).not.toBeInTheDocument();
  });

  it("미리 계산 원안에 화면에서 변경을 적용했으면 '원안 + 변경 n건'으로 바꾼다", () => {
    const pre = { ...plan("A", 0.9), cached: true, precomputed_at: "2026-10-06T14:40:50+00:00" };
    render(<PlanCards plans={[pre]} selected="A" onSelect={() => {}} editCounts={{ A: 2 }} />);
    expect(screen.getByText("미리 계산 원안 + 변경 2건")).toBeInTheDocument();
  });

  it("변경한 명단에는 원안의 '최선 증명' 배지를 물려주지 않는다(Codex 사후 리뷰 MUST)", () => {
    const solved = { ...plan("A", 0.9), termination: "Optimal", objective: 5, best_bound: 5, gap_allowed: 0.05 };
    const { rerender } = render(<PlanCards plans={[solved]} selected="A" onSelect={() => {}} />);
    expect(screen.queryByText("변경 후 재평가 · 솔버 증명은 원안 기준")).not.toBeInTheDocument();
    rerender(<PlanCards plans={[solved]} selected="A" onSelect={() => {}} editCounts={{ A: 1 }} />);
    expect(screen.getByText("변경 후 재평가 · 솔버 증명은 원안 기준")).toBeInTheDocument();
    expect(screen.queryByText(/최선 증명/)).not.toBeInTheDocument();
  });
});

