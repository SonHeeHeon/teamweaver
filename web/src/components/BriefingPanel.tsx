import type { ObjectiveBreakdown, WhatifResponse } from "../api/types";
import { swapWarnings } from "../api/whatifWarnings";

interface Props {
  result: WhatifResponse | null;
  loading: boolean;
}

const TERMS: [keyof ObjectiveBreakdown, string][] = [
  ["skill", "기술 적합도"],
  ["synergy", "협업 시너지"],
  ["overfamiliarity", "반복 협업 감점"],
  ["unfilled", "미충원 감점"],
];

const signed = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(4)}`;

export function BriefingPanel({ result, loading }: Props) {
  if (loading) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
        브리핑 생성 중…
      </div>
    );
  }
  if (!result) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-400">
        인력을 교체하면 변화량과 XAI 브리핑이 여기에 표시된다.
      </div>
    );
  }
  const up = result.objective_delta >= 0;
  const warnings = swapWarnings(result);
  const hasNewViolation = result.new_violations.length > 0;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">XAI 브리핑</h3>
        {result.fallback_used && (
          <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
            규칙 기반(LLM 미사용)
          </span>
        )}
      </div>
      <p className={`mt-2 text-sm font-medium ${up ? "text-emerald-600" : "text-red-600"}`}>
        현행 점수 기준 변화 {signed(result.objective_delta)}
      </p>
      <p className="text-xs text-slate-400">참고값 · 재최적화 아님</p>
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
      <p className="mt-3 text-sm text-slate-700">{result.briefing.rationale}</p>
      <div className="mt-3">
        <h4 className="text-xs font-medium text-slate-500">리스크</h4>
        <ul className="mt-1 list-inside list-disc text-sm text-slate-700">
          {result.briefing.risks.map((r) => <li key={r}>{r}</li>)}
        </ul>
      </div>
      <div className="mt-3">
        <h4 className="text-xs font-medium text-slate-500">대안</h4>
        <ul className="mt-1 list-inside list-disc text-sm text-slate-700">
          {result.briefing.alternatives.map((a) => <li key={a}>{a}</li>)}
        </ul>
      </div>
    </div>
  );
}
