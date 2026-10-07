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

  it("최적 종료면 허용 차이 안에서 최선 증명, 독립 검증 통과를 함께 보인다", () => {
    render(<ConfidenceBadge termination="Optimal" objective={100} bestBound={101} gapAllowed={0.05} />);
    expect(screen.getByText("독립 검증 통과")).toBeInTheDocument();
    expect(screen.getByText("허용 차이 5.0% 안에서 최선 증명")).toBeInTheDocument();
  });

  it("차이 0으로 풀었으면 최선 증명", () => {
    render(<ConfidenceBadge termination="Optimal" objective={10} bestBound={10} gapAllowed={0} />);
    expect(screen.getByText("최선 증명")).toBeInTheDocument();
  });

  it("시간 한도면 최적값이 최대 X% 높을 수 있음", () => {
    render(<ConfidenceBadge termination="time_limit_incumbent" objective={100} bestBound={112} gapAllowed={0.05} />);
    expect(screen.getByText("시간 한도 도달 · 최적값이 이 해보다 최대 12% 높을 수 있음")).toBeInTheDocument();
  });
});
