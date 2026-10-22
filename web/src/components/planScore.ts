/** 편성안 카드 점수 표시(2026-10-22 사용자 "a안이 b안보다 점수가 더 낮은데 a안인 이유" · "빈자리가 -100점인게 너무 크리티컬").
 *  - 배치 품질 = 종합 점수 + 빈자리 감점 × 빈자리 수 = 기술 적합 + 협업 보상 − 오래 함께한 사이 감점(빈자리 감점 뺀 값).
 *    빈자리는 따로 센다 -- 계산·안 순서는 그대로(종합 점수 = 빈자리 적은 안이 먼저, 같으면 품질).
 *  - A안은 "가장 높은 안"이 아니라 먼저 계산한 기준안이다(허용 차이 1%로 풀되 시간 한도가 있다 -- core/optimize/alternatives).
 *    그래서 가장 높은 안을 따로 표시하고, 품질 1% 안 차이는 "사실상 같은 수준"으로 알린다.
 *    이 1%는 표시 기준이다 -- 솔버의 허용 차이(종합 점수 기준)와 분모가 달라 "계산 오차"라고 부르지 않는다(폴백 리뷰 S1). */

/** 기본 빈자리 감점(서버 `MilpParams.slack_penalty` 기본) -- 예전 응답에 칸이 없을 때만 쓴다 */
export const DEFAULT_SLACK_PENALTY = 100;
/** 배치 품질 차이가 이 비율 안이면 "사실상 같은 수준"(사용자 결정 2026-10-22, 점수 계수가 가정값이라 이 정도 차이에 의미를 두기 어렵다) */
export const SAME_LEVEL = 0.01;

// "J007:중급:1명 미충원" -- 사업 ID에 ':'가 있어도 뒤의 "n명"만 본다
const UNFILLED = /:(\d+)명[^:]*$/;

/** 미충원 표기의 자리 수 합. 형식이 다르면 한 줄을 1자리로 센다(0으로 숨기지 않게). */
export function unfilledSeats(unfilled: string[]): number {
  return unfilled.reduce((s, u) => s + (Number(UNFILLED.exec(u)?.[1] ?? 1) || 1), 0);
}

export interface ScoredPlan {
  label: string; objective: number; unfilled: string[]; slack_penalty?: number;
  /** 화면에서 바꾼 명단의 제약 위반 수 -- 위반이 있는 안은 "가장 높음" 비교에서 뺀다(폴백 리뷰 S2) */
  violations?: number;
}

/** 배치 품질(빈자리 감점 뺀 점수) */
export function qualityOf(p: ScoredPlan): number {
  return p.objective + (p.slack_penalty ?? DEFAULT_SLACK_PENALTY) * unfilledSeats(p.unfilled);
}

/** 종합 점수가 가장 높은 위반 없는 안. 안이 하나뿐이거나 위반 없는 안이 없으면 null(견줄 게 없다). */
export function bestLabel(plans: ScoredPlan[]): string | null {
  if (plans.length < 2) return null;
  const ok = plans.filter((p) => !(p.violations && p.violations > 0));
  if (ok.length === 0) return null;
  return ok.reduce((b, p) => (p.objective > b.objective + 1e-9 ? p : b)).label;
}

/** 가장 높은 안과의 차이. 빈자리 수가 같고 품질 차이가 1% 안이면 sameLevel(사실상 같은 수준).
 *  moreUnfilled < 0 = 가장 높은 안보다 빈자리가 적다(감점을 낮게 바꾼 계산에서만 생긴다). */
export function gapToBest(p: ScoredPlan, best: ScoredPlan): { qualityDiff: number; pct: number; sameLevel: boolean; moreUnfilled: number } {
  const qb = qualityOf(best), qp = qualityOf(p);
  const moreUnfilled = unfilledSeats(p.unfilled) - unfilledSeats(best.unfilled);
  const diff = qb - qp;
  // 품질이 0 근처면 비율이 뜻이 없다 -- 차이가 있으면 같은 수준으로 보지 않는다
  const pct = Math.abs(qb) > 1e-9 ? diff / Math.abs(qb) : (diff > 1e-9 ? Infinity : 0);
  // 가장 높은 안보다 품질이 높은 안(위반 안 등)은 "같은 수준"이 아니다(2차 폴백 리뷰 M1)
  return { qualityDiff: diff, pct, sameLevel: moreUnfilled === 0 && diff >= -1e-9 && pct <= SAME_LEVEL, moreUnfilled };
}
