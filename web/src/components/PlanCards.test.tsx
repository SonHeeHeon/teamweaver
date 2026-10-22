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

  it("빈자리가 있으면 '빈자리 n석'을 따로, 큰 숫자는 빈자리 감점을 뺀 배치 품질(2026-10-22)", () => {
    const p = { ...plan("A", 0.9), objective: -12.51, unfilled: ["j00:중급:1명 미충원"], slack_penalty: 100 };
    render(<PlanCards plans={[p]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("1석")).toBeInTheDocument();
    expect(screen.getByText("87.49")).toBeInTheDocument();               // 배치 품질
    expect(screen.getByText("-12.51")).toBeInTheDocument();              // 종합 점수는 작게 함께
    expect(screen.queryByText("가장 높음")).not.toBeInTheDocument();     // 안이 하나면 견주지 않는다
  });

  it("빈자리가 없으면 종합 점수 줄을 따로 보이지 않는다(배치 품질과 같은 숫자)", () => {
    render(<PlanCards plans={[plan("A", 0.9)]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("12.30")).toBeInTheDocument();
    expect(screen.queryByText("종합 점수 ⓘ")).not.toBeInTheDocument();
    expect(screen.getByText("없음")).toBeInTheDocument();
  });

  it("사용자 사례: A -12.51 · B -12.29(둘 다 빈자리 1석) -- 가장 높은 안은 B, A는 1% 안이라 사실상 같은 수준이라고 알린다", () => {
    const a = { ...plan("A", 0.886), objective: -12.51, unfilled: ["j00:중급:1명 미충원"], slack_penalty: 100 };
    const b = { ...plan("B", 0.892), objective: -12.29, unfilled: ["j01:초급:1명 미충원"], slack_penalty: 100 };
    render(<PlanCards plans={[a, b]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("가장 높음").closest("button")).toHaveTextContent("Plan B");
    expect(screen.getByText("먼저 계산한 기준안")).toBeInTheDocument();
    expect(screen.getByText("가장 높은 안(B)과 0.22점 차이 — 1% 안, 사실상 같은 수준")).toBeInTheDocument();
    expect(screen.queryByText(/계산 오차/)).not.toBeInTheDocument();      // 솔버 허용 차이는 종합 점수 기준이라 "계산 오차"라 부르지 않는다(폴백 리뷰 S1)
  });

  it("A가 시간 한도에서 멈춘 해면 설명에 그 이유를, 빈자리가 있으면 배지 차이는 %가 아니라 점수로(폴백 리뷰 S1·S3)", () => {
    const a = { ...plan("A", 0.886), objective: -12.51, unfilled: ["j00:중급:1명 미충원"], slack_penalty: 100,
                termination: "time_limit_incumbent", time_limited: true, best_bound: -11.0, gap_allowed: 0.01 };
    const b = { ...plan("B", 0.892), objective: -12.29, unfilled: ["j01:초급:1명 미충원"], slack_penalty: 100 };
    render(<PlanCards plans={[a, b]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("먼저 계산한 기준안")).toHaveAttribute("title", expect.stringContaining("계산 시간 한도에서 멈춘 해"));
    expect(screen.getByText(/시간 한도 도달 · 최적값이 이 해보다 최대 1\.51점 높을 수 있음/)).toBeInTheDocument();
    expect(screen.queryByText(/최대 12/)).not.toBeInTheDocument();
  });

  it("화면에서 바꿔 제약 위반이 생긴 안은 '가장 높음'에서 빼고 위반 수를 보인다(폴백 리뷰 S2)", () => {
    const a = { ...plan("A", 0.9), objective: 10 };
    const b = { ...plan("B", 0.9), objective: 12 };
    render(<PlanCards plans={[a, b]} selected="A" onSelect={() => {}} editCounts={{ B: 1 }} violationCounts={{ B: 2 }} />);
    expect(screen.getByText("가장 높음").closest("button")).toHaveTextContent("Plan A");
    expect(screen.getByText("제약 위반 2건").closest("button")).toHaveTextContent("Plan B");
    expect(screen.queryByText(/가장 높은 안/)).not.toBeInTheDocument();    // 위반 안에 "−2.00점 차이 — 같은 수준"을 찍지 않는다(2차 리뷰 M1)
  });

  it("위반 안이 빈자리가 더 적어도 '빈자리 적지만 종합 점수는 낮음'이라고 하지 않는다(2차 리뷰 M1)", () => {
    const c = { ...plan("C", 0.9), objective: 50, unfilled: ["j00:중급:1명 미충원"] };
    const d = { ...plan("D", 0.9), objective: 95, unfilled: [] };
    render(<PlanCards plans={[c, d]} selected="C" onSelect={() => {}} editCounts={{ D: 1 }} violationCounts={{ D: 1 }} />);
    expect(screen.getByText("가장 높음").closest("button")).toHaveTextContent("Plan C");
    expect(screen.queryByText(/가장 높은 안/)).not.toBeInTheDocument();
  });

  it("가장 높은 안보다 배치 품질이 높은 안이 있으면(빈자리가 더 많아서) 배지에 이유를 보인다(2차 리뷰 S1)", () => {
    const a = { ...plan("A", 0.9), objective: -12.5, unfilled: ["j00:중급:1명 미충원"], slack_penalty: 100 };   // 품질 87.5
    const b = { ...plan("B", 0.9), objective: 80, unfilled: [], slack_penalty: 100 };                          // 품질 80
    render(<PlanCards plans={[a, b]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("가장 높음 · 빈자리 적음").closest("button")).toHaveTextContent("Plan B");
    expect(screen.getByText("가장 높은 안(B)보다 빈자리 1석 많음")).toBeInTheDocument();
  });

  it("계산 중에는 '가장 높음'·차이 안내를 숨긴다(대안이 도착하며 옮겨 다니지 않게)", () => {
    render(<PlanCards plans={[{ ...plan("A", 0.9), objective: 10 }, { ...plan("B", 0.9), objective: 12 }]}
                      selected="A" onSelect={() => {}} settled={false} />);
    expect(screen.queryByText("가장 높음")).not.toBeInTheDocument();
    expect(screen.queryByText(/가장 높은 안/)).not.toBeInTheDocument();
  });

  it("감점이 100이 아니면 그 값으로 품질을 셈하고, 빈자리가 더 적은데 종합 점수가 낮은 안은 그렇다고", () => {
    const a = { ...plan("A", 0.9), objective: 70, unfilled: ["j00:중급:1명 미충원"], slack_penalty: 10 };   // 품질 80
    const b = { ...plan("B", 0.9), objective: 60, unfilled: [], slack_penalty: 10 };                        // 품질 60
    render(<PlanCards plans={[a, b]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("80.00")).toBeInTheDocument();
    expect(screen.getAllByText("빈자리 ⓘ")[0]).toHaveAttribute("title", expect.stringContaining("1자리 감점 10)"));
    expect(screen.getByText("가장 높은 안(A)보다 빈자리 1석 적지만 종합 점수는 낮음")).toBeInTheDocument();
  });

  it("빈자리가 더 많은 안은 그렇다고, 1% 넘는 품질 차이는 숫자로", () => {
    const a = { ...plan("A", 0.9), objective: 90, unfilled: [] };
    const b = { ...plan("B", 0.9), objective: -15, unfilled: ["j00:중급:1명 미충원"] };
    const c = { ...plan("C", 0.9), objective: 80, unfilled: [] };
    render(<PlanCards plans={[a, b, c]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("가장 높은 안(A)보다 빈자리 1석 많음")).toBeInTheDocument();
    expect(screen.getByText("가장 높은 안(A)보다 품질 10.00 낮음")).toBeInTheDocument();
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

  it("밤 배치가 풀어 둔 결과는 '밤사이 계산 · 시각'으로 밝힌다(2026-10-19)", () => {
    const night = { ...plan("A", 0.9), cached: true, precomputed_at: "2026-10-19T01:40:00+00:00", precomputed_source: "nightly" };
    render(<PlanCards plans={[night]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText(/^밤사이 계산 · \d+월 \d+일 \d\d:\d\d$/)).toBeInTheDocument();
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


describe("PlanCards — 검증 도장(2026-10-18)", () => {
  it("화면에서 바꾼 명단에는 '위반 없음' 도장을 달지 않는다(문구 2026-10-22 P3-5 -- 예전 '검증 통과')", async () => {
    const { render, screen } = await import("@testing-library/react");
    const { PlanCards } = await import("./PlanCards");
    const plan = { label: "A", entries: [], objective: 1, unfilled: [], fulfillment: 0.5, optimization_ratio: 0.5,
                   index: 1, cached: false, termination: "Optimal" } as never;
    const { rerender } = render(<PlanCards plans={[plan]} selected="A" onSelect={() => {}} />);
    expect(screen.getByText("검증기 확인")).toBeInTheDocument();
    rerender(<PlanCards plans={[plan]} selected="A" onSelect={() => {}} editCounts={{ A: 1 }} />);
    expect(screen.queryByText("검증기 확인")).not.toBeInTheDocument();
  });
  it("지금 배치가 있으면 안마다 지금 대비 옮김·새로·빠짐을 보인다(2026-10-21 -- 운영 중 안은 모두를 다시 짠 결과)", () => {
    const p = { ...plan("A", 0.9), entries: [{ person_id: "a", project_id: "J2", alloc: 1 }, { person_id: "c", project_id: "J1", alloc: 1 }] };
    const base = [{ person_id: "a", project_id: "J1", alloc: 1 }, { person_id: "b", project_id: "J1", alloc: 1 }];
    const { rerender } = render(<PlanCards plans={[p]} selected="A" onSelect={() => {}} baseline={base} />);
    expect(screen.getByText("지금 대비 옮김 1명 · 새로 1명 · 빠짐 1명")).toBeInTheDocument();
    rerender(<PlanCards plans={[p]} selected="A" onSelect={() => {}} baseline={[]} />);   // 백지면 비교하지 않는다
    expect(screen.queryByText(/지금 대비/)).toBeNull();
  });
});
