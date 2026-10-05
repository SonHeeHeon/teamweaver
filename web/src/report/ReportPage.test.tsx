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
  dataset_version: "a".repeat(64),
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

describe("ReportPage — 계산 기준 표기(K8)", () => {
  afterEach(() => {
    delete (window as any).__REPORT_DATA__;
    delete (window as any).__REPORT_READY__;
  });
  const base = { plan_label: "A", entries: [], objective: 1, fulfillment: 1,
                 optimization_ratio: 1, unfilled: [], briefing: null, fallback_used: false,
                 swap: null, objective_delta: null, swap_violations: [] };

  it("플랜을 계산한 배치 설정을 보여 준다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    window.__REPORT_DATA__ = { ...base, milp_params: { min_alloc: 0.3,
      clique_threshold_months: 6, lam: 0.3, mu: 0.2, time_limit: 120, gap: 0.05, max_concurrent_projects: 1, allocation_mode: "fixed" as const } };
    render(<ReportPage />);
    expect(await screen.findByText(/최소 투입률 30%/)).toBeInTheDocument();
    expect(screen.getByText(/동시 프로젝트 최대 1개/)).toBeInTheDocument();
  });

  it("설정 없이 계산한 플랜은 모델 기본값임을 밝힌다", async () => {
    vi.mocked(fetchMeta).mockResolvedValue(META);
    window.__REPORT_DATA__ = { ...base, milp_params: null };
    render(<ReportPage />);
    expect(await screen.findByText(/서버 모델 기본값/)).toBeInTheDocument();
  });
});

describe("ReportPage — 서버가 넣어 준 meta(K9)", () => {
  afterEach(() => {
    delete (window as any).__REPORT_DATA__;
    delete (window as any).__REPORT_READY__;
  });

  it("PDF 데이터에 meta가 있으면 /api/meta를 부르지 않고 그것으로 이름을 붙인다", async () => {
    vi.mocked(fetchMeta).mockReset();
    window.__REPORT_DATA__ = {
      plan_label: "A", entries: [{ person_id: "p1", project_id: "j1", alloc: 1 }],
      objective: 1, fulfillment: 1, optimization_ratio: 1, unfilled: [], briefing: null,
      fallback_used: false, swap: null, objective_delta: null, swap_violations: [],
      milp_params: null, dataset_version: META.dataset_version, meta: META };
    render(<ReportPage />);
    expect((await screen.findAllByText("김일번")).length).toBeGreaterThan(0);
    expect(fetchMeta).not.toHaveBeenCalled();
  });
});


describe("ReportPage — 달별 투입률 조정", () => {
  afterEach(() => { delete (window as any).__REPORT_DATA__; delete (window as any).__REPORT_READY__; });

  it("조정 이력과 명단의 달별 요약을 찍는다", async () => {
    vi.mocked(fetchMeta).mockReset();
    const monthly = { "0": 0.5, "1": 0.5, "2": 1, "3": 1 };
    window.__REPORT_DATA__ = {
      plan_label: "A", entries: [{ person_id: "p1", project_id: "j1", alloc: 0.75, monthly_alloc: monthly }],
      objective: 1, fulfillment: 1, optimization_ratio: 0.9, unfilled: [], briefing: null,
      fallback_used: false, swap: null, objective_delta: null, swap_violations: [],
      milp_params: null, dataset_version: META.dataset_version, meta: META,
      applied_swaps: [{ kind: "alloc", person_id: "p1", project_id: "j1", monthly_alloc: monthly,
                        objective_delta: -0.1, feasible: true, warnings: [] }],
      applied_violations: [], plan_provenance: "unverified" };
    render(<ReportPage />);
    expect(await screen.findByText("적용된 변경 1건")).toBeInTheDocument();
    expect(screen.getByText("투입률 조정: 김일번 1~2월 50%, 3~4월 100%")).toBeInTheDocument();
  });
});


describe("ReportPage — 적용된 교체(K10)", () => {
  afterEach(() => {
    delete (window as any).__REPORT_DATA__;
    delete (window as any).__REPORT_READY__;
  });

  it("적용한 교체 목록·경고와 적용 후 위반을 보여 준다", async () => {
    vi.mocked(fetchMeta).mockReset();
    window.__REPORT_DATA__ = {
      plan_label: "A", entries: [{ person_id: "p2", project_id: "j1", alloc: 1 }],
      objective: 1, fulfillment: 1, optimization_ratio: null, unfilled: [], briefing: null,
      fallback_used: false, swap: null, objective_delta: null, swap_violations: [],
      milp_params: null, dataset_version: META.dataset_version, meta: META,
      applied_swaps: [{ out_person_id: "p1", in_person_id: "p2", project_id: "j1",
                        objective_delta: -0.25, feasible: false,
                        warnings: ["j1 0월 예산 초과"] }],
      applied_violations: ["j1 0월 예산 초과(최종)"], plan_provenance: "unverified" };
    render(<ReportPage />);
    expect(await screen.findByText("적용된 교체 1건")).toBeInTheDocument();
    expect(screen.getByText(/최적화가 고른 명단이/)).toBeInTheDocument();
    expect(screen.getByText("김일번 → 이이번")).toBeInTheDocument();
    expect(screen.getByText("-0.2500")).toBeInTheDocument();
    expect(screen.getByText("j1 0월 예산 초과(최종)")).toBeInTheDocument();
    expect(screen.getByText("산정 불가(제약 위반)")).toBeInTheDocument();
    expect(screen.getByText(/서버 계산 여부 미검증/)).toBeInTheDocument();
  });
});


describe("ReportPage — 원 플랜 서명", () => {
  afterEach(() => { delete (window as any).__REPORT_DATA__; delete (window as any).__REPORT_READY__; });
  it("서버가 서명을 확인한 플랜이면 그렇게 밝힌다", async () => {
    vi.mocked(fetchMeta).mockReset();
    window.__REPORT_DATA__ = { plan_label: "A", entries: [], objective: 1, fulfillment: 1,
      optimization_ratio: 1, unfilled: [], briefing: null, fallback_used: false, swap: null,
      objective_delta: null, swap_violations: [], milp_params: null, meta: META,
      dataset_version: META.dataset_version, applied_swaps: [], applied_violations: [],
      plan_provenance: "verified" };
    render(<ReportPage />);
    expect(await screen.findByText(/서명 확인/)).toBeInTheDocument();
  });
});


describe("ReportPage — 브리핑 근거(K5)", () => {
  afterEach(() => { delete (window as any).__REPORT_DATA__; delete (window as any).__REPORT_READY__; });
  it("PDF에도 근거 종류·출처를 찍는다", async () => {
    vi.mocked(fetchMeta).mockReset();
    window.__REPORT_DATA__ = { plan_label: "A", entries: [], objective: 1, fulfillment: 1,
      optimization_ratio: 1, unfilled: [], fallback_used: true, swap: null, objective_delta: null,
      swap_violations: [], milp_params: null, meta: META, dataset_version: META.dataset_version,
      applied_swaps: [], applied_violations: [],
      briefing: { rationale: "근거 [rv:p1>p2#1:pos]", risks: [], alternatives: [],
                  evidence: [{ source_id: "rv:p1>p2#1:pos", reviewer_id: "p1", kind: "quote",
                               text: "꼼꼼하다" }] } };
    render(<ReportPage />);
    expect(await screen.findByText("직접 인용")).toBeInTheDocument();
    expect(screen.getByText(/김일번 리뷰 · rv:p1>p2#1:pos/)).toBeInTheDocument();
    expect(screen.getByText(/검증됐다는 뜻은 아니다/)).toBeInTheDocument();
  });
});
