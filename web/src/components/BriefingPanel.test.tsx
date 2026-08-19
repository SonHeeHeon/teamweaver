import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BriefingPanel } from "./BriefingPanel";
import type { WhatifResponse } from "../api/types";

const RESULT: WhatifResponse = {
  objective_delta: -0.0833,
  briefing: {
    rationale: "p052로 교체 검토: 스킬 4종 보유",
    risks: ["협업 이력이 적다"],
    alternatives: ["다른 프로젝트에서 투입"],
  },
  fallback_used: false,
};

describe("BriefingPanel", () => {
  it("명분·리스크·대안을 모두 보인다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.getByText(/p052로 교체 검토/)).toBeInTheDocument();
    expect(screen.getByText("협업 이력이 적다")).toBeInTheDocument();
    expect(screen.getByText("다른 프로젝트에서 투입")).toBeInTheDocument();
  });

  it("델타 부호를 명확히 보인다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.getByText(/-0\.083/)).toBeInTheDocument();
  });

  it("LLM 대신 규칙 기반이 쓰였으면 배지를 보인다", () => {
    render(<BriefingPanel result={{ ...RESULT, fallback_used: true }} loading={false} />);
    expect(screen.getByText(/규칙 기반/)).toBeInTheDocument();
  });

  it("LLM이 쓰였으면 배지를 보이지 않는다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.queryByText(/규칙 기반/)).not.toBeInTheDocument();
  });
});
