import { useEffect, useRef, useState } from "react";
import type { AllocChange, AssignEntry, Person, Project } from "../api/types";
import { formatAlloc, formatMonthly, monthLabel } from "./allocFormat";
import { LifecycleChips } from "./LifecycleChips";
import { projectPeriod } from "./lifecycle";
import { practicalCredits, skillChips } from "./skillLabel";
import { ProjectCode } from "../ui";

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
  /** 결과의 빈자리("J003:중급:1명 미충원") -- 카드에 빈자리 수, "빈자리 있는 사업 먼저" 정렬 */
  unfilled?: string[];
}


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

type SortKey = "code" | "start" | "end" | "size" | "vacancy";
const SORTS: { key: SortKey; label: string }[] = [
  { key: "code", label: "사업 코드순" }, { key: "start", label: "시작 빠른 순" }, { key: "end", label: "끝나 가는 순" },
  { key: "size", label: "인원 많은 순" }, { key: "vacancy", label: "빈자리 있는 사업 먼저" },
];
const GRADE_SHORT: Record<string, string> = { 특급: "특", 고급: "고", 중급: "중", 초급: "초" };

/** "J003:중급:1명 미충원" → 사업별 빈자리 수 */
function vacancies(unfilled: string[] | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const u of unfilled ?? []) {
    const [jid, , rest] = u.split(":");
    const n = Number.parseInt(rest ?? "", 10);
    out.set(jid, (out.get(jid) ?? 0) + (Number.isFinite(n) ? n : 1));
  }
  return out;
}

/** 배치 명단(2026-10-20 사용자 "프로젝트 당 인력 나열을 최소화해서 … 한 모니터 화면에 많이 담았으면"): 사업 카드 = 머리 한 줄 + 이름표 줄,
 *  카드를 여러 열로. 오른쪽에서 정렬·이름 찾기. 이름표를 누르면 기술 창. 전일 투입(100%)은 숫자를 보이지 않는다(100%가 아닌 옛 기록만). */
