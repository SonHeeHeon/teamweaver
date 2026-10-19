/** 신규 사업 등록 양식(2026-10-19, claude-b) -- 사용자 요청 "추가하는건 항상 동일한 양식대로 … 추가하는게 쉽도록".
 *  다섯 단계: 기본 → 연결 사업(고도화 과제면 이전 사업 요구를 복사, 추가 과제면 진행 중인 상위 사업에서 미리 채움) → 필요 인원(등급별)
 *  → 필요 기술(사전에서 고르기) → 확인. 추가 과제(사용자 예: 운영 중 사업의 클라우드 마이그레이션, 내부 2명·6개월, 외주 여러 명)는
 *  상위 사업 기간 안이어야 하고 외주 인원은 참고로만 남는다.
 *  서버(POST /api/workspace/projects)가 같은 규칙으로 다시 검사한다 -- 여기서는 빈칸·순서만 미리 막는다. */
import { useEffect, useMemo, useRef, useState } from "react";
import { emptyForm, formProblems, normalizeGrades } from "./formRules";
import {
  fetchProjectForm, fetchSkillDictionary, GRADES, SECTORS, STAGE_LABEL,
  type ProjectForm as Form, type SkillDictEntry, type Stage,
} from "./workspaceApi";

const STEPS = ["기본", "연결 사업", "필요 인원", "필요 기술", "확인"] as const;
const MONTH_CHOICES = [12, 24, 36, 60];

interface Props {
  start: string | null | undefined;
  predecessors: { project_id: string; name: string; end: string | null }[];
  /** 추가 과제를 붙일 수 있는 진행 사업 */
  parents?: { project_id: string; name: string; start: string | null; end: string | null }[];
  /** 진행 사업 행의 "추가 과제 붙이기"로 열면 그 사업 -- 열자마자 미리 채운다 */
  initialParent?: string | null;
  onSubmit: (form: Form) => Promise<void>;
  onCancel: () => void;
}

