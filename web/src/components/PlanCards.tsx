import type { AssignEntry, PlanEvent } from "../api/types";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { formatPrecomputedAt } from "./precomputed";
import { diffText, rosterDiff } from "./rosterDiff";
import { bestLabel, DEFAULT_SLACK_PENALTY, gapToBest, qualityOf, unfilledSeats } from "./planScore";

interface Props {
  plans: PlanEvent[];
  selected: string | null;
  onSelect: (label: string) => void;
  /** 플랜별 화면에서 적용한 변경(교체·투입률 조정) 수 -- 저장 결과 배지를 "원안 + 변경 n건"으로 바꾼다. */
  editCounts?: Record<string, number>;
  /** 지금 배치(작업 데이터, 비었으면 백지). 주면 안마다 "지금 대비 옮김·새로·빠짐"(2026-10-21 화면 검토 P1-2 -- 운영 중 안 A~D는 모두를 다시 짠 결과다) */
  baseline?: AssignEntry[];
  /** 화면에서 바꾼 명단의 제약 위반 수 -- 위반이 있는 안은 "가장 높음" 비교에서 뺀다(폴백 리뷰 S2) */
  violationCounts?: Record<string, number>;
  /** 계산이 끝났나 -- 대안이 도착하는 동안에는 "가장 높음"이 옮겨 다니지 않게 비교를 숨긴다 */
  settled?: boolean;
}

const pct = (v: number | null) => (v === null ? "산정 불가" : `${(v * 100).toFixed(1)}%`);

/** 안 이름 아래 한 줄의 설명. 이름 순서 = 계산 순서이지 점수 순서가 아니다(2026-10-22 사용자 "a안이 b안보다 낮은데 a안인 이유"). */
function captionTitle(p: PlanEvent, edited: number): string {
  if (p.label !== "A") {
    return edited ? "앞의 안과 명단이 다르게 다시 푼 대안을 화면에서 바꾼 것입니다(바꾼 뒤 다시 채점한 값)."
                  : "앞의 안과 명단이 충분히 다르게 다시 푼 대안입니다(종합 점수가 A안보다 5% 넘게 낮지 않고 빈자리가 A안보다 많지 않은 것만).";
  }
  const head = "가장 먼저 계산한 안입니다(이름 순서 = 계산 순서, 점수 순서가 아님).";
  if (edited) return `${head} 화면에서 바꾼 명단은 다시 채점한 값입니다.`;
  if (p.termination === "record") return `${head} 편성 기록에서 불러온 안입니다.`;
  if (p.termination === "user_interrupt") return `${head} 계산을 멈춘 해라 뒤에 푼 안이 더 높을 수 있습니다.`;
  if (p.time_limited || p.termination === "time_limit_incumbent") return `${head} 계산 시간 한도에서 멈춘 해라 뒤에 푼 안이 더 높을 수 있습니다.`;
  if (p.gap_allowed) return `${head} 허용 차이(종합 점수의 ${(p.gap_allowed * 100).toFixed(1)}%) 안에서 멈춘 해라 뒤에 푼 안이 그만큼 높을 수 있습니다.`;
  return head;
}

/** 편성안 카드(2026-10-18 개편 -- 이 화면에서 힘을 준 한 곳). 큰 글자와 인주색 "검증" 도장으로 "솔버 답을 서버가 다시
 *  검사해 통과했다"를 한눈에. 주 지표는 전체 목적값(협업·익숙함·빈자리까지 포함한 점수) -- 기술항 비율(최적화율)과 헷갈리지
 *  않게 둘을 나눠 적는다(Codex 평가 2026-10-18 UX 지적). */
