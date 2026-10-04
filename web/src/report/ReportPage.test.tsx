import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ReportPage } from "./ReportPage";
import type { Meta } from "../api/types";

vi.mock("../api/client", () => ({ fetchMeta: vi.fn() }));
import { fetchMeta } from "../api/client";

const META: Meta = {
  people: [
    { id: "p1", name: "김일번", grade: "중급", skills: {} },
    { id: "p2", name: "이이번", grade: "중급", skills: {} },
  ],
  projects: [
    { id: "j1", name: "J1", sector: "대내", phase: "실행",
      start_month: 0, end_month: 1, grade_headcount: {}, monthly_budget: 0 },
  ],
  skills: [],
  review_items: [],
  coworks: [],
};

describe("ReportPage", () => {
  afterEach(() => {
    delete (window as any).__REPORT_DATA__;
    delete (window as any).__REPORT_READY__;
  });

  it("data가 없으면 __REPORT_READY__를 세우지 않는다 (빈 PDF 방지)", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    window.__REPORT_DATA__ = undefined;

    render(<ReportPage />);

    await waitFor(() => expect(fetchMeta).toHaveBeenCalled());
    // meta가 정말로 해소되고 그 뒤의 useEffect가 실행될 시간을 준다 --
    // 그렇지 않으면 이 단언이 effect가 돌기도 전에 통과해버려 게이팅이
    // 실제로 걸려 있는지 검증하지 못한다.
    await new Promise((r) => setTimeout(r, 20));

    expect(screen.getByText("리포트 데이터가 없다.")).toBeInTheDocument();
    expect(window.__REPORT_READY__).not.toBe(true);
  });

  it("fallback_used면 규칙 기반 provenance와 교체 대상·delta를 함께 보인다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    window.__REPORT_DATA__ = {
      plan_label: "A",
      entries: [{ person_id: "p1", project_id: "j1", alloc: 1 }],
      objective: 1, fulfillment: 1, optimization_ratio: 1, unfilled: [],
      briefing: { rationale: "근거", risks: [], alternatives: [] },
      fallback_used: true,
      swap: { out_person_id: "p1", in_person_id: "p2", project_id: "j1" },
      objective_delta: -0.25,
    };

    render(<ReportPage />);

    await waitFor(() => expect(window.__REPORT_READY__).toBe(true));
    expect(screen.getByText(/규칙 기반\(결정론적\) 생성기/)).toBeInTheDocument();
    expect(screen.getByText(/검토한 교체: 김일번 → 이이번/)).toBeInTheDocument();
    expect(screen.getByText(/현행 점수 기준 Δ -0.2500/)).toBeInTheDocument();
    expect(screen.queryByText(/교체 검토 경고/)).not.toBeInTheDocument();
  });

  it("교체 검토 경고를 PDF에도 보인다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    window.__REPORT_DATA__ = {
      plan_label: "A", entries: [], objective: 1, fulfillment: 1,
      optimization_ratio: 1, unfilled: [],
      briefing: { rationale: "근거", risks: [], alternatives: [] },
      fallback_used: false,
      swap: { out_person_id: "p1", in_person_id: "p2", project_id: "j1" },
      objective_delta: -100.5,
      swap_violations: ["j1 월 비용 6,000이 예산 5,000을 초과", "j1의 중급 1명 미충원"],
    };

    render(<ReportPage />);

    await waitFor(() => expect(window.__REPORT_READY__).toBe(true));
    expect(screen.getByText(/교체 검토 경고/)).toBeInTheDocument();
    expect(screen.getByText("j1 월 비용 6,000이 예산 5,000을 초과")).toBeInTheDocument();
    expect(screen.getByText("j1의 중급 1명 미충원")).toBeInTheDocument();
  });

  it("LLM이 쓰였으면 provenance 문구가 없다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    window.__REPORT_DATA__ = {
      plan_label: "A", entries: [], objective: 1, fulfillment: 1,
      optimization_ratio: 1, unfilled: [],
      briefing: { rationale: "근거", risks: [], alternatives: [] },
      fallback_used: false, swap: null, objective_delta: null,
    };

    render(<ReportPage />);

    await waitFor(() => expect(window.__REPORT_READY__).toBe(true));
    expect(screen.queryByText(/규칙 기반\(결정론적\)/)).not.toBeInTheDocument();
  });
});