export function ProjectForm({ start, predecessors, parents = [], initialParent = null, onSubmit, onCancel }: Props) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<Form>(() => emptyForm(start));
  const [dict, setDict] = useState<SkillDictEntry[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { fetchSkillDictionary().then(setDict).catch(() => setDict([])); }, []);
  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));
  const parentRow = parents.find((x) => x.project_id === form.parent);
  // 추가 과제 기간은 상위 사업 기간 안(서버도 같은 규칙으로 거절한다)
  const parentProblem = parentRow && parentRow.start && parentRow.end
    && (form.start_month < parentRow.start || form.end_month > parentRow.end)
    ? `추가 과제 기간은 상위 사업 기간(${parentRow.start}~${parentRow.end}) 안이어야 한다` : null;
  const problems = [...formProblems(form, step), ...(parentProblem && step >= 1 ? [parentProblem] : [])];
  const known = useMemo(() => new Set(dict.flatMap((d) => [d.name, ...d.aliases].map((n) => n.toLowerCase()))), [dict]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return dict.filter((d) => [d.name, ...d.aliases].some((n) => n.toLowerCase().includes(q))).slice(0, 8);
  }, [dict, query]);

  const copyGen = useRef(0);
  const [copying, setCopying] = useState(false);
  async function copyPredecessor(id: string | null) {
    const gen = ++copyGen.current;              // 늦게 온 복사 응답이 그사이 바꾼 선택을 덮지 않게(Codex 리뷰 2026-10-19)
    set({ predecessor: id, ...(id ? { parent: null } : {}) });
    setError(null);
    if (!id) { setCopying(false); return; }
    setCopying(true);
    try {
      const prev = await fetchProjectForm(id);
      if (gen !== copyGen.current) return;
      setError(null);
      setForm((f) => ({ ...f, predecessor: id, grades: normalizeGrades(prev.grades), skills: prev.skills,
                        sector: prev.sector, monthly_budget: prev.monthly_budget }));
    } catch (e) {
      if (gen === copyGen.current) setError(`이전 사업 요구를 읽지 못했다: ${(e as Error).message}`);
    } finally {
      if (gen === copyGen.current) setCopying(false);
    }
  }

  /** 추가 과제: 상위 진행 사업에서 이름·분야·기술·기간(6개월, 상위 기간 안)·내부 인원을 미리 채운다. 사업명을 이미 적었으면 둔다. */
  async function chooseParent(id: string | null) {
    const gen = ++copyGen.current;
    set({ parent: id, ...(id ? { predecessor: null } : { external_headcount: 0 }) });
    setError(null);
    if (!id) { setCopying(false); return; }
    setCopying(true);
    try {
      const pre = await fetchProjectForm(id, { start: form.start_month || start });
      if (gen !== copyGen.current) return;
      setForm((f) => ({ ...f, parent: id, predecessor: null, name: f.name.trim() ? f.name : pre.name, sector: pre.sector,
                        start_month: pre.start_month, end_month: pre.end_month, monthly_budget: pre.monthly_budget,
                        grades: normalizeGrades(pre.grades), skills: pre.skills }));
    } catch (e) {
      if (gen === copyGen.current) setError(`상위 사업을 읽지 못했다: ${(e as Error).message}`);
    } finally {
      if (gen === copyGen.current) setCopying(false);
    }
  }
  const opened = useRef(false);
  useEffect(() => {
    if (initialParent && !opened.current) { opened.current = true; void chooseParent(initialParent); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialParent]);

  function addSkill(name: string) {
    const n = name.trim();
    if (!n || form.skills.some((s) => s.name.toLowerCase() === n.toLowerCase())) return;
    set({ skills: [...form.skills, { name: n, min_months: 24, headcount: 1 }] });
    setQuery("");
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ ...form, name: form.name.trim(), grades: normalizeGrades(form.grades) });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="project-form-title"
         className="fixed inset-0 z-40 flex justify-end bg-black/30" onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }}>
      <div className="flex h-full w-full max-w-[560px] flex-col bg-paper shadow-xl">
        <header className="border-b border-line px-6 py-4">
          <h2 id="project-form-title" className="text-lg font-semibold text-slate-900">{form.parent ? "추가 과제 등록" : "신규 사업 등록"}</h2>
          <ol className="mt-3 flex flex-wrap gap-1 text-xs" aria-label="단계">
            {STEPS.map((s, i) => (
              <li key={s} aria-current={i === step ? "step" : undefined}
                  className={`rounded-full px-2.5 py-1 ${i === step ? "bg-ink text-white" : i < step ? "bg-slate-200 text-slate-700" : "bg-slate-100 text-slate-400"}`}>
                {i + 1}. {s}
              </li>
            ))}
          </ol>
        </header>
        {/* 미리 채우기(이전·상위 사업) 응답을 기다리는 동안 입력을 잠근다 -- 늦은 응답이 그사이 고친 값을 덮지 않게(Codex 리뷰 2026-10-19) */}
        <fieldset disabled={copying} aria-busy={copying} className="flex-1 space-y-4 overflow-y-auto px-6 py-5 text-sm">
          {step === 0 && (
            <>
              <label className="block">사업명
                <input className="field mt-1" autoFocus value={form.name} onChange={(e) => set({ name: e.target.value })}
                       placeholder="예: 보험사 B 데이터 플랫폼 고도화" />
              </label>
              <label className="block">고객 구분
                <select className="field mt-1" value={form.sector} onChange={(e) => set({ sector: e.target.value as Form["sector"] })}>
                  {SECTORS.map((s) => <option key={s}>{s}</option>)}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">시작 달
                  <input type="month" className="field mt-1" value={form.start_month} onChange={(e) => set({ start_month: e.target.value })} />
                </label>
                <label className="block">종료 달
                  <input type="month" className="field mt-1" value={form.end_month} onChange={(e) => set({ end_month: e.target.value })} />
                </label>
              </div>
              <p className="text-xs text-slate-500">종료 달은 실제 종료 달 그대로 적는다(해를 넘겨도 된다). 계산은 계획 기간 안으로 잘라 쓴다.</p>
              <label className="block">월 예산(가상비용점)
                <input type="number" min={1} className="field mt-1" value={form.monthly_budget}
                       onChange={(e) => set({ monthly_budget: Number(e.target.value) })} />
              </label>
              <fieldset>
                <legend>단계</legend>
                <div className="mt-1 flex gap-2">
                  {(Object.keys(STAGE_LABEL) as Stage[]).map((s) => (
                    <label key={s} className={`btn ${form.stage === s ? "border-ink" : ""}`}>
                      <input type="radio" name="stage" className="sr-only" checked={form.stage === s} onChange={() => set({ stage: s })} />
                      {STAGE_LABEL[s]}
                    </label>
                  ))}
                </div>
              </fieldset>
            </>
          )}
          {step === 1 && (
            <>
              <p>올해 사업이 이어지는 <b>고도화 과제</b>면 이전 사업을 고른다. 그 사업의 필요 인원·기술·예산을 복사하고, 이전 팀원을 후보 맨 위에 보인다.</p>
              <label className="block">이전 사업
                <select className="field mt-1" value={form.predecessor ?? ""} onChange={(e) => void copyPredecessor(e.target.value || null)}>
                  <option value="">없음(새 사업)</option>
                  {predecessors.map((p) => (
                    <option key={p.project_id} value={p.project_id}>{p.project_id} {p.name}{p.end ? ` (~${p.end})` : ""}</option>
                  ))}
                </select>
              </label>
              {parents.length > 0 && (
                <>
                  <p className="pt-2">진행 중인 사업에 붙는 <b>추가 과제</b>(예: 운영 중 사업의 클라우드 마이그레이션)면 상위 사업을 고른다.
                    기간은 상위 사업 안이어야 하고, 상위 사업 팀원이 후보 맨 위에 온다.</p>
                  <label className="block">상위 사업(추가 과제)
                    <select className="field mt-1" value={form.parent ?? ""} onChange={(e) => void chooseParent(e.target.value || null)}>
                      <option value="">없음</option>
                      {parents.map((p) => (
                        <option key={p.project_id} value={p.project_id}>{p.project_id} {p.name}{p.start && p.end ? ` (${p.start}~${p.end})` : ""}</option>
                      ))}
                    </select>
                  </label>
                  {form.parent && (
                    <label className="block">외주 인원(참고 — 계산에는 쓰지 않는다)
                      <input type="number" min={0} max={500} className="field mt-1" value={form.external_headcount ?? 0}
                             onChange={(e) => set({ external_headcount: Math.max(0, Math.floor(Number(e.target.value) || 0)) })} />
                    </label>
                  )}
                </>
              )}
            </>
          )}
          {step === 2 && (
            <div className="grid grid-cols-2 gap-3">
              {GRADES.map((g) => (
                <label key={g} className="block">{g}
                  <input type="number" min={0} max={50} className="field mt-1" value={form.grades[g] ?? 0}
                         onChange={(e) => set({ grades: { ...form.grades, [g]: Math.max(0, Number(e.target.value)) } })} />
                </label>
              ))}
              <p className="col-span-2 text-xs text-slate-500">0을 적은 등급은 이 사업에 넣지 않는다는 뜻이다.</p>
            </div>
          )}
          {step === 3 && (
            <>
              <label className="block">기술 검색
                <input className="field mt-1" value={query} placeholder="예: 파이썬, Kubernetes, 데이터 모델링"
                       onChange={(e) => setQuery(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSkill(matches[0]?.name ?? query); } }} />
              </label>
              {matches.length > 0 && (
                <ul className="flex flex-wrap gap-1.5" aria-label="사전에서 찾은 기술">
                  {matches.map((m) => (
                    <li key={m.name}><button type="button" className="btn px-2 py-1 text-xs" onClick={() => addSkill(m.name)}>+ {m.name}</button></li>
                  ))}
                </ul>
              )}
              {query.trim() && matches.length === 0 && (
                <button type="button" className="btn-ghost text-xs" onClick={() => addSkill(query)}>
                  “{query.trim()}” 그대로 넣기(사전에 없음 — 다른 이름과 합치지 않는다)
                </button>
              )}
              <ul className="space-y-2" aria-label="필요 기술">
                {form.skills.map((s, i) => (
                  <li key={s.name} className="rounded-md border border-line px-3 py-2">
                    <div className="flex items-center justify-between">
                      <b>{s.name}</b>
                      {!known.has(s.name.toLowerCase()) && <span className="text-xs text-amber-700">사전에 없음</span>}
                      <button type="button" className="btn-ghost px-1.5 py-0.5 text-xs" aria-label={`${s.name} 빼기`}
                              onClick={() => set({ skills: form.skills.filter((_, j) => j !== i) })}>빼기</button>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
                      최소 경력
                      {MONTH_CHOICES.map((mo) => (
                        <button key={mo} type="button" aria-pressed={s.min_months === mo}
                                className={`btn px-2 py-0.5 text-xs ${s.min_months === mo ? "border-ink font-semibold" : ""}`}
                                onClick={() => set({ skills: form.skills.map((x, j) => j === i ? { ...x, min_months: mo } : x) })}>
                          {mo / 12}년
                        </button>
                      ))}
                      <label className="ml-2">인원
                        <input type="number" min={1} max={50} className="field ml-1 inline-block w-16 py-0.5" value={s.headcount}
                               onChange={(e) => set({ skills: form.skills.map((x, j) => j === i ? { ...x, headcount: Math.max(1, Number(e.target.value)) } : x) })} />
                      </label>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
          {step === 4 && (
            <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5">
              <dt className="text-slate-500">사업명</dt><dd>{form.name}</dd>
              <dt className="text-slate-500">고객 구분</dt><dd>{form.sector}</dd>
              <dt className="text-slate-500">기간</dt><dd className="num">{form.start_month} ~ {form.end_month}</dd>
              <dt className="text-slate-500">월 예산</dt><dd className="num">{form.monthly_budget.toLocaleString()}</dd>
              <dt className="text-slate-500">단계</dt><dd>{STAGE_LABEL[form.stage]}</dd>
              <dt className="text-slate-500">이전 사업</dt><dd>{form.predecessor ?? "없음"}</dd>
              {form.parent && <><dt className="text-slate-500">상위 사업</dt><dd>{form.parent}(추가 과제) · 외주 {form.external_headcount ?? 0}명(참고)</dd></>}
              <dt className="text-slate-500">필요 인원</dt>
              <dd>{GRADES.filter((g) => form.grades[g] !== undefined).map((g) => `${g} ${form.grades[g]}`).join(" · ")}</dd>
              <dt className="text-slate-500">필요 기술</dt>
              <dd>{form.skills.length ? form.skills.map((s) => `${s.name}(${s.min_months / 12}년·${s.headcount}명)`).join(", ") : "없음"}</dd>
            </dl>
          )}
          {problems.length > 0 && (step !== 1 || parentProblem) && (
            <ul role="alert" className="rounded-md bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
              {problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</p>}
        </fieldset>
        <footer className="flex items-center justify-between border-t border-line px-6 py-3">
          <button className="btn-ghost" onClick={onCancel}>닫기</button>
          <div className="flex gap-2">
            {step > 0 && <button className="btn" disabled={copying} onClick={() => setStep(step - 1)}>이전</button>}
            {step < STEPS.length - 1
              ? <button className="btn-primary" disabled={copying || (problems.length > 0 && (step !== 1 || !!parentProblem))} onClick={() => setStep(step + 1)}>
                  {copying ? "복사하는 중…" : "다음"}</button>
              : <button className="btn-primary" disabled={busy || copying || formProblems(form, 4).length > 0 || !!parentProblem} onClick={() => void submit()}>
                  {busy ? "등록하는 중…" : "등록"}
                </button>}
          </div>
        </footer>
      </div>
    </div>
  );
}

