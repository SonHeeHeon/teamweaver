import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { gapPct } from "./confidence";

describe("ConfidenceBadge — 계산 신뢰도", () => {
  it("상한 대비 차이는 |상한−해|/|해|", () => {
    expect(gapPct(100, 104)).toBeCloseTo(4);
    expect(gapPct(-50, -45)).toBeCloseTo(10);
    expect(gapPct(0, 1)).toBeNull();
    expect(gapPct(10, null)).toBeNull();
  });

  it("최적 종료면 허용 차이 안에서 최선 증명, 검증기 확인을 함께 보인다", () => {
    render(<ConfidenceBadge termination="Optimal" objective={100} bestBound={101} gapAllowed={0.05} />);
    expect(screen.getByText("규칙 위반 없음 확인(검증기)")).toBeInTheDocument();
    expect(screen.getByText("허용 차이 5.0% 안에서 최선 증명")).toBeInTheDocument();
  });

  it("차이 0으로 풀었으면 최선 증명", () => {
    render(<ConfidenceBadge termination="Optimal" objective={10} bestBound={10} gapAllowed={0} />);
    expect(screen.getByText("최선 증명")).toBeInTheDocument();
  });

  it("사용자가 멈춘 해는 시간 한도와 구분해 보인다", () => {
    render(<ConfidenceBadge termination="user_interrupt" objective={100} bestBound={105} gapAllowed={0.05} />);
    expect(screen.getByText("사용자가 멈춤 · 최적값이 이 해보다 최대 5.0% 높을 수 있음")).toBeInTheDocument();
  });

  it("시간 한도면 최적값이 최대 X% 높을 수 있음", () => {
    render(<ConfidenceBadge termination="time_limit_incumbent" objective={100} bestBound={112} gapAllowed={0.05} />);
    expect(screen.getByText("시간 한도 도달 · 최적값이 이 해보다 최대 12% 높을 수 있음")).toBeInTheDocument();
  });

  it("점수로 보이기: 빈자리 감점이 섞여 종합 점수가 0 근처면 %가 부풀려진다(2026-10-22 폴백 리뷰 S3)", () => {
    render(<ConfidenceBadge termination="time_limit_incumbent" objective={-12.51} bestBound={-11} gapAllowed={0.01} inPoints />);
    expect(screen.getByText("시간 한도 도달 · 최적값이 이 해보다 최대 1.51점 높을 수 있음")).toBeInTheDocument();
  });
});
