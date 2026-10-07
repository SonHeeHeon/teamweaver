import type { PlanEvent } from "../api/types";
import { ConfidenceBadge } from "./ConfidenceBadge";

interface Props {
  plans: PlanEvent[];
  selected: string | null;
  onSelect: (label: string) => void;
}

const pct = (v: number | null) => (v === null ? "산정 불가" : `${(v * 100).toFixed(1)}%`);

export function PlanCards({ plans, selected, onSelect }: Props) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {plans.map((p) => (
        <button
          key={p.label}
          onClick={() => onSelect(p.label)}
          className={`rounded-lg border p-4 text-left transition ${
            selected === p.label
              ? "border-slate-900 bg-slate-900 text-white"
              : "border-slate-200 bg-white hover:border-slate-400"
          }`}
        >
          <div className="flex items-baseline justify-between">
            <span className="text-base font-semibold">Plan {p.label}</span>
            {p.cached && (
              <span className={`text-xs ${selected === p.label ? "text-slate-300" : "text-slate-400"}`}>
                캐시
              </span>
            )}
          </div>
          <dl className="mt-3 space-y-1 text-sm">
            <div className="flex justify-between">
              <dt className={selected === p.label ? "text-slate-300" : "text-slate-500"}>최적화율</dt>
              <dd className="tabular-nums font-medium">{pct(p.optimization_ratio)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className={selected === p.label ? "text-slate-300" : "text-slate-500"}>충족률</dt>
              <dd className="tabular-nums">{pct(p.fulfillment)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className={selected === p.label ? "text-slate-300" : "text-slate-500"}>배치</dt>
              <dd className="tabular-nums">{p.entries.length}건</dd>
            </div>
          </dl>
          {p.termination !== undefined && (
            <div className="mt-2">
              <ConfidenceBadge termination={p.termination} timeLimited={p.time_limited} objective={p.objective}
                               bestBound={p.best_bound} gapAllowed={p.gap_allowed}
                               within={p.label !== "A" ? "다양성 조건" : undefined} />
            </div>
          )}
          {p.time_limited && p.termination === undefined && (
            <p className="mt-2 text-xs text-amber-600" title="계산 시간 한도에 걸려 멈춘 답이다. 더 좋은 답이 있을 수 있다.">
              시간 한도 도달(최선 증명 전)
            </p>
          )}
          {p.unfilled.length > 0 && (
            <p className="mt-2 text-xs text-amber-500">
              미충원 {p.unfilled.length}건
            </p>
          )}
        </button>
      ))}
    </div>
  );
}
