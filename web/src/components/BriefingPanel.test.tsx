import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BriefingPanel } from "./BriefingPanel";
import type { WhatifResponse } from "../api/types";

const ZERO = { skill: 0, synergy: 0, overfamiliarity: 0, unfilled: 0, total: 0 };

const RESULT: WhatifResponse = {
  objective_delta: -0.0833,
  before: { skill: 1.0, synergy: 0.1, overfamiliarity: -0.2, unfilled: 0, total: 0.9 },
  after: { skill: 0.9, synergy: 0.1167, overfamiliarity: -0.2, unfilled: 0, total: 0.8167 },
  new_violations: [],
  new_shortfalls: [],
  feasible: true,
  briefing: {
    rationale: "p052로 교체 검토: 스킬 4종 보유",
    risks: ["협업 이력이 적다"],
    alternatives: ["다른 프로젝트에서 투입"],
  },
  fallback_used: false,
};

describe("BriefingPanel", () => {
  it("실데이터라 외부 AI로 보내지 않았으면 그 이유를 배지에 밝힌다", () => {
    render(<BriefingPanel result={{ ...RESULT, fallback_used: true, fallback_reason: "external_blocked" }} loading={false} />);
    expect(screen.getByText("규칙 기반(실데이터라 외부 AI로 보내지 않음)")).toBeInTheDocument();
  });

  it("명분·리스크·대안을 모두 보인다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.getByText(/p052로 교체 검토/)).toBeInTheDocument();
    expect(screen.getByText("협업 이력이 적다")).toBeInTheDocument();
    expect(screen.getByText("다른 프로젝트에서 투입")).toBeInTheDocument();
  });

  it("델타 부호를 명확히 보인다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.getByText(/-0\.0833/)).toBeInTheDocument();
  });

  it("델타를 재최적화가 아닌 현행 점수 기준 참고값으로 표기한다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.getByText(/현행 점수 기준 변화/)).toBeInTheDocument();
    expect(screen.getByText(/재최적화 아님/)).toBeInTheDocument();
    expect(screen.queryByText(/목적함수 변화/)).not.toBeInTheDocument();
  });

  it("항목별 변화를 보인다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.getByText("기술 적합도")).toBeInTheDocument();
    expect(screen.getByText("-0.1000")).toBeInTheDocument();
    expect(screen.getByText("+0.0167")).toBeInTheDocument();
  });

  it("교체로 새로 생긴 제약 위반을 경고한다", () => {
    const bad: WhatifResponse = {
      ...RESULT, feasible: false,
      new_violations: [{ code: "availability", location: "p052:month1", actual: 1.2,
                         limit: 1, message: "p052의 1월 투입 합 1.20가 가용률 1.00를 초과" }],
    };
    render(<BriefingPanel result={bad} loading={false} />);
    expect(screen.getByText(/제약 위반/)).toBeInTheDocument();
    expect(screen.getByText(/p052의 1월 투입 합 1.20/)).toBeInTheDocument();
  });

  it("교체로 생긴 등급 미충원을 알린다", () => {
    const short: WhatifResponse = {
      ...RESULT, after: { ...ZERO, unfilled: -100, total: -100 },
      new_shortfalls: [{ project_id: "j01", grade: "중급", missing: 1 }],
    };
    render(<BriefingPanel result={short} loading={false} />);
    expect(screen.getByText(/j01의 중급 1명 미충원/)).toBeInTheDocument();
  });

  it("교체 전부터 있던 위반은 별도 문구로 알린다", () => {
    render(<BriefingPanel result={{ ...RESULT, feasible: false }} loading={false} />);
    expect(screen.getByText(/교체 전 배치에도 제약 위반/)).toBeInTheDocument();
  });

  it("위반이 없으면 경고를 보이지 않는다", () => {
    render(<BriefingPanel result={RESULT} loading={false} />);
    expect(screen.queryByText(/제약 위반/)).not.toBeInTheDocument();
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

describe("BriefingPanel — 근거(K5)", () => {
  it("브리핑 근거를 이름·출처와 함께 보여 준다", () => {
    const zero = { skill: 0, synergy: 0, overfamiliarity: 0, unfilled: 0, total: 0 };
    render(<BriefingPanel loading={false} nameOf={(id) => (id === "p1" ? "김일번" : id)} result={{
      objective_delta: 0, before: zero, after: zero, new_violations: [], new_shortfalls: [],
      feasible: true, fallback_used: true,
      briefing: { rationale: "r", risks: [], alternatives: [],
                  evidence: [{ source_id: "rv:p1>p2#1:neg", reviewer_id: "p1", kind: "summary",
                               text: "공유가 늦다" }] } }} />);
    expect(screen.getByText("요약")).toBeInTheDocument();
    expect(screen.getByText(/김일번 리뷰 · rv:p1>p2#1:neg/)).toBeInTheDocument();
  });
});