export function AssignmentTable({ entries, people, projects, onAdjust, busy, minAlloc = 0.2, horizonStart, unfilled }: Props) {
  const pName = new Map(people.map((p) => [p.id, p]));
  const jById = new Map(projects.map((j) => [j.id, j]));
  const [editing, setEditing] = useState<string | null>(null);
  const [skillsOf, setSkillsOf] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("code");
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
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
  const vac = vacancies(unfilled);
  const q = query.trim();
  const hit = (pid: string) => q !== "" && ((pName.get(pid)?.name ?? "").includes(q) || pid.toLowerCase().includes(q.toLowerCase()));
  const hits = q ? entries.filter((e) => hit(e.person_id)) : [];
  const ordered = [...byProject.entries()].sort(([a, la], [b, lb]) => {
    const ja = jById.get(a), jb = jById.get(b);
    const startOf = (j?: Project) => j?.period?.start ?? String(j?.start_month ?? 0).padStart(7, "0");
    const endOf = (j?: Project) => j?.period?.end ?? String(j?.end_month ?? 0).padStart(7, "0");
    const by = sort === "start" ? startOf(ja).localeCompare(startOf(jb))
      : sort === "end" ? endOf(ja).localeCompare(endOf(jb))
      : sort === "size" ? lb.length - la.length
      : sort === "vacancy" ? (vac.get(b) ?? 0) - (vac.get(a) ?? 0) : 0;
    return by || a.localeCompare(b);
  });
  // 바깥을 누르거나 Esc면 기술 창을 닫는다
  useEffect(() => {
    if (!skillsOf) return;
    const close = (ev: MouseEvent) => {
      if (!(ev.target instanceof Element) || !ev.target.closest("[data-skill-pop]")) setSkillsOf(null);
    };
    const esc = (ev: KeyboardEvent) => { if (ev.key === "Escape") setSkillsOf(null); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [skillsOf]);
  function find(text: string) {
    setQuery(text);
    const t = text.trim();
    const first = t && entries.find((e) => (pName.get(e.person_id)?.name ?? "").includes(t));
    if (first) {
      const el = rootRef.current?.querySelector(`[data-project="${first.project_id}"]`);
      if (el && "scrollIntoView" in el) (el as HTMLElement).scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    }
  }
  return (
    <div className="space-y-3" ref={rootRef}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[13px] text-slate-600">
          사업 {byProject.size}개 · 배치 {new Set(entries.map((e) => e.person_id)).size}명. 이름을 누르면 그 사람의 기술이 보입니다.
          {onAdjust && " 투입률을 나누는 예전 방식이면 이름 옆 '조정'으로 달마다 비율을 바꿀 수 있습니다."}
        </p>
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <label className="flex items-center gap-1">
            <span className="text-slate-500">사람 찾기</span>
            <input value={query} onChange={(e) => find(e.target.value)} placeholder="이름" aria-label="배치 명단에서 사람 찾기"
                   className="field w-32 py-1" />
          </label>
          {q && <span className="text-slate-500" aria-live="polite">{hits.length > 0
            ? `${[...new Set(hits.map((e) => e.project_id))].join(", ")}에 있음` : "배치 명단에 없음"}</span>}
          <label className="flex items-center gap-1">
            <span className="text-slate-500">정렬</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="사업 정렬 기준" className="field py-1">
              {SORTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
          </label>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {ordered.map(([jid, list]) => {
          const j = jById.get(jid);
          const seats = j ? Object.values(j.grade_headcount ?? {}).reduce((a, b) => a + b, 0) : 0;
          const v = vac.get(jid) ?? 0;
          return (
            <div key={jid} data-project={jid} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[13px]">
                <ProjectCode id={jid} name={j?.name} className="font-semibold text-slate-900" />
                <span className="min-w-0 flex-1 truncate text-slate-700" title={j?.name}>{j?.name ?? ""}</span>
                <span className="text-slate-500" title="배치된 인원 / 등급별 정원 합">{list.length}{seats ? `/${seats}` : ""}명</span>
                {v > 0 && <span className="rounded bg-amber-100 px-1.5 text-[11px] text-amber-800">빈자리 {v}</span>}
              </div>
              <div className="flex flex-wrap items-center gap-x-2 text-[11.5px] text-slate-500">
                {horizonStart && j && <span>{projectPeriod(j, horizonStart)}</span>}
                {j && <LifecycleChips lc={j.lifecycle} name={j.name} />}
              </div>
              <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label={`${jid} 배치 명단`}>
                {list.map((e) => {
                  const p = pName.get(e.person_id);
                  const key = `${e.person_id}-${e.project_id}`;
                  const name = p?.name ?? e.person_id;
                  const full = Math.abs(e.alloc - 1) < 1e-9 && !e.monthly_alloc;
                  return (
                    <li key={key} className="relative" data-skill-pop={skillsOf === key ? "" : undefined}>
                      <button type="button" onClick={() => setSkillsOf(skillsOf === key ? null : key)}
                              aria-expanded={skillsOf === key} aria-label={`${name} 기술 보기`}
                              title={concurrent.has(e.person_id)
                                ? `프로젝트 동시 수행${p?.concurrency ? ` · 허용 사유: ${p.concurrency.reason}${p.concurrency.memo ? ` · ${p.concurrency.memo}` : ""}` : ""}`
                                : undefined}
                              className={`rounded-full border px-2 py-0.5 text-[12.5px] ${hit(e.person_id)
                                ? "border-amber-500 bg-amber-100 ring-2 ring-amber-300"
                                : concurrent.has(e.person_id) ? "border-violet-300 bg-violet-50" : "border-slate-200 bg-slate-50 hover:bg-slate-100"}`}>
                        <span className="text-slate-900">{name}</span>
                        <span className="ml-1 text-[11px] text-slate-500">{GRADE_SHORT[p?.grade ?? ""] ?? p?.grade}</span>
                        {concurrent.has(e.person_id) && <span className="ml-1 text-[11px] text-violet-700">동시 수행</span>}
                        {!full && <span className="ml-1 tabular-nums text-[11px] text-slate-600">{formatAlloc(e.alloc)}</span>}
                      </button>
                      {e.monthly_alloc && (
                        <span className="ml-1 text-[11px] text-slate-400">{formatMonthly(e.monthly_alloc, horizonStart)}</span>
                      )}
                      {onAdjust && (
                        <button type="button" onClick={() => setEditing(editing === key ? null : key)}
                                aria-expanded={editing === key} aria-label={`${name} 달별 조정`}
                                className="ml-1 text-[11px] text-sky-700 underline">조정</button>
                      )}
                      {skillsOf === key && (
                        <div role="dialog" aria-label={`${name} 기술`}
                             className="absolute left-0 top-full z-20 mt-1 w-72 rounded-md border border-slate-200 bg-white p-2 shadow-lg">
                          <p className="mb-1 text-[12px] text-slate-600">
                            {name} · {p?.grade}{concurrent.has(e.person_id) && p?.concurrency ? ` · 동시 수행 허용 사유: ${p.concurrency.reason}` : ""}
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {skillChips(p).map((c) => (
                              <span key={c.skill} title={c.credit ? "본인이 적은 경력이 아니라 하위 기술 경력에서 일부 인정한 값" : undefined}
                                    className={`rounded px-1.5 py-0.5 text-[11px] ${c.credit
                                      ? "border border-dashed border-amber-400 bg-amber-50 text-amber-900"
                                      : "bg-slate-100 text-slate-700"}`}>
                                {c.skill} 레벨 {c.level}{c.credit ? ` (${c.credit})` : ""}
                              </span>
                            ))}
                            {skillChips(p).length === 0 && <span className="text-[11px] text-slate-500">등록된 기술 없음</span>}
                          </div>
                          {practicalCredits(p).length > 0 && (
                            <p className="mt-1 text-[11px] text-amber-800">점선 = 하위 기술 경력에서 일부 인정한 레벨</p>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
              {onAdjust && list.map((e) => {
                const key = `${e.person_id}-${e.project_id}`;
                return editing === key ? (
                  <MonthlyEditor key={JSON.stringify([e.alloc, e.monthly_alloc ?? null])}
                                 entry={e} project={jById.get(jid)} busy={busy} minAlloc={minAlloc}
                                 name={pName.get(e.person_id)?.name ?? e.person_id} horizonStart={horizonStart}
                                 onCancel={() => setEditing(null)}
                                 onApply={async (m) => {
                                   // 성공했을 때만 닫는다 -- 실패·취소면 입력을 남겨 다시 시도할 수 있게(리뷰 S4).
                                   const ok = await onAdjust({ kind: "alloc", person_id: e.person_id,
                                                               project_id: e.project_id, monthly_alloc: m });
                                   if (ok !== false) setEditing(null);
                                 }} />
                ) : null;
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
