import type { WhatifResponse } from "../api/types";

interface Props {
  result: WhatifResponse | null;
  loading: boolean;
}

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
        목적함수 변화 {up ? "+" : ""}{result.objective_delta.toFixed(4)}
      </p>
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
