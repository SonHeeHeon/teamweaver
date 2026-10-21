/** 낯선 기술 이름 정리(2026-10-22 사용자 "4,5번은 개발해보자" -- 5번, claude-a, core/ingest/skill_mapping).
 *  사전에 없는 기술 이름(오타·한글 음차·꼬리말)은 지금 다른 기술로 셈해진다. AI가 이름마다 "같은 기술(합치기) / 하위 기술 / 새 기술 / 기술 아님"과
 *  이유를 제안하고, 관리자가 고른 것만 새 판으로 켠다(되돌리기로 무름). 헷갈리기 쉬운 짝(Java↔JavaScript 등)은 따로 표시하고, 자동으로 합치지 않는다. */
import { useEffect, useState } from "react";
import { Spinner } from "../ui";
import {
  fetchUnknownSkills, postSkillNames, postSkillSuggest, withPersistNote, type DecidedSkill, type SkillDecision, type SkillSuggestion,
  type UnknownSkill,
} from "../scenarios/workspaceApi";

const DECISION_LABEL: Record<SkillDecision, string> = { alias: "같은 기술로 합치기", narrower: "하위 기술로", new: "새 기술로 두기", not_skill: "기술 아님" };
const WHY: Record<string, string> = { no_client: "AI 키가 없어", external_blocked: "실데이터라 회사 밖 AI로 보내지 않아" };

interface Choice { decision: SkillDecision; concept: string | null; method: "ai" | "string" | "manual"; reason: string }

