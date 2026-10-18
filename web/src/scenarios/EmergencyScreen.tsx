/** 상황 3 -- 긴급 투입(2026-10-19 claude-b). 진행 사업에 이슈·사고가 나서 사람을 더 넣는다 -- 넣은 사람은 지금부터 사업이 끝날
 *  때까지 남는다(사용자 결정: "계속 남는 경우로만"). 사업을 고르면 밤 배치가 뽑아 둔 "지금 바로 넣을 수 있는 사람"이 바로 뜨고,
 *  상황(필요 기술·인원·등급·빼 오기 허용)을 적으면 최선 조합과 후보 순위가 나온다. 확정하면 그 사업의 정원·예산·요구 기술이
 *  늘어난 새 판이 된다(서버가 계산한 apply_hint 그대로). */
import { useEffect, useMemo, useRef, useState } from "react";
import { ActionBar, Chip, Dialog, EmptyState, GRADE_TONE, Notice, Panel, Tabs } from "../ui";
import {
  fetchSkillDictionary, fetchStandby, fetchWorkspace, GRADES, isDatasetChanged, postApply, postEmergency,
  type CandidateRow, type EmergencyPlan, type Grade, type SkillDictEntry, type StandbyRow, type Workspace,
} from "./workspaceApi";

type Group = "bench" | "spare" | "pull";
const GROUP_LABEL: Record<Group, string> = { bench: "쉬는 인력", spare: "여유 있는 인력", pull: "다른 사업에서 빼 오기" };
const fmt = (x: number | undefined) => (x === undefined ? "–" : (x >= 0 ? "+" : "") + x.toFixed(2));

