import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import App from "./App";
import type { Meta, PlanEvent, WhatifResponse } from "./api/types";

// 실제 네트워크를 타지 않도록 클라이언트 모듈 전체를 목킹한다. postWhatif는
// 이 테스트가 언제 이행(resolve)할지 직접 통제해야 하므로 vi.fn으로 둔다
// (deferred 프라미스는 각 테스트 안에서 만든다).
vi.mock("./api/client", () => ({
  fetchMeta: vi.fn(),
  streamOptimize: vi.fn(),
  postWhatif: vi.fn(),
  downloadReport: vi.fn(),
}));

// NetworkGraph는 react-force-graph-2d를 통해 <canvas>를 그리는데 jsdom에는
// canvas 구현이 없다 -- NetworkGraph.test.tsx와 같은 방식으로 목킹한다.
vi.mock("react-force-graph-2d", () => ({
  default: () => <div data-testid="fg" />,
}));

import { fetchMeta, streamOptimize, postWhatif } from "./api/client";

const META: Meta = {
  people: [
    { id: "p1", name: "김일번", grade: "중급", skills: {} },
    { id: "p2", name: "이이번", grade: "중급", skills: {} },
    { id: "p3", name: "박삼번", grade: "중급", skills: {} },
  ],
  projects: [
    { id: "j1", name: "프로젝트1", sector: "대내", phase: "실행",
      start_month: 0, end_month: 3, grade_headcount: { 중급: 1 }, monthly_budget: 1000 },
  ],
  skills: [],
  review_items: [],
  coworks: [],
};

const PLAN_A: PlanEvent = {
  label: "A", entries: [{ person_id: "p1", project_id: "j1", alloc: 1 }],
  objective: 1, unfilled: [], fulfillment: 1, optimization_ratio: 1,
  index: 0, cached: false,
};

const PLAN_B: PlanEvent = {
  label: "B", entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
  objective: 0.9, unfilled: [], fulfillment: 1, optimization_ratio: 0.9,
  index: 1, cached: false,
};

const STALE_RESULT: WhatifResponse = {
  objective_delta: 0.5,
  briefing: {
    rationale: "이 문장은 Plan A 기준으로 계산된 것이라 Plan B 아래 보이면 안 된다",
    risks: ["stale risk"],
    alternatives: ["stale alternative"],
  },
  fallback_used: false,
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

describe("App — 플랜 전환 중 진행 중이던 what-if 응답", () => {
  it("이전 플랜 기준 브리핑이 새로 선택한 플랜 아래 표시되지 않는다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    vi.mocked(streamOptimize).mockReturnValue((async function* () {
      yield { event: "plan" as const, data: PLAN_A };
      yield { event: "plan" as const, data: PLAN_B };
    })());
    const pending = deferred<WhatifResponse>();
    vi.mocked(postWhatif).mockReturnValue(pending.promise);

    render(<App />);

    // 최적화 실행 -> Plan A, Plan B 도착
    fireEvent.click(await screen.findByRole("button", { name: "최적화 실행" }));
    await screen.findByText("Plan A");
    await screen.findByText("Plan B");

    // Plan A가 선택된 상태에서 스왑을 시작한다 (out=p1/j1, in=p3)
    fireEvent.change(screen.getByLabelText("교체 대상"), { target: { value: "p1::j1" } });
    fireEvent.change(screen.getByLabelText("교체 투입"), { target: { value: "p3" } });
    fireEvent.click(screen.getByRole("button", { name: /브리핑 생성/ }));

    expect(postWhatif).toHaveBeenCalledTimes(1);
    await screen.findByText("브리핑 생성 중…");

    // 응답이 오기 전에 Plan B로 전환한다
    fireEvent.click(screen.getByText("Plan B"));

    // 대기 중이던 postWhatif가 이제야 이행된다 -- Plan A 기준 결과다.
    await act(async () => {
      pending.resolve(STALE_RESULT);
      await Promise.resolve();
    });

    // Plan B 아래에는 stale 결과가 절대 보이면 안 된다.
    await waitFor(() => {
      expect(screen.queryByText(/이 문장은 Plan A 기준으로 계산된 것/)).not.toBeInTheDocument();
    });
    // busy 상태로 멈춰 있지도 않아야 한다 (finally 가드 확인).
    expect(screen.queryByText("브리핑 생성 중…")).not.toBeInTheDocument();
    expect(screen.getByText(/인력을 교체하면/)).toBeInTheDocument();
  });
});
