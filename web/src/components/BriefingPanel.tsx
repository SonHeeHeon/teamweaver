import type { ObjectiveBreakdown, WhatifResponse } from "../api/types";
import { swapWarnings } from "../api/whatifWarnings";
import { EvidenceList } from "./EvidenceList";
import { hasMarkers, numberMarkers } from "./evidenceMarkers";
import { Term } from "../ui";

interface Props {
  result: WhatifResponse | null;
  loading: boolean;
  /** 근거의 평가자 ID를 이름으로 보여 줄 때(없으면 ID 그대로). */
  nameOf?: (personId: string) => string;
}

const TERMS: [keyof ObjectiveBreakdown, string][] = [
  ["skill", "기술 적합"],
  ["synergy", "협업 보상(함께 일한 기간·서로 남긴 평가)"],
  ["overfamiliarity", "오래 함께한 사이 감점"],
  ["unfilled", "빈자리 감점(1자리 −100)"],
];

const signed = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;

export function BriefingPanel({ result, loading, nameOf }: Props) {
  if (loading) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
        XAI 브리핑 쓰는 중…
      </div>
    );
  }
  if (!result) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-400">
        빠질 사람과 들어올 사람을 고르고 '교체 효과 보기'를 누르면 점수 변화와 XAI 브리핑이 여기에 나옵니다.
      </div>
    );
  }
  const up = result.objective_delta >= 0;
  const warnings = swapWarnings(result);
  const hasNewViolation = result.new_violations.length > 0;
  const ev = result.briefing.evidence ?? [];
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        {/* "XAI 브리핑" 이름은 그대로(사용자 2026-10-20 "표현이 멋있어서" -- 잠시 "AI 교체 설명"이었다), 뜻은 ⓘ에 */}
        <h3 className="text-sm font-semibold text-slate-900">
          <Term help="XAI = 설명할 수 있는 AI. 이 교체로 점수가 왜 오르거나 내리는지 근거(기술 경력·함께 일한 기간·동료 평가)를 들어 설명합니다. 본문의 [근거 n]은 아래 근거 목록의 번호입니다.">XAI 브리핑</Term>
        </h3>
        {result.fallback_used && (
          <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
            {result.fallback_reason === "external_blocked"
              ? "규칙 기반(실데이터라 외부 AI로 보내지 않음)"
              : "규칙 기반(LLM 미사용)"}
          </span>
        )}
      </div>
      <p className={`mt-2 text-sm font-medium ${up ? "text-emerald-600" : "text-red-600"}`}>
        교체하면 종합 점수 {signed(result.objective_delta)}
      </p>
      <p className="text-xs text-slate-500">
        참고값 · 재최적화 아님 — 전체를 다시 푼 최적 배치가 아니라, 지금 명단에서 두 사람만 바꿔 같은 기준으로 다시 채점한 값입니다.
        종합 점수 = 기술 적합 + 협업 보상 − 감점.
      </p>
      <table className="mt-2 w-full text-xs text-slate-600">
        <tbody>
          {TERMS.map(([key, label]) => (
            <tr key={key}>
              <td className="py-0.5">{label}</td>
              <td className="py-0.5 text-right tabular-nums">
                {signed(result.after[key] - result.before[key])}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {warnings.length > 0 && (
        <div className={`mt-3 rounded border px-3 py-2 text-sm
                         ${hasNewViolation
                           ? "border-red-300 bg-red-50 text-red-800"
                           : "border-amber-300 bg-amber-50 text-amber-800"}`}>
          {hasNewViolation && <p className="font-medium">교체 시 제약 위반</p>}
          <ul className="list-inside list-disc">
            {warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        </div>
      )}
      <p className="mt-3 text-sm text-slate-700">{numberMarkers(result.briefing.rationale, ev)}</p>
      <div className="mt-3">
        <h4 className="text-xs font-medium text-slate-500">주의할 점</h4>
        <ul className="mt-1 list-inside list-disc text-sm text-slate-700">
          {result.briefing.risks.map((r) => <li key={r}>{numberMarkers(r, ev)}</li>)}
        </ul>
      </div>
      <div className="mt-3">
        <h4 className="text-xs font-medium text-slate-500">다른 방법</h4>
        <ul className="mt-1 list-inside list-disc text-sm text-slate-700">
          {result.briefing.alternatives.map((a) => <li key={a}>{numberMarkers(a, ev)}</li>)}
        </ul>
      </div>
      <EvidenceList evidence={result.briefing.evidence ?? []} nameOf={nameOf}
                    hasInlineMarkers={hasMarkers([result.briefing.rationale, ...result.briefing.risks,
                                                  ...result.briefing.alternatives])} />
    </div>
  );
}