export function SkillNamesPanel({ datasetVersion, onChanged }: { datasetVersion: string | null; onChanged: () => void }) {
  const [items, setItems] = useState<UnknownSkill[] | null>(null);
  const [decided, setDecided] = useState<DecidedSkill[]>([]);
  const [sugg, setSugg] = useState<Record<string, SkillSuggestion>>({});
  const [choice, setChoice] = useState<Record<string, Choice>>({});
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"load" | "suggest" | "save" | null>(null);

  async function load() {
    if (!datasetVersion) return;
    setBusy("load");
    setError(null);
    try {
      const r = await fetchUnknownSkills(datasetVersion);
      setItems(r.items);
      setDecided(r.decided);
      setSugg({});
      setChoice({});
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  useEffect(() => { void load(); }, [datasetVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  async function suggest() {
    if (!datasetVersion || !items?.length) return;
    setBusy("suggest");
    setError(null);
    try {
      const r = await postSkillSuggest(datasetVersion);
      setSugg(Object.fromEntries(r.items.map((i) => [i.name, i])));
      setChoice(Object.fromEntries(r.items.map((i) => [i.name, { decision: i.decision, concept: i.concept, method: i.method, reason: i.reason }])));
      setNote(r.method === "ai"
        ? `AI 제안 ${r.items.length}개${r.usage ? ` · ${r.usage.latency_s.toFixed(0)}초 · $${r.usage.cost_usd.toFixed(4)}` : ""} — 고른 것만 반영됩니다.`
        : `${WHY[r.fallback_reason ?? ""] ?? "AI를 쓰지 못해"} 문자열이 비슷한 이름만 제안했습니다.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function save(names: string[]) {
    if (!datasetVersion || !names.length) return;
    setBusy("save");
    setError(null);
    try {
      const res = await postSkillNames(datasetVersion, names.map((n) => ({ name: n, ...choice[n] })));
      setNote(withPersistNote(`기술 이름 ${names.length}개를 정리한 새 판입니다 — 기술 점수를 다시 계산했습니다(변경 이력에서 되돌릴 수 있음).`, res));
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const high = Object.values(sugg).filter((s) => s.confidence === "high" && choice[s.name]?.decision === s.decision
                                               && choice[s.name]?.concept === s.concept).map((s) => s.name);
  const picked = Object.keys(choice).filter((n) => sugg[n]);
  return (
    <section className="panel" aria-labelledby="skill-names">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 id="skill-names" className="text-base font-semibold text-slate-900">기술 이름 정리</h2>
          <p className="mt-0.5 text-[13px] text-slate-500">
            기술 이름 사전에 없는 표기(오타·한글 음차·꼬리말)는 다른 기술로 셈해집니다. AI가 이름마다 사전의 어느 기술인지 제안하고, 고른 것만 반영합니다.
            AI에는 이름과 사람 수·함께 가진 기술만 보냅니다.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => void suggest()} disabled={!items?.length || busy !== null}>
            {busy === "suggest" ? <Spinner status={false} label="AI가 보는 중…" /> : "AI 제안 받기"}
          </button>
          {high.length > 0 && (
            <button className="btn-primary" onClick={() => void save(high)} disabled={busy !== null}
                    title="AI 제안과 문자열 1위가 같고 헷갈리는 짝이 없는 것만">확실한 {high.length}개 한 번에 반영</button>
          )}
        </div>
      </header>
      <div className="space-y-3 px-5 py-4 text-sm">
        {busy === "load" && <Spinner label="사전에 없는 이름을 찾는 중…" />}
        {items && items.length === 0 && <p className="text-slate-600">사전에 없는 기술 이름이 없습니다.</p>}
        {note && <p role="status" className="text-xs text-slate-600">{note}</p>}
        {error && <p role="alert" className="text-red-700">{error}</p>}
        {items && items.length > 0 && (
          <table className="w-full text-[13px]">
            <thead className="text-left text-xs text-slate-500">
              <tr><th className="py-1">이름</th><th>쓰는 곳</th><th>제안</th><th>반영할 결정</th><th><span className="sr-only">반영</span></th></tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const s = sugg[it.name];
                const c = choice[it.name];
                const options = [...(s?.concept ? [{ id: s.concept, label: s.concept_label ?? s.concept }] : []), ...(s?.alternatives ?? []),
                                 ...it.candidates.map((x) => ({ id: x.id, label: x.label }))]
                  .filter((o, k, arr) => arr.findIndex((p) => p.id === o.id) === k).slice(0, 4);
                return (
                  <tr key={it.name} className="border-t border-slate-100 align-top">
                    <td className="py-1.5 font-medium text-slate-900">{it.name}</td>
                    <td className="text-xs text-slate-600">사람 {it.people}{it.projects.length ? ` · 사업 ${it.projects.length}` : ""}
                      {it.co_skills.length > 0 && <div className="text-[11px] text-slate-500">함께: {it.co_skills.slice(0, 3).join(", ")}</div>}</td>
                    <td className="text-xs">
                      {s ? (
                        <>
                          <span className={s.confidence === "high" ? "text-pine-800" : "text-amber-800"}>
                            {DECISION_LABEL[s.decision]}{s.concept_label ? ` → ${s.concept_label}` : ""}
                          </span>
                          <span className="ml-1 text-[11px] text-slate-500">{s.method === "ai" ? (s.confidence === "high" ? "AI · 확실" : "AI · 확인 필요") : "문자열"}</span>
                          {s.reason && <div className="text-[11px] text-slate-500">{s.reason}</div>}
                          {s.flags.map((f) => <div key={f} className="text-[11px] text-amber-800">{f}</div>)}
                        </>
                      ) : <span className="text-slate-500">비슷한 이름: {it.candidates.slice(0, 2).map((x) => x.label).join(", ") || "없음"}</span>}
                    </td>
                    <td className="text-xs">
                      {s && c && (
                        <div className="flex flex-wrap items-center gap-1">
                          <select className="field py-0.5 text-xs" aria-label={`${it.name} 결정`} value={c.decision}
                                  onChange={(e) => setChoice((o) => ({ ...o, [it.name]: { ...c, decision: e.target.value as SkillDecision, method: "manual",
                                    concept: ["alias", "narrower"].includes(e.target.value) ? (c.concept ?? options[0]?.id ?? null) : null } }))}>
                            {(Object.keys(DECISION_LABEL) as SkillDecision[]).map((d) => <option key={d} value={d}>{DECISION_LABEL[d]}</option>)}
                          </select>
                          {(c.decision === "alias" || c.decision === "narrower") && (
                            <select className="field py-0.5 text-xs" aria-label={`${it.name} 사전 기술`} value={c.concept ?? ""}
                                    onChange={(e) => setChoice((o) => ({ ...o, [it.name]: { ...c, concept: e.target.value, method: "manual" } }))}>
                              {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                            </select>
                          )}
                        </div>
                      )}
                    </td>
                    <td>{s && c && <button className="btn px-2 py-0.5 text-xs" disabled={busy !== null || ((c.decision === "alias" || c.decision === "narrower") && !c.concept)}
                                           onClick={() => void save([it.name])}>반영</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {picked.length > 1 && (
          <button className="btn" onClick={() => void save(picked)} disabled={busy !== null}>보이는 결정 {picked.length}개 모두 반영</button>
        )}
        {decided.length > 0 && (
          <details className="text-xs text-slate-600">
            <summary className="cursor-pointer">이미 정리한 이름 {decided.length}개</summary>
            <ul className="mt-1 space-y-0.5">{decided.map((d) => <li key={d.name}>{d.name} — {DECISION_LABEL[d.decision]}{d.concept ? ` (${d.concept})` : ""}{d.reason ? ` · ${d.reason}` : ""}</li>)}</ul>
          </details>
        )}
      </div>
    </section>
  );
}
