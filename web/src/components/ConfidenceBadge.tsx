import { gapPct } from "./confidence";

/** 계산 신뢰도 배지(claude-a 요청 2026-10-06).
 *
 *  - 서비스가 내는 해는 모두 독립 검증기(C0, core/optimize/validation)를 통과한 해다 → "독립 검증 통과".
 *  - 최적 종료면 "허용 차이 x% 안에서 최선 증명". 운영 편성처럼 차이 0으로 풀었으면 "최선 증명".
 *  - 시간 한도면 HiGHS가 증명한 상한으로 "최적값이 이 해보다 최대 X% 높을 수 있음"(X = |상한−해|/|해|, HiGHS gap 정의
 *    -- 분모가 해라서 "상한 대비"라고 쓰지 않는다, 리뷰 S6). 대안 플랜은 다양성 조건 안에서의 증명이다. */
function pct(x: number): string {
  return x < 0.05 ? "0%" : x < 10 ? `${x.toFixed(1)}%` : `${Math.round(x)}%`;
}

interface Props {
  termination?: string | null;
  timeLimited?: boolean;
  objective?: number | null;
  bestBound?: number | null;
  /** 허용 차이(0~1). 0이면 "최선 증명". */
  gapAllowed?: number | null;
  /** 증명이 붙은 조건(예: 대안 플랜의 "다양성 조건 안에서"). */
  within?: string;
}

export function ConfidenceBadge({ termination, timeLimited, objective, bestBound, gapAllowed, within }: Props) {
  const x = gapPct(objective, bestBound);
  const limited = timeLimited || termination === "time_limit_incumbent";
  const optimal = !limited && termination === "Optimal";
  let text: string;
  let tone: string;
  if (limited) {
    text = x != null ? `시간 한도 도달 · ${within ? `${within}의 ` : ""}최적값이 이 해보다 최대 ${pct(x)} 높을 수 있음`
                     : "시간 한도 도달 · 최선 증명 전";
    tone = "border-amber-300 bg-amber-50 text-amber-800";
  } else if (optimal) {
    text = (within ? `${within} · ` : "") + (!gapAllowed ? "최선 증명" : `허용 차이 ${pct(gapAllowed * 100)} 안에서 최선 증명`);
    tone = "border-emerald-300 bg-emerald-50 text-emerald-800";
  } else {
    text = termination ? `종료: ${termination}` : "증명 정보 없음";
    tone = "border-slate-200 bg-slate-50 text-slate-600";
  }
  return (
    <span className="inline-flex flex-wrap gap-1 text-[11px]">
      <span className="rounded border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-sky-800"
            title="솔버의 해를 별도 검증기로 다시 확인했다(제약·목적 재계산)">독립 검증 통과</span>
      <span className={`rounded border px-1.5 py-0.5 ${tone}`}>{text}</span>
    </span>
  );
}
