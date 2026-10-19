import { useState } from "react";
import type { AllocChange, AssignEntry, Person, Project } from "../api/types";
import { formatAlloc, formatMonthly, monthLabel } from "./allocFormat";
import { practicalCredits, skillChips } from "./skillLabel";

interface Props {
  entries: AssignEntry[];
  people: Person[];
  projects: Project[];
  /** 주면 행마다 "달별 조정"을 보여 준다(사람별 달별 투입률 조정). */
  onAdjust?: (change: AllocChange) => Promise<boolean> | boolean | void;
  busy?: boolean;
  /** 계산 기준의 최소 투입률(달마다 지켜야 한다). 편집기 안내용. */
  minAlloc?: number;
  /** 계획 시작 달("2026-10"). 주면 달 순번 대신 실제 달로 보인다(계획기간 1년 확장, 2026-10-19). */
  horizonStart?: string | null;
}

/** 사업 기간 "26.12~27.3월"(한 달이면 "26.12월"). */
const periodLabel = (p: Project, start: string) =>
  p.start_month === p.end_month ? monthLabel(p.start_month, start)
    : `${monthLabel(p.start_month, start).slice(0, -1)}~${monthLabel(p.end_month, start)}`;

const months = (p: Project | undefined) =>
  p ? Array.from({ length: p.end_month - p.start_month + 1 }, (_, k) => p.start_month + k) : [];

/** 한 행의 달별 입력. 처음 값은 지금 투입률(월별이면 그 값, 아니면 모든 달 같은 값). */
function MonthlyEditor({ entry, project, busy, minAlloc, name, onApply, onCancel, horizonStart }: {
  entry: AssignEntry; project: Project | undefined; busy?: boolean; minAlloc: number; name: string; horizonStart?: string | null;
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
            {monthLabel(m, horizonStart)}
            <input aria-label={`${monthLabel(m, horizonStart)} 투입률`} inputMode="decimal" value={vals[m]}
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
          {low.map((m) => monthLabel(m, horizonStart)).join(", ")}이 최소 투입률보다 낮다 -- 적용하면 위반 경고가 붙는다.
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

export function AssignmentTable({ entries, people, projects, onAdjust, busy, minAlloc = 0.2, horizonStart }: Props) {
  const pName = new Map(people.map((p) => [p.id, p]));
  const jById = new Map(projects.map((j) => [j.id, j]));
  const [editing, setEditing] = useState<string | null>(null);
  const [skillsOf, setSkillsOf] = useState<string | null>(null);
  const byProject = new Map<string, AssignEntry[]>();
  for (const e of entries) {
    const list = byProject.get(e.project_id) ?? [];
    list.push(e);
    byProject.set(e.project_id, list);
  }
  // 같은 달에 두 사업 이상인 사람 = 프로젝트 동시 수행(전일 투입에서는 부서장이 허용한 사람만, 2026-10-19)
  const concurrent = new Set<string>();
  const spans = new Map<string, [number, number][]>();
  for (const e of entries) {
    const j = jById.get(e.project_id);
    if (!j) continue;
    const mine = spans.get(e.person_id) ?? [];
    if (mine.some(([a, b]) => a <= j.end_month && j.start_month <= b)) concurrent.add(e.person_id);
    mine.push([j.start_month, j.end_month]);
    spans.set(e.person_id, mine);
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
            {horizonStart && jById.get(jid) && (
              <span className="ml-2 text-xs font-normal text-slate-500">
                {periodLabel(jById.get(jid)!, horizonStart)}
              </span>
            )}
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
                      {concurrent.has(e.person_id) && (
                        <span className="ml-2 rounded-full bg-violet-100 px-1.5 py-0.5 text-[11px] text-violet-800"
                              title={p?.concurrency ? `허용 사유: ${p.concurrency.reason}${p.concurrency.memo ? ` · ${p.concurrency.memo}` : ""}` : undefined}>
                          동시 수행{p?.concurrency ? ` · ${p.concurrency.reason}` : ""}
                        </span>
                      )}
                      {p && Object.keys(p.skills).length > 0 && (
                        <button type="button" onClick={() => setSkillsOf(skillsOf === key ? null : key)}
                                aria-expanded={skillsOf === key} aria-label={`${p.name} 기술 보기`}
                                className="ml-2 text-[11px] text-indigo-700 underline">
                          기술{practicalCredits(p).length > 0 ? " · 인정 포함" : ""}
                        </button>
                      )}
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-right tabular-nums text-slate-600">
                        {formatAlloc(e.alloc)}
                        {e.monthly_alloc && (
                          <span className="block text-[11px] text-slate-400">{formatMonthly(e.monthly_alloc, horizonStart)}</span>
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
                  {skillsOf === key && (
                    <div className="mt-1 flex flex-wrap gap-1" aria-label={`${p?.name ?? e.person_id} 기술`}>
                      {skillChips(p).map((c) => (
                        <span key={c.skill} title={c.credit ? "본인이 적은 경력이 아니라 하위 기술 경력에서 일부 인정한 값" : undefined}
                              className={`rounded px-1.5 py-0.5 text-[11px] ${c.credit
                                ? "border border-dashed border-amber-400 bg-amber-50 text-amber-900"
                                : "bg-slate-100 text-slate-700"}`}>
                          {c.skill} 레벨 {c.level}{c.credit ? ` (${c.credit})` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                  {onAdjust && editing === key && (
                    <MonthlyEditor key={JSON.stringify([e.alloc, e.monthly_alloc ?? null])}
                                   entry={e} project={jById.get(jid)} busy={busy} minAlloc={minAlloc}
                                   name={p?.name ?? e.person_id} horizonStart={horizonStart}
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
