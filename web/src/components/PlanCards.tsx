import type { PlanEvent } from "../api/types";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { formatPrecomputedAt } from "./precomputed";

interface Props {
  plans: PlanEvent[];
  selected: string | null;
  onSelect: (label: string) => void;
  /** 플랜별 화면에서 적용한 변경(교체·투입률 조정) 수 -- 저장 결과 배지를 "원안 + 변경 n건"으로 바꾼다. */
  editCounts?: Record<string, number>;
}

const pct = (v: number | null) => (v === null ? "산정 불가" : `${(v * 100).toFixed(1)}%`);

/** 편성안 카드(2026-10-18 개편 -- 이 화면에서 힘을 준 한 곳). 큰 글자와 인주색 "검증" 도장으로 "솔버 답을 서버가 다시
 *  검사해 통과했다"를 한눈에. 주 지표는 전체 목적값(협업·익숙함·빈자리까지 포함한 점수) -- 기술항 비율(최적화율)과 헷갈리지
 *  않게 둘을 나눠 적는다(Codex 평가 2026-10-18 UX 지적). */
export function PlanCards({ plans, selected, onSelect, editCounts = {} }: Props) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {plans.map((p) => {
        const on = selected === p.label;
        const fromRecord = p.termination === "record";
        const edited = editCounts[p.label] ?? 0;
        return (
          <button key={p.label} onClick={() => onSelect(p.label)} aria-pressed={on}
                  className={`rounded-lg bg-paper p-4 text-left ${on ? "border-2 border-ink p-[15px]" : "border border-line hover:border-slate-400"}`}>
            <div className="flex items-start justify-between gap-2">
              <span className="text-plan text-ink" aria-hidden>{p.label}</span>
              <span className="sr-only">Plan {p.label}</span>
              {/* 화면에서 바꾼 명단은 검증한 원안이 아니다 -- 도장을 물려주지 않는다(Codex 리뷰 P2) */}
              {!edited && (
                <span className="seal" title={fromRecord ? "편성 기록의 명단을 지금 평가기로 다시 채점해 위반이 없음을 확인했다"
                                                         : "솔버의 해를 별도 검증기로 다시 확인했다(제약·목적 재계산)"}>
                  {fromRecord ? "재채점" : "검증"}<small className="block text-[9.5px] font-semibold">통과</small>
                </span>
              )}
            </div>
            <dl className="num mt-3 grid grid-cols-[1fr_auto] gap-y-0.5 text-[13px]">
              <dt className="cursor-help text-slate-500" title="종합 점수 = 기술 적합 + 협업 보상 − 오래 함께한 사이 감점 − 빈자리 감점(1자리 100). 높을수록 좋은 편성안입니다.">종합 점수 ⓘ</dt>
              <dd className="text-lg font-semibold text-slate-900">{p.objective.toFixed(2)}</dd>
              <dt className="cursor-help text-slate-500" title="기술만 놓고 이론상 가장 잘 맞출 수 있는 값(상한) 대비 이 편성안이 얻은 비율. 100%에 가까울수록 기술이 잘 맞습니다.">기술 적합(최적화율) ⓘ</dt><dd className="font-semibold">{pct(p.optimization_ratio)}</dd>
              <dt className="cursor-help text-slate-500" title="사업이 요구한 기술·레벨을 배치된 사람이 실제로 채운 비율(보조 지표). 요구가 까다로울수록 낮게 나옵니다.">충족률 ⓘ</dt><dd>{pct(p.fulfillment)}</dd>
              <dt className="text-slate-500">배치</dt><dd>{p.entries.length}건</dd>
            </dl>
            {p.precomputed_at != null && !p.precomputed_label ? (
              <p className="mt-2 text-xs text-slate-500" title={formatPrecomputedAt(p.precomputed_at, true)}>
                {edited ? `미리 계산 원안 + 변경 ${edited}건`
                  : `${p.precomputed_source === "nightly" ? "밤사이 계산" : "미리 계산"} · ${formatPrecomputedAt(p.precomputed_at)}`}
              </p>
            ) : p.cached && !p.precomputed_label && (
              <p className="mt-2 text-xs text-slate-400" title="같은 조건으로 앞서 계산해 둔 결과를 다시 보여 준다">
                {edited ? `저장된 결과 + 변경 ${edited}건` : "저장된 결과"}
              </p>
            )}
            {edited ? (
              // 화면에서 교체·조정한 명단은 솔버가 푼 해가 아니다 -- 원안의 증명 배지를 물려주지 않는다(Codex 사후 리뷰 MUST)
              <p className="mt-2 rounded bg-slate-50 px-2 py-1 text-xs text-slate-600"
                 title="솔버의 계산 신뢰도(최선 증명·허용 차이)는 원안에만 해당한다. 변경한 명단은 현행 평가기로 다시 채점한 값이다.">
                변경 후 재평가 · 솔버 증명은 원안 기준
              </p>
            ) : p.termination !== undefined && (
              <div className="mt-2">
                <ConfidenceBadge termination={p.termination} timeLimited={p.time_limited} objective={p.objective}
                                 bestBound={p.best_bound} gapAllowed={p.gap_allowed} hideVerified
                                 within={p.label !== "A" ? "다양성 조건" : undefined} />
              </div>
            )}
            {!edited && p.time_limited && p.termination === undefined && (
              <p className="mt-2 text-xs text-amber-600" title="계산 시간 한도에 걸려 멈춘 답이다. 더 좋은 답이 있을 수 있다.">
                시간 한도 도달(최선 증명 전)
              </p>
            )}
            {p.unfilled.length > 0 && (
              <p className="mt-2 text-xs text-amber-600">미충원 {p.unfilled.length}건</p>
            )}
          </button>
        );
      })}
    </div>
  );
}