export function EmergencyScreen({ datasetVersion, onDatasetChanged, onReviewDoc }: {
  datasetVersion?: string | null; onDatasetChanged: () => void;
  /** 확정 뒤 "인사팀 검토 자료 만들기"(claude-a 2026-10-19 -- 사용자 결정: 긴급 투입도 자료) */
  onReviewDoc?: (version: string, projectId: string | null) => void;
}) {
  const [confirmed, setConfirmed] = useState<{ version: string; projectId: string | null } | null>(null);
  useEffect(() => {                                  // 판이 바뀌면 옛 확정의 버튼은 지운다(리뷰 P3)
    if (confirmed && datasetVersion && datasetVersion !== confirmed.version) setConfirmed(null);
  }, [datasetVersion, confirmed]);
  const [ws, setWs] = useState<Workspace | null>(null);
  const [projectId, setProjectId] = useState<string>("");
  const [note, setNote] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [dict, setDict] = useState<SkillDictEntry[]>([]);
  const [n, setN] = useState(1);
  const [grade, setGrade] = useState<Grade | "">("");
  const [pull, setPull] = useState(0);
  const [standby, setStandby] = useState<{ rows: StandbyRow[]; source: string | null } | null>(null);
  const [plan, setPlan] = useState<EmergencyPlan | null>(null);
  // 요청 세대: 사업·판이 바뀐 뒤 늦게 온 결과는 버린다(Codex 리뷰 2026-10-19: 다른 사업의 결과가 확정될 수 있었다)
  const reqGen = useRef(0);
  const [group, setGroup] = useState<Group>("bench");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => { fetchSkillDictionary().then(setDict).catch(() => setDict([])); }, []);
  // 처음과, 다른 화면에서 데이터가 바뀌었을 때 다시 읽는다
  const wsGen = useRef(0);
  function reloadWorkspace() {
    const gen = ++wsGen.current;                 // 연달아 다시 읽을 때 옛 응답이 새 응답을 덮지 않게
    fetchWorkspace().then((w) => {
      if (gen !== wsGen.current) return;
      setWs(w);
      setProjectId((pid) => (w.ongoing.some((o) => o.project_id === pid) ? pid : ""));
    }).catch((e) => { if (gen === wsGen.current) setError((e as Error).message); });
  }
  useEffect(() => {
    if (ws && datasetVersion && datasetVersion === ws.dataset_version) return;
    reloadWorkspace();
  }, [datasetVersion]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    reqGen.current += 1;
    setPlan(null);
    setStandby(null);
    setConfirming(false);
    setBusy(false);                              // 진행 중이던 검색은 버렸다 -- "찾는 중"에 멈추지 않게(대체 리뷰 MUST)
    if (!ws || !projectId) return;
    let live = true;
    fetchStandby(ws.dataset_version, projectId)
      .then((s) => { if (live) setStandby({ rows: s.projects[projectId] ?? [], source: s.source }); })
      .catch(() => { if (live) setStandby({ rows: [], source: null }); });
    return () => { live = false; };
  }, [ws, projectId]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return dict.filter((d) => [d.name, ...d.aliases].some((x) => x.toLowerCase().includes(q))).slice(0, 8);
  }, [dict, query]);
  const project = ws?.ongoing.find((o) => o.project_id === projectId) ?? null;

  async function search() {
    if (!ws || !projectId) return;
    const gen = ++reqGen.current;
    setBusy(true);
    setError(null);
    try {
      const res = await postEmergency({ dataset_version: ws.dataset_version, project_id: projectId, n,
                                        grade: grade || null, skills, pull_budget: pull, note });
      if (gen === reqGen.current && res.project_id === projectId) setPlan(res);
    } catch (e) {
      if (gen === reqGen.current) setError((e as Error).message);
    } finally {
      if (gen === reqGen.current) setBusy(false);
    }
  }

  async function apply() {
    if (!ws || !plan?.best.accepted || !plan.best.entries) return;
    if (plan.project_id !== projectId || plan.dataset_version !== ws.dataset_version) {
      setPlan(null);
      setConfirming(false);
      return;
    }
    setBusy(true);
    try {
      const hint = plan.apply_hint ?? { headcount_add: {}, budget_add: {}, skills_add: {} };
      // 결과를 계산한 판·사업 그대로 확정한다
      const res = await postApply(plan.dataset_version, { entries: plan.best.entries, source: "emergency",
        note: `${plan.project_id} 긴급 투입${note ? `: ${note}` : ""}`, ...hint });
      setWs(res.workspace);
      setConfirmed({ version: res.workspace.dataset_version, projectId: plan.project_id });
      setPlan(null);
      setNotice(`${project?.name ?? projectId}에 ${plan.best.added?.map((a) => a.name).join(", ")}을(를) 넣었다(사업 끝까지).`);
      onDatasetChanged();
    } catch (e) {
      setError((e as Error).message);
      if (isDatasetChanged(e)) { setPlan(null); reloadWorkspace(); onDatasetChanged(); }
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  if (!ws) return error ? <Notice tone="bad">{error}</Notice> : <p className="text-sm text-slate-500">작업 데이터를 불러오는 중…</p>;
  if (!ws.editable || ws.ongoing.length === 0) {
    return <Panel title="긴급 투입"><EmptyState title="진행 중인 사업이 없습니다"
                  body={ws.reason ?? "현재 배치가 있는 운영 중 데이터에서 씁니다 — 편성 기록에서 운영 중 데이터를 여세요."} /></Panel>;
  }
  const rows: CandidateRow[] = plan ? plan.candidates[group] : [];
  const pulled = plan?.best.diff?.moved ?? [];
  return (
    <div className="max-w-[1180px] space-y-4">
      {notice && <Notice>{notice}</Notice>}
      {confirmed && onReviewDoc && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-pine-200 bg-pine-50 px-3 py-2 text-[13px] text-pine-800">
          긴급 투입의 인사팀 검토 자료(새로 넣은 사람 · 빼 온 사업의 영향 · 상황 메모)를 만듭니다.
          <button type="button" className="btn-primary" onClick={() => onReviewDoc(confirmed.version, confirmed.projectId)}>
            인사팀 검토 자료 만들기
          </button>
        </div>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <Panel title="상황 적기" sub="넣은 사람은 지금부터 사업이 끝날 때까지 배치됩니다.">
          <div className="space-y-3 px-5 py-4 text-sm">
            <label className="block">사업
              <select className="field mt-1" aria-label="긴급 투입할 사업" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                <option value="">진행 사업 고르기</option>
                {ws.ongoing.map((o) => <option key={o.project_id} value={o.project_id}>{o.project_id} {o.name}</option>)}
              </select>
            </label>
            <label className="block">무슨 일인지(기록용, AI에 보내지 않음)
              <textarea className="field mt-1" rows={2} value={note} maxLength={300} onChange={(e) => setNote(e.target.value)}
                        placeholder="예: 운영 이관 중 장애 — 쿠버네티스 경험자 필요" />
            </label>
            <label className="block">필요한 기술(선택)
              <input className="field mt-1" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="기술 검색"
                     onKeyDown={(e) => { if (e.key === "Enter" && (matches[0] || query.trim())) { e.preventDefault();
                       const nm = matches[0]?.name ?? query.trim(); if (!skills.includes(nm)) setSkills([...skills, nm]); setQuery(""); } }} />
            </label>
            {matches.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {matches.map((m) => (
                  <button key={m.name} className="btn px-2 py-0.5 text-xs"
                          onClick={() => { if (!skills.includes(m.name)) setSkills([...skills, m.name]); setQuery(""); }}>+ {m.name}</button>
                ))}
              </div>
            )}
            {skills.length > 0 && (
              <div className="flex flex-wrap gap-1.5" aria-label="고른 기술">
                {skills.map((s) => (
                  <button key={s} className="btn px-2 py-0.5 text-xs" aria-label={`${s} 빼기`}
                          onClick={() => setSkills(skills.filter((x) => x !== s))}>{s} ×</button>
                ))}
              </div>
            )}
            <div className="grid grid-cols-3 gap-2">
              <label className="block">인원
                <input type="number" min={1} max={5} className="field mt-1" aria-label="넣을 인원 수" value={n}
                       onChange={(e) => { const v = Math.min(5, Math.max(1, Number(e.target.value))); setN(v); setPull(Math.min(pull, v)); }} />
              </label>
              <label className="block">등급
                <select className="field mt-1" value={grade} onChange={(e) => setGrade(e.target.value as Grade | "")}>
                  <option value="">상관없음</option>
                  {GRADES.map((g) => <option key={g}>{g}</option>)}
                </select>
              </label>
              <label className="block">빼 오기
                <input type="number" min={0} max={n} className="field mt-1" value={pull}
                       onChange={(e) => setPull(Math.min(n, Math.max(0, Number(e.target.value))))} />
              </label>
            </div>
            <p className="text-xs text-slate-500">빼 오기 = 다른 진행 사업에서 데려와도 되는 사람 수(그 사업 빈자리는 메움). 고정한 사람은 빼 오지 않습니다.</p>
            <button className="btn-primary w-full" disabled={!projectId || busy} onClick={() => void search()}>
              {busy ? "찾는 중…" : "후보 찾기"}
            </button>
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="지금 바로 넣을 수 있는 사람" sub={standby?.source === "nightly" ? "밤사이 미리 뽑아 둔 명단" : "지금 계산한 명단"}>
            {!projectId ? <EmptyState title="사업을 고르면 바로 보입니다" />
              : !standby ? <p className="px-5 py-4 text-sm text-slate-500">불러오는 중…</p>
              : standby.rows.length === 0 ? <EmptyState title="빼 오지 않고 넣을 수 있는 사람이 없습니다" body="빼 오기를 허용해 후보를 찾으세요." />
              : (
                <ul className="flex flex-wrap gap-2 px-5 py-4" aria-label="대기 명단">
                  {standby.rows.map((r) => (
                    <li key={r.person_id} className="rounded-md border border-line px-3 py-1.5 text-sm">
                      <Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip> {r.name}
                      <span className="num ml-1.5 text-xs text-slate-500">{Math.round(r.alloc * 100)}% · {fmt(r.delta_total)}</span>
                    </li>
                  ))}
                </ul>
              )}
          </Panel>

          {plan && (
            <Panel title="최선 조합" sub={plan.added_requirements.length
              ? `요구에 없던 기술(${plan.added_requirements.map((s) => s.name).join(", ")})을 더해 계산했습니다 — 확정하면 사업 요구에도 들어갑니다.` : plan.note}>
              <div className="space-y-2 px-5 py-4 text-sm">
                {!plan.best.accepted ? <p className="text-amber-700">조건에 맞는 조합을 찾지 못했습니다(빼 오기·등급 조건을 바꿔 보세요).</p> : (
                  <>
                    <p>넣을 사람: <b>{plan.best.added?.map((a) => a.name).join(", ") || "없음"}</b>
                      <span className="num ml-2 text-xs text-slate-500">점수 변화 {fmt(plan.best.delta?.total)} · 추가 월 비용 {plan.best.added_cost ?? 0}</span></p>
                    {pulled.length > 0 && <p className="text-xs text-slate-600">빼 오는 사람: {pulled.map((m) => `${m.person_id}(${m.from}→${m.to.join(",")})`).join(" · ")}</p>}
                    {(plan.best.new_violations?.length ?? 0) > 0 && <Notice tone="warn">이 조합은 새 위반이 있어 확정할 수 없습니다.</Notice>}
                    {plan.apply_hint && (Object.keys(plan.apply_hint.headcount_add).length > 0 || Object.keys(plan.apply_hint.budget_add).length > 0) && (
                      <p className="text-xs text-slate-500">확정하면 사업 정원
                        {Object.values(plan.apply_hint.headcount_add).flatMap((h) => Object.entries(h)).map(([g, c]) => ` ${g} +${c}`)}
                        {Object.values(plan.apply_hint.budget_add).map((b) => ` · 월 예산 +${b}`)}이(가) 늘어납니다(증원).</p>
                    )}
                  </>
                )}
                <Tabs label="후보 갈래" value={group} onChange={setGroup}
                      items={(Object.keys(GROUP_LABEL) as Group[]).map((g) => [g, `${GROUP_LABEL[g]} ${plan.candidates[g].length}`])} />
                {rows.length === 0 ? <p className="text-slate-500">이 갈래에는 후보가 없습니다.</p> : (
                  <table className="w-full">
                    <thead className="text-left text-xs text-slate-500"><tr><th>이름</th><th>등급</th><th className="text-right">투입률</th>
                      <th className="text-right">점수 변화</th><th>비고</th></tr></thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.person_id} className="border-t border-slate-100">
                          <td className="py-1.5">{r.name}</td><td><Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip></td>
                          <td className="num text-right">{Math.round(r.alloc * 100)}%</td>
                          <td className="num text-right">{fmt(r.delta_total)}</td>
                          <td className="text-xs text-slate-500">{r.pulled_from.length ? `${r.pulled_from.join(", ")}에서 빼 옴` : ""}
                            {r.new_violations.length > 0 && <span className="ml-1 text-amber-700">넣으면 위반</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </Panel>
          )}
        </div>
      </div>
      {plan?.best.accepted && (plan.best.new_violations?.length ?? 0) === 0 && (
        <ActionBar note="확정하면 지금 배치가 바뀐 새 판이 됩니다(변경 이력에서 되돌릴 수 있음).">
          <button className="btn-primary" disabled={busy} onClick={() => setConfirming(true)}>이 조합 확정</button>
        </ActionBar>
      )}
      {confirming && plan && (
        <Dialog title={`${project?.name ?? projectId} 긴급 투입 확정`} actions={<>
          <button className="btn-ghost" onClick={() => setConfirming(false)} disabled={busy}>취소</button>
          <button className="btn-primary" disabled={busy} onClick={() => void apply()}>{busy ? "처리 중…" : "확정"}</button>
        </>}>
          {plan.best.added?.map((a) => a.name).join(", ")}을(를) 지금부터 사업이 끝날 때까지 넣습니다.
        </Dialog>
      )}
    </div>
  );
}
