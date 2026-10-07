import type { PlanEvent } from "../api/types";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { formatPrecomputedAt } from "./precomputed";

interface Props {
  plans: PlanEvent[];
  selected: string | null;
  onSelect: (label: string) => void;
  /** 플랜별 화면에서 적용한 변경(교체·투입률 조정) 수 -- 미리 계산 배지를 "원안 + 변경 n건"으로 바꾼다. */
  editCounts?: Record<string, number>;
}

const pct = (v: number | null) => (v === null ? "산정 불가" : `${(v * 100).toFixed(1)}%`);

export function PlanCards({ plans, selected, onSelect, editCounts = {} }: Props) {
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
            {p.precomputed_at != null ? (
              <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                      selected === p.label ? "bg-sky-800 text-sky-100" : "bg-sky-100 text-sky-800"}`}
                    title={`시연용으로 ${formatPrecomputedAt(p.precomputed_at, true)}에 미리 계산해 둔 결과(같은 데이터·같은 설정임을 서버가 확인)${
                      editCounts[p.label] ? `. 그 위에 화면에서 변경 ${editCounts[p.label]}건을 적용했다` : ""}`}>
                {editCounts[p.label]
                  ? `미리 계산 원안 + 변경 ${editCounts[p.label]}건`
                  : `미리 계산 · ${formatPrecomputedAt(p.precomputed_at)}`}
              </span>
            ) : p.cached && (
              <span className={`text-xs ${selected === p.label ? "text-slate-300" : "text-slate-400"}`}
                    title="같은 조건으로 앞서 계산해 둔 결과를 다시 보여 준다">
                {editCounts[p.label] ? `저장된 결과 + 변경 ${editCounts[p.label]}건` : "저장된 결과"}
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
