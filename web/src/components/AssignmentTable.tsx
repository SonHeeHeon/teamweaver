import { useState } from "react";
import type { AllocChange, AssignEntry, Person, Project } from "../api/types";
import { formatAlloc, formatMonthly } from "./allocFormat";

interface Props {
  entries: AssignEntry[];
  people: Person[];
  projects: Project[];
  /** 주면 행마다 "달별 조정"을 보여 준다(사람별 달별 투입률 조정). */
  onAdjust?: (change: AllocChange) => Promise<boolean> | boolean | void;
  busy?: boolean;
  /** 계산 기준의 최소 투입률(달마다 지켜야 한다). 편집기 안내용. */
  minAlloc?: number;
}

const months = (p: Project | undefined) =>
  p ? Array.from({ length: p.end_month - p.start_month + 1 }, (_, k) => p.start_month + k) : [];

/** 한 행의 달별 입력. 처음 값은 지금 투입률(월별이면 그 값, 아니면 모든 달 같은 값). */
function MonthlyEditor({ entry, project, busy, minAlloc, name, onApply, onCancel }: {
  entry: AssignEntry; project: Project | undefined; busy?: boolean; minAlloc: number; name: string;
  onApply: (m: Record<string, number>) => void; onCancel: () => void;
}) {
  const ms = months(project);
  const initial = Object.fromEntries(ms.map((m) => [
    m, String(Math.round((entry.monthly_alloc?.[String(m)] ?? entry.alloc) * 1000) / 10)])) as Record<number, string>;
  const [vals, setVals] = useState<Record<number, string>>(initial);
  const parsed = ms.map((m) => Number(vals[m]));
  const bad = ms.filter((m, k) => !/^(\d+(\.\d*)?|\.\d+)$/.test(vals[m].trim()) || parsed[k] > 100);
  const low = ms.filter((m, k) => !bad.includes(m) && parsed[k] < minAlloc * 100 - 1e-9);
  return (
    <div className="mt-2 rounded-md border border-sky-200 bg-sky-50 p-2" role="group"
         aria-label={`${name} 달별 투입률`}>
      <div className="flex flex-wrap gap-2">
        {ms.map((m) => (
          <label key={m} className="flex items-center gap-1 text-xs text-slate-700">
            {m + 1}월
            <input aria-label={`${m + 1}월 투입률`} inputMode="decimal" value={vals[m]}
                   onChange={(e) => setVals({ ...vals, [m]: e.target.value })}
                   className={`w-14 rounded border px-1 py-0.5 text-right tabular-nums ${
                     bad.includes(m) ? "border-red-400" : "border-slate-300"}`} />%
          </label>
        ))}
      </div>
      <p className="mt-1 text-xs text-slate-500">
        달마다 최소 {Math.round(minAlloc * 1000) / 10}% 이상(계산 기준의 최소 투입률). 그 달에 빠지는 것(0%)은 지원하지 않는다.
      </p>
      {bad.length > 0 && <p className="mt-1 text-xs text-red-600">0~100 사이 숫자로 입력한다.</p>}
      {low.length > 0 && (
        <p className="mt-1 text-xs text-amber-700">
          {low.map((m) => `${m + 1}월`).join(", ")}이 최소 투입률보다 낮다 -- 적용하면 위반 경고가 붙는다.
        </p>
      )}
      <div className="mt-2 flex gap-2 text-xs">
        <button type="button" onClick={() => setVals(Object.fromEntries(ms.map((m) => [m, vals[ms[0]]])))}
                className="rounded border border-slate-300 bg-white px-2 py-1">모든 달 같게</button>
        <button type="button" disabled={busy || bad.length > 0}
                onClick={() => onApply(Object.fromEntries(ms.map((m, k) => [String(m), parsed[k] / 100])))}
                className="rounded bg-slate-900 px-2 py-1 font-medium text-white disabled:opacity-50">적용</button>
        <button type="button" onClick={onCancel} className="rounded border border-slate-300 bg-white px-2 py-1">
          취소
        </button>
      </div>
    </div>
  );
}

export function AssignmentTable({ entries, people, projects, onAdjust, busy, minAlloc = 0.2 }: Props) {
  const pName = new Map(people.map((p) => [p.id, p]));
  const jById = new Map(projects.map((j) => [j.id, j]));
  const [editing, setEditing] = useState<string | null>(null);
  const byProject = new Map<string, AssignEntry[]>();
  for (const e of entries) {
    const list = byProject.get(e.project_id) ?? [];
    list.push(e);
    byProject.set(e.project_id, list);
  }
  return (
    <div className="space-y-4">
      {onAdjust && (
        <p className="rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900">
          기본은 프로젝트 기간 내내 같은 투입률이다. 여러 프로젝트에 나눠 들어가는 사람은 행의
          <b> 달별 조정</b>으로 달마다 비율을 바꿀 수 있다(가용률·예산을 달별로 다시 확인하고 이력에 남는다).
        </p>
      )}
      {[...byProject.entries()].map(([jid, list]) => (
        <div key={jid} className="rounded-lg border border-slate-200 bg-white">
          <div className="border-b border-slate-100 px-3 py-2 text-sm font-medium text-slate-900">
            {jById.get(jid)?.name ?? jid}
            <span className="ml-2 text-xs font-normal text-slate-500">{list.length}명</span>
          </div>
          <ul className="divide-y divide-slate-100">
            {list.map((e) => {
              const p = pName.get(e.person_id);
              const key = `${e.person_id}-${e.project_id}`;
              return (
                <li key={key} className="px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-900">
                      {p?.name ?? e.person_id}
                      <span className="ml-2 text-xs text-slate-500">{p?.grade}</span>
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-right tabular-nums text-slate-600">
                        {formatAlloc(e.alloc)}
                        {e.monthly_alloc && (
                          <span className="block text-[11px] text-slate-400">{formatMonthly(e.monthly_alloc)}</span>
                        )}
                      </span>
                      {onAdjust && (
                        <button type="button" onClick={() => setEditing(editing === key ? null : key)}
                                aria-expanded={editing === key} aria-label={`${p?.name ?? e.person_id} 달별 조정`}
                                className="rounded border border-sky-300 bg-white px-2 py-0.5 text-xs text-sky-800">
                          달별 조정
                        </button>
                      )}
                    </span>
                  </div>
                  {onAdjust && editing === key && (
                    <MonthlyEditor key={JSON.stringify([e.alloc, e.monthly_alloc ?? null])}
                                   entry={e} project={jById.get(jid)} busy={busy} minAlloc={minAlloc}
                                   name={p?.name ?? e.person_id}
                                   onCancel={() => setEditing(null)}
                                   onApply={async (m) => {
                                     // 성공했을 때만 닫는다 -- 실패·취소면 입력을 남겨 다시 시도할 수 있게(리뷰 S4).
                                     const ok = await onAdjust({ kind: "alloc", person_id: e.person_id,
                                                                 project_id: e.project_id, monthly_alloc: m });
                                     if (ok !== false) setEditing(null);
                                   }} />
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}
