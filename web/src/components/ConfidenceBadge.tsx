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
  /** 카드의 "위반 없음" 도장이 이미 보이면 첫 칩(규칙 위반 없음 확인)을 뺀다. 문구는 부서장 말로(2026-10-22 화면 검토 P3-5 --
   *  예전 "독립 검증 통과"·"서버 재채점 통과"·"기록에서 불러옴 · 증명은 다시 계산 시"). */
  hideVerified?: boolean;
  /** 차이를 %가 아니라 점수로(2026-10-22 폴백 리뷰 S3) -- 빈자리 감점이 섞인 종합 점수는 0 근처라 %가 크게 부풀려진다. */
  inPoints?: boolean;
}

export function ConfidenceBadge({ termination, timeLimited, objective, bestBound, gapAllowed, within, hideVerified, inPoints }: Props) {
  const x = gapPct(objective, bestBound);
  const pts = objective != null && bestBound != null && Number.isFinite(objective) && Number.isFinite(bestBound)
    ? Math.abs(bestBound - objective) : null;
  const upTo = inPoints ? (pts != null ? `${pts.toFixed(2)}점` : null) : (x != null ? pct(x) : null);
  // 편성 기록에서 연 안(2026-10-18): 서버가 지금 평가기로 다시 채점해 제약 위반이 없음을 확인했다. 솔버 증명은 기록이 주장할 뿐
  // 이 서버가 확인할 수 없어 버렸다(api/records) -- "다시 계산"하면 증명이 붙는다.
  if (termination === "record") {
    return (
      <span className="inline-flex flex-wrap gap-1 text-[11px]">
        {!hideVerified && <span className="rounded border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-sky-800"
              title="편성 기록의 명단을 지금 평가기로 다시 채점해 제약 위반이 없음을 확인했다">규칙 위반 없음 확인(다시 채점)</span>}
        <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-slate-600"
              title="기록이 주장하는 솔버 증명은 이 서버가 확인할 수 없어 버렸다 -- 다시 계산하면 최적값과의 거리가 붙는다">
          기록에서 연 결과 · 최적인지는 다시 계산해야 확인
        </span>
      </span>
    );
  }
  const stopped = termination === "user_interrupt";                 // 사용자가 "계산 멈추기"로 멈춘 해(2026-10-12)
  const limited = !stopped && (timeLimited || termination === "time_limit_incumbent");
  const optimal = !limited && !stopped && termination === "Optimal";
  let text: string;
  let tone: string;
  if (stopped) {
    text = upTo != null ? `사용자가 멈춤 · ${within ? `${within}의 ` : ""}최적값이 이 해보다 최대 ${upTo} 높을 수 있음`
                     : "사용자가 멈춤 · 최선 증명 전";
    tone = "border-amber-300 bg-amber-50 text-amber-800";
  } else if (limited) {
    text = upTo != null ? `시간 한도 도달 · ${within ? `${within}의 ` : ""}최적값이 이 해보다 최대 ${upTo} 높을 수 있음`
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
      {!hideVerified && <span className="rounded border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-sky-800"
            title="솔버의 해를 별도 검증기로 다시 확인했다(제약·목적 재계산)">규칙 위반 없음 확인(검증기)</span>}
      <span className={`rounded border px-1.5 py-0.5 ${tone}`}>{text}</span>
    </span>
  );
}