export function PlanCards({ plans, selected, onSelect, editCounts = {}, baseline, violationCounts = {}, settled = true }: Props) {
  // 가장 높은 위반 없는 안(종합 점수 = 빈자리 적은 안이 먼저, 같으면 품질). A안은 먼저 계산한 기준안이라 늘 가장 높지는 않다(2026-10-22)
  const best = settled ? bestLabel(plans.map((p) => ({ ...p, violations: violationCounts[p.label] ?? 0 }))) : null;
  const bestPlan = plans.find((p) => p.label === best);
  // 가장 높은 안보다 배치 품질(큰 숫자)이 높은 안이 있으면 = 그 안들은 빈자리가 더 많다 -- 배지에 이유를 눈에 보이게(2차 폴백 리뷰 S1)
  const fewerSeats = bestPlan != null && plans.some((p) => p.label !== best && !(violationCounts[p.label] > 0)
                                                          && qualityOf(p) > qualityOf(bestPlan) + 1e-9);
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {plans.map((p) => {
        const on = selected === p.label;
        const fromRecord = p.termination === "record";
        const edited = editCounts[p.label] ?? 0;
        const seats = unfilledSeats(p.unfilled);
        const penalty = p.slack_penalty ?? DEFAULT_SLACK_PENALTY;
        const violations = violationCounts[p.label] ?? 0;
        // 위반 안은 가장 높은 안 비교에서 뺐으니 차이 안내도 하지 않는다(종합 점수가 더 높을 수 있다, 2차 폴백 리뷰 M1)
        const gap = bestPlan && p.label !== best && !(violationCounts[p.label] > 0) ? gapToBest(p, bestPlan) : null;
        return (
          <button key={p.label} onClick={() => onSelect(p.label)} aria-pressed={on}
                  className={`rounded-lg bg-paper p-4 text-left ${on ? "border-2 border-ink p-[15px]" : "border border-line hover:border-slate-400"}`}>
            <div className="flex items-start justify-between gap-2">
              <span>
                <span className="text-plan text-ink" aria-hidden>{p.label}</span>
                <span className="sr-only">Plan {p.label}</span>
                {plans.length > 1 && (
                  <span className="block text-[11px] text-slate-500" title={captionTitle(p, edited)}>
                    {p.label === "A" ? "먼저 계산한 기준안" : "A와 다른 명단"}</span>
                )}
                {p.label === best && (
                  <span className="mt-1 inline-block rounded-full bg-emerald-100 px-2 text-[11px] font-semibold text-emerald-800"
                        title="종합 점수 기준 — 빈자리가 적은 안이 먼저, 같으면 배치 품질이 높은 안(제약 위반이 있는 안은 뺌)">
                    {fewerSeats ? "가장 높음 · 빈자리 적음" : "가장 높음"}<span className="sr-only">(종합 점수 기준 — 빈자리가 적은 안이 먼저, 같으면 배치 품질)</span></span>
                )}
              </span>
              {/* 화면에서 바꾼 명단은 검증한 원안이 아니다 -- 도장을 물려주지 않는다(Codex 리뷰 P2) */}
              {!edited && (
                <span className="seal" title={fromRecord ? "편성 기록의 명단을 지금 평가기로 다시 채점해 위반이 없음을 확인했다"
                                                         : "솔버의 해를 별도 검증기로 다시 확인했다(제약·목적 재계산)"}>
                  {fromRecord ? "재채점" : "검증"}<small className="block text-[9.5px] font-semibold">통과</small>
                </span>
              )}
            </div>
            <dl className="num mt-3 grid grid-cols-[1fr_auto] gap-y-0.5 text-[13px]">
              <dt className="cursor-help text-slate-500" title="배치 품질 = 기술 적합 + 협업 보상 − 오래 함께한 사이 감점(빈자리 감점은 빼고 봅니다). 빈자리는 따로 셉니다. 높을수록 좋은 편성안입니다.">배치 품질 ⓘ</dt>
              <dd className="text-lg font-semibold text-slate-900">{qualityOf(p).toFixed(2)}</dd>
              <dt className="cursor-help text-slate-500" title={`모든 사업에서 정원을 채우지 못한 자리 수. 계산은 자리를 비우지 않는 것을 가장 먼저 지킵니다(1자리 감점 ${penalty}).`}>빈자리 ⓘ</dt>
              <dd className={seats > 0 ? "font-semibold text-red-700" : "text-slate-500"}>{seats > 0 ? `${seats}석` : "없음"}</dd>
              {/* 빈자리가 없으면 종합 점수 = 배치 품질이라 같은 숫자를 두 번 보이지 않는다 */}
              {seats > 0 && <>
                <dt className="cursor-help text-slate-500" title={`종합 점수 = 배치 품질 − 빈자리 감점(1자리 ${penalty}). 안을 고르고 줄 세우는 값입니다 -- 빈자리가 적은 안이 먼저, 같으면 품질.`}>종합 점수 ⓘ</dt>
                <dd className="text-slate-500">{p.objective.toFixed(2)}</dd>
              </>}
              <dt className="cursor-help text-slate-500" title="기술만 놓고 이론상 가장 잘 맞출 수 있는 값(상한) 대비 이 편성안이 얻은 비율. 100%에 가까울수록 기술이 잘 맞습니다.">기술 적합(최적화율) ⓘ</dt><dd className="font-semibold">{pct(p.optimization_ratio)}</dd>
              <dt className="cursor-help text-slate-500" title="사업이 요구한 기술·레벨을 배치된 사람이 실제로 채운 비율(보조 지표). 요구가 까다로울수록 낮게 나옵니다.">충족률 ⓘ</dt><dd>{pct(p.fulfillment)}</dd>
              <dt className="text-slate-500">배치</dt><dd>{p.entries.length}건</dd>
            </dl>
            {violations > 0 && (
              <p className="mt-2 text-xs font-semibold text-red-700"
                 title="화면에서 바꾼 명단이 제약(정원·예산·가용률 등)을 어깁니다. '가장 높음' 비교에서 뺐습니다.">
                제약 위반 {violations}건</p>
            )}
            {baseline && baseline.length > 0 && (
              <p className="mt-2 rounded bg-sky-50 px-2 py-1 text-xs text-sky-900"
                 title="이 안은 모두를 처음부터 다시 짠 결과입니다. 지금 실제 배치와 비교해 사업이 바뀌는 사람 수입니다.">
                지금 대비 {diffText(rosterDiff(baseline, p.entries))}</p>
            )}
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
                {/* 빈자리가 있으면 종합 점수가 0 근처라 %가 부풀려진다 -- 점수로 보인다(폴백 리뷰 S3) */}
                <ConfidenceBadge termination={p.termination} timeLimited={p.time_limited} objective={p.objective}
                                 bestBound={p.best_bound} gapAllowed={p.gap_allowed} hideVerified inPoints={seats > 0}
                                 within={p.label !== "A" ? "다양성 조건" : undefined} />
              </div>
            )}
            {!edited && p.time_limited && p.termination === undefined && (
              <p className="mt-2 text-xs text-amber-600" title="계산 시간(안 하나당)에 걸려 멈춘 답이다. 더 좋은 답이 있을 수 있다.">
                시간 한도 도달(최선 증명 전)
              </p>
            )}
            {gap && (
              <p className={`mt-2 text-xs ${gap.sameLevel ? "text-slate-500" : "text-amber-700"}`}
                 title="가장 높은 안과의 배치 품질 차이. 1% 안이면 사실상 같은 수준으로 봅니다 -- 계산은 시간 한도·허용 차이에서 멈춰 안마다 조금씩 다르고, 점수 계수는 실제 성과로 맞춘 값이 아닌 가정값입니다.">
                {gap.moreUnfilled > 0 ? `가장 높은 안(${best})보다 빈자리 ${gap.moreUnfilled}석 많음`
                  : gap.moreUnfilled < 0 ? `가장 높은 안(${best})보다 빈자리 ${-gap.moreUnfilled}석 적지만 종합 점수는 낮음`
                    : gap.sameLevel ? `가장 높은 안(${best})과 ${gap.qualityDiff.toFixed(2)}점 차이 — 1% 안, 사실상 같은 수준`
                      : `가장 높은 안(${best})보다 품질 ${gap.qualityDiff.toFixed(2)} 낮음`}
              </p>
            )}
          </button>
        );
      })}
    </div>
  );
}
