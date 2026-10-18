/** 상황 2 -- 신규 사업 편성(첫 화면, 2026-10-19 claude-b). 설계 `.omc/plan/2026-10-18-three-scenarios.md`.
 *
 *  왼쪽: 신규 사업(파이프라인) 목록과 "신규 사업 등록". 오른쪽: 고른 사업의 자리·팀·단계 → 후보 세 갈래(쉬는 인력·끝나 가는 인력·
 *  진행 사업에서 빼 오기)로 손 편성 → 확정. 아래: 자동 편성(몇 명까지 옮길지 K 비교) → 확정, 진행 사업 고정(사업·사람),
 *  변경 이력과 되돌리기. 확정·등록·고정은 모두 작업 데이터의 새 판이 된다(서버가 업로드와 같은 검증을 한다). */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DatasetChangedError, postJustification, streamOperatingCompare } from "../api/client";
import type { AssignEntry, OperatingRow, PlacementSettings } from "../api/types";
import { ActionBar, Chip, Dialog, EmptyState, GRADE_TONE, Notice, Panel, Tabs } from "../ui";
import { ProjectForm } from "./ProjectForm";
import {
  currentEntries, dropProject, isDatasetChanged, fetchCandidates, fetchWorkspace, patchStage, postApply, postLock, postProject, postUndo,
  STAGE_LABEL, type CandidateRow, type Candidates, type OngoingItem, type PipelineItem, type Stage, type Workspace,
} from "./workspaceApi";

type Group = "bench" | "ending" | "pull";
const GROUP_LABEL: Record<Group, string> = { bench: "쉬는 인력", ending: "끝나 가는 인력", pull: "진행 사업에서 빼 오기" };
type SignedRow = OperatingRow & { plan_label?: string; plan_token?: string };

export function Seats({ seats }: { seats: PipelineItem["seats"] }) {
  const grades = Object.entries(seats);
  if (!grades.length) return <span className="text-xs text-slate-400">정원 없음</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {grades.map(([g, s]) => (
        <span key={g} className={`num rounded px-1.5 text-xs ${s.filled < s.need ? "bg-amber-50 text-amber-800" : "bg-slate-100 text-slate-600"}`}>
          {g} {s.filled}/{s.need}
        </span>
      ))}
    </span>
  );
}

/** 서버는 UTC로 남긴다 -- 화면은 브라우저 지역 시각으로 */
const localTime = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
};
const fmt = (x: number | undefined | null) => (x === undefined || x === null ? "–" : (x >= 0 ? "+" : "") + x.toFixed(2));

interface Props {
  milpParams: PlacementSettings | null;
  /** App이 아는 지금 데이터 버전 -- 다른 화면에서 바뀌면 다시 읽는다 */
  datasetVersion?: string | null;
  onDatasetChanged: () => void;
  /** 확정 뒤 "인사팀 검토 자료 만들기"(claude-a 2026-10-19): 확정한 판과 사업(자동 편성은 여러 사업이라 null = 목록)으로 검토 자료 화면을 연다 */
  onReviewDoc?: (version: string, projectId: string | null) => void;
}

export function NewProjectsScreen({ milpParams, datasetVersion, onDatasetChanged, onReviewDoc }: Props) {
  const [ws, setWs] = useState<Workspace | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [cands, setCands] = useState<Candidates | null>(null);
  const [group, setGroup] = useState<Group>("bench");
  const [draft, setDraft] = useState<Record<string, CandidateRow>>({});
  const [formOpen, setFormOpen] = useState(false);
  const [confirm, setConfirm] = useState<null | { title: string; body: string; run: () => Promise<void> }>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<{ version: string; projectId: string | null } | null>(null);
  // 다른 화면에서 판이 바뀌면 옛 확정의 버튼은 지운다(이미 만든 자료는 "검토 자료" 목록에서 연다, 리뷰 P3)
  useEffect(() => {
    if (confirmed && datasetVersion && datasetVersion !== confirmed.version) setConfirmed(null);
  }, [datasetVersion, confirmed]);
  const [rows, setRows] = useState<SignedRow[]>([]);
  // 자동 편성 결과가 어느 판·설정으로 계산됐는지 -- 확정·소명 요청은 이 값을 쓴다(Codex 리뷰 2026-10-19: 판이 바뀐 뒤 옛 명단이
  // 새 판 번호로 확정될 수 있었다, 설정을 바꾸면 소명 서명이 어긋났다)
  const autoOf = useRef<{ version: string; params: PlacementSettings | null } | null>(null);
  const [autoState, setAutoState] = useState<"idle" | "running" | "done">("idle");
  const [justify, setJustify] = useState<{ title: string; text: string } | null>(null);
  const [openTeam, setOpenTeam] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    try {
      const w = await fetchWorkspace();
      setWs(w);
      setError(null);
      setSelected((s) => (s && w.pipeline.some((p) => p.project_id === s) ? s : w.pipeline[0]?.project_id ?? null));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => { void load(); return () => abort.current?.abort(); }, [load]);
  // 다른 화면(긴급 투입·데이터·편성 기록)에서 데이터가 바뀌었으면 다시 읽는다
  useEffect(() => { if (datasetVersion && ws && datasetVersion !== ws.dataset_version) void load(); }, [datasetVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  const project = ws?.pipeline.find((p) => p.project_id === selected) ?? null;
  // 판이 바뀌면 그 전 판으로 계산한 자동 편성·열어 둔 확인 창은 버린다
  useEffect(() => {
    if (autoOf.current && ws && autoOf.current.version !== ws.dataset_version) {
      abort.current?.abort();
      autoOf.current = null;
      setRows([]);
      setAutoState("idle");
    }
    setConfirm(null);
  }, [ws?.dataset_version]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setDraft({});
    setCands(null);
    if (!ws || !selected) return;
    let live = true;
    fetchCandidates(selected, ws.dataset_version)
      .then((c) => { if (live) setCands(c); })
      .catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
  }, [ws, selected]);

  /** 판을 바꾸는 요청 하나 -- 성공하면 새 작업 데이터로 바꾸고 App에도 알린다. */
  async function mutate(run: () => Promise<{ workspace: Workspace }>, done: string, review?: { projectId: string | null }) {
    setBusy(true);
    setError(null);
    setConfirmed(null);
    try {
      const res = await run();
      setWs(res.workspace);
      if (review) setConfirmed({ version: res.workspace.dataset_version, projectId: review.projectId });
      setRows([]);
      setAutoState("idle");
      setNotice(done);
      onDatasetChanged();
    } catch (e) {
      setError((e as Error).message);
      if (isDatasetChanged(e)) { void load(); onDatasetChanged(); }   // 다른 곳에서 판이 바뀌었다 -- 새 판을 읽는다
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  const draftList = Object.values(draft);
  function applyDraft() {
    if (!ws || !project || !draftList.length) return;
    const pulled = new Set(draftList.flatMap((r) => r.pulled_from.map((src) => `${r.person_id}@${src}`)));
    const entries: AssignEntry[] = currentEntries(ws).filter((e) => !pulled.has(`${e.person_id}@${e.project_id}`));
    for (const r of draftList) entries.push({ person_id: r.person_id, project_id: project.project_id, alloc: r.alloc });
    const names = draftList.map((r) => r.name).join(", ");
    setConfirm({
      title: `${project.name}에 ${draftList.length}명 확정`,
      body: `${names}을(를) 넣는다.${pulled.size ? ` 빼 오는 배치 ${pulled.size}건은 원래 사업에서 빠진다.` : ""} 지금 배치가 바뀐 새 판이 된다(되돌릴 수 있다).`,
      run: () => mutate(() => postApply(ws.dataset_version, { entries, source: "manual",
                                                               note: `${project.project_id} 손 편성: ${names}` }),
                        `${project.name}에 ${draftList.length}명을 넣었다.`, { projectId: project.project_id }),
    });
  }

  async function runAuto() {
    if (!ws) return;
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setRows([]);
    setAutoState("running");
    setError(null);
    autoOf.current = { version: ws.dataset_version, params: milpParams };
    try {
      for await (const ev of streamOperatingCompare({ dataset_version: ws.dataset_version, milp_params: milpParams,
                                                       ks: [0, 1, 2, 3] }, ctl.signal)) {
        if (ctl.signal.aborted) break;                // 판이 바뀌어 버린 계산의 늦은 행
        if (ev.event === "row") setRows((r) => [...r, ev.data as SignedRow]);
        if (ev.event === "error") setError(ev.data.message);
      }
      if (abort.current === ctl && !ctl.signal.aborted) setAutoState("done");
    } catch (e) {
      if (abort.current !== ctl) return;           // 이미 새 계산이 시작됐다 -- 옛 계산이 상태를 덮지 않게
      if (e instanceof DatasetChangedError) { void load(); onDatasetChanged(); }
      else if ((e as Error).name !== "AbortError") setError((e as Error).message);
      setAutoState("idle");
    }
  }

  function applyRow(row: SignedRow) {
    const of = autoOf.current;
    if (!ws || !row.entries || !of || of.version !== ws.dataset_version) return;
    const moved = row.diff?.moved?.length ?? 0;
    const joined = row.diff?.joined?.length ?? 0;
    setConfirm({
      title: `자동 편성 K=${row.k} 확정`,
      body: `대기 인력 ${row.diff?.joined?.filter((j) => j.from_bench).length ?? 0}명 투입, 진행 사업에서 ${moved}명 이동` +
            `(신규 배치 ${joined}건). 이 안의 변경 전체가 지금 배치가 된다.`,
      run: () => mutate(() => postApply(of.version, { entries: row.entries!, source: "auto",
                                                               note: `자동 편성 확정(K=${row.k}, 최대 ${row.k}명 이동)` }),
                        `자동 편성 K=${row.k}을(를) 확정했다.`, { projectId: null }),
    });
  }

  async function showJustification(row: SignedRow, p: PipelineItem) {
    const of = autoOf.current;
    if (!ws || !of || !row.entries || !row.plan_token || !row.plan_label) return;
    setBusy(true);
    try {
      const j = await postJustification({ dataset_version: of.version, project_id: p.project_id, entries: row.entries,
        base_entries: null, applied_swaps: [], weights: {}, milp_params: of.params, ai: true,
        plan_label: row.plan_label, plan_token: row.plan_token });
      setJustify({ title: `${p.name} — 인사팀 검토 자료(K=${row.k})`, text: j.text ?? "" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const predecessors = useMemo(() => (ws ? [...ws.ongoing, ...ws.pipeline].map((p) => ({ project_id: p.project_id, name: p.name, end: p.end })) : []),
                               [ws]);

  if (!ws) return error ? <Notice tone="bad">{error}</Notice> : <p className="text-sm text-slate-500">작업 데이터를 불러오는 중…</p>;
  if (!ws.editable) {
    return (
      <Panel title="신규 사업 편성">
        <EmptyState title="지금 데이터에서는 신규 사업을 편성할 수 없습니다" body={ws.reason}
                    actions={<span className="text-[13px] text-slate-500">편성 기록에서 운영 중 데이터를 열거나, 인력·사업 CSV 묶음을 올리세요.</span>} />
      </Panel>
    );
  }

  const groupRows = cands ? cands[group] : [];
  return (
    <div className="max-w-[1180px] space-y-4">
      {notice && <Notice>{notice}</Notice>}
      {confirmed && onReviewDoc && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-pine-200 bg-pine-50 px-3 py-2 text-[13px] text-pine-800">
          확정한 편성의 인사팀 검토 자료(사업별 소명 · 옮겨 온 인력의 원래 사업 영향)를 만듭니다.
          <button type="button" className="btn-primary" onClick={() => onReviewDoc(confirmed.version, confirmed.projectId)}>
            인사팀 검토 자료 만들기
          </button>
        </div>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Panel title="신규 사업" sub="발주 예정 · 제안 중 · 수주"
               actions={<button className="btn-primary" onClick={() => setFormOpen(true)}>신규 사업 등록</button>}>
          {ws.pipeline.length === 0
            ? <EmptyState title="등록된 신규 사업이 없습니다" body="발주 예정 소식이 들어오면 정해진 양식으로 등록하세요." />
            : (
              <ul className="divide-y divide-slate-100" aria-label="신규 사업 목록">
                {ws.pipeline.map((p) => (
                  <li key={p.project_id}>
                    <button onClick={() => setSelected(p.project_id)} aria-current={p.project_id === selected ? "true" : undefined}
                            className={`w-full px-5 py-3 text-left ${p.project_id === selected ? "bg-[#F1F7F4]" : "hover:bg-slate-50"}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-slate-900">{p.name}</span>
                        <Chip tone="stage">{STAGE_LABEL[p.stage]}</Chip>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <span className="num">{p.project_id} · {p.start}~{p.end}</span>
                        {p.predecessor && <span>이전 사업 {p.predecessor}</span>}
                      </div>
                      <div className="mt-1.5"><Seats seats={p.seats} /></div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          <div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
            쉬는 인력 {ws.bench.length}명 · 계획 기간 안에 끝나는 진행 사업 {ws.ongoing.filter((o) => o.ends_in_horizon).length}개
          </div>
        </Panel>

        {project ? (
          <Panel title={`${project.name}`} sub={`${project.project_id} · ${project.start} ~ ${project.end} · 월 예산 ${project.monthly_budget.toLocaleString()}`}
                 actions={<>
                   {(Object.keys(STAGE_LABEL) as Stage[]).map((s) => (
                     <button key={s} className={`btn px-2.5 py-1 text-xs ${project.stage === s ? "border-ink font-semibold" : ""}`}
                             aria-pressed={project.stage === s} disabled={busy || project.stage === s}
                             onClick={() => void mutate(() => patchStage(ws.dataset_version, project.project_id, s),
                                                        `${project.name} 단계를 ${STAGE_LABEL[s]}(으)로 바꿨다.`)}>
                       {STAGE_LABEL[s]}
                     </button>
                   ))}
                   <button className="btn px-2.5 py-1 text-xs text-red-700" disabled={busy}
                           onClick={() => setConfirm({ title: `${project.name} 떨어짐·취소`,
                             body: "사업과 그 배치를 지운다. 다른 배치가 없는 팀원은 쉬는 인력이 된다(되돌릴 수 있다).",
                             run: () => mutate(() => dropProject(ws.dataset_version, project.project_id), `${project.name}을(를) 지웠다.`) })}>
                     떨어짐
                   </button>
                 </>}>
            <div className="space-y-3 px-5 py-4">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className="text-slate-500">자리</span><Seats seats={project.seats} />
                {project.predecessor && <span className="text-xs text-slate-500">고도화 과제 — 이전 사업 {project.predecessor} 팀원이 후보 맨 위에 옵니다</span>}
              </div>
              <div className="text-sm">
                <span className="text-slate-500">지금 팀</span>{" "}
                {project.team.length ? project.team.map((m) => (
                  <span key={m.person_id} className="mr-1.5 inline-flex items-center gap-1">
                    {m.grade && <Chip tone={GRADE_TONE[m.grade]}>{m.grade}</Chip>}{m.name}
                  </span>
                )) : <span className="text-slate-400">아직 없음</span>}
              </div>
              <Tabs label="후보 갈래" value={group} onChange={setGroup}
                    items={(Object.keys(GROUP_LABEL) as Group[]).map((g) => [g, `${GROUP_LABEL[g]} ${cands ? cands[g].length : ""}`])} />
              {!cands ? <p className="text-sm text-slate-500">후보를 계산하는 중…(1~2초)</p> : groupRows.length === 0 ? (
                <p className="text-sm text-slate-500">이 갈래에는 후보가 없습니다.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-slate-500">
                    <tr><th className="py-1">이름</th><th>등급</th><th className="text-right">점수 변화</th><th>비고</th><th /></tr>
                  </thead>
                  <tbody>
                    {groupRows.map((r) => {
                      const picked = !!draft[r.person_id];
                      return (
                        <tr key={r.person_id} className="border-t border-slate-100">
                          <td className="py-1.5">{r.name}{r.predecessor_team && <span className="ml-1.5"><Chip tone="stage">이전 팀원</Chip></span>}</td>
                          <td><Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip></td>
                          <td className="num text-right">{fmt(r.delta_total)}</td>
                          <td className="text-xs text-slate-500">
                            {r.pulled_from.length ? `${r.pulled_from.join(", ")}에서 빼 옴` : r.source === "partly_free" ? "남는 시간으로" : ""}
                            {r.new_violations.length > 0 && <span className="ml-1 text-amber-700">넣으면 위반</span>}
                          </td>
                          <td className="text-right">
                            <button className={`btn px-2 py-0.5 text-xs ${picked ? "border-ink font-semibold" : ""}`} aria-pressed={picked}
                                    aria-label={`${r.name} ${picked ? "빼기" : "넣기"}`}
                                    onClick={() => setDraft((d) => { const n = { ...d }; if (picked) delete n[r.person_id]; else n[r.person_id] = r; return n; })}>
                              {picked ? "넣음" : "넣기"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {cands && <p className="text-xs text-slate-400">{cands.note}</p>}
            </div>
          </Panel>
        ) : (
          <Panel title="신규 사업을 고르세요"><EmptyState title="왼쪽에서 사업을 고르면 후보가 세 갈래로 나옵니다" /></Panel>
        )}
      </div>

      {draftList.length > 0 && project && (
        <ActionBar note="넣기로 고른 사람을 이 사업에 확정합니다. 빼 오는 사람은 원래 사업에서 빠집니다.">
          <button className="btn-primary" disabled={busy} onClick={applyDraft}>{draftList.length}명 확정</button>
          <button className="btn-ghost" onClick={() => setDraft({})}>고른 것 지우기</button>
        </ActionBar>
      )}

      <Panel title="자동 편성 — 몇 명까지 옮길까" sub="신규 사업을 모두 채우면서 진행 사업에서 옮기는 사람 수(K)를 0~3명으로 비교합니다. 고정한 사람은 옮기지 않습니다."
             actions={<button className="btn" disabled={autoState === "running" || busy} onClick={() => void runAuto()}>
               {autoState === "running" ? "계산 중…(100명 약 45초)" : "비교 계산"}
             </button>}>
        {rows.length === 0 ? (
          <EmptyState title={autoState === "running" ? "계산하고 있습니다" : "아직 계산하지 않았습니다"}
                      body="밤 배치가 미리 계산해 두었으면 바로 나옵니다." />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr><th className="px-5 py-1.5">K</th><th className="text-right">품질</th><th className="text-right">빈자리</th>
                <th>옮김</th><th>인사팀 검토 자료</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.k} className="border-t border-slate-100">
                  <td className="px-5 py-2 font-semibold">K={r.k}</td>
                  <td className="num text-right">{r.accepted ? r.quality?.toFixed(2) : "–"}</td>
                  <td className="num text-right">{r.accepted ? r.unfilled_seats : "–"}</td>
                  <td className="text-xs text-slate-600">
                    {!r.accepted ? (r.error ?? "풀지 못함")
                      : `대기 투입 ${r.diff?.joined?.filter((j) => j.from_bench).length ?? 0}명 · 이동 ${r.diff?.moved?.length ?? 0}명`}
                    {r.carried_from_k !== undefined && ` (K=${r.carried_from_k} 해 유지)`}
                  </td>
                  <td className="text-xs">
                    {r.accepted && r.plan_token && ws.pipeline.map((p) => (
                      <button key={p.project_id} className="btn-ghost px-1.5 py-0.5 text-xs" disabled={busy}
                              aria-label={`K=${r.k} ${p.name} 인사팀 검토 자료`}
                              onClick={() => void showJustification(r, p)}>{p.project_id}</button>
                    ))}
                  </td>
                  <td className="pr-5 text-right">
                    {r.accepted && r.entries && <button className="btn px-2.5 py-1 text-xs" disabled={busy} onClick={() => applyRow(r)}>이 안 확정</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="진행 사업 — 고정" sub="자물쇠를 채운 사업·사람은 어떤 계산도 빼 가지 않습니다(사람을 더 넣는 것은 됩니다).">
        <ul className="divide-y divide-slate-100">
          {ws.ongoing.map((o: OngoingItem) => (
            <li key={o.project_id} className="px-5 py-2.5 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <button className="text-left" onClick={() => setOpenTeam(openTeam === o.project_id ? null : o.project_id)}
                        aria-expanded={openTeam === o.project_id}>
                  <span className="font-medium">{o.name}</span>{" "}
                  <span className="num text-xs text-slate-500">{o.project_id} · ~{o.end} · {o.team.length}명</span>
                  {o.ends_in_horizon && <span className="ml-1.5"><Chip tone="warn">곧 종료 {o.end}</Chip></span>}
                  {o.locked_count > 0 && !o.locked_all && <span className="ml-1.5 text-xs text-slate-500">고정 {o.locked_count}명</span>}
                </button>
                <button className={`btn px-2.5 py-1 text-xs ${o.locked_all ? "border-ink font-semibold" : ""}`} disabled={busy}
                        aria-pressed={o.locked_all} aria-label={`${o.name} 사업 ${o.locked_all ? "고정 해제" : "고정"}`}
                        onClick={() => void mutate(() => postLock(ws.dataset_version, o.project_id, null, !o.locked_all),
                                                   `${o.name} ${o.locked_all ? "고정을 풀었다" : "사업 전체를 고정했다"}.`)}>
                  {o.locked_all ? "🔒 사업 고정됨" : "🔓 사업 고정"}
                </button>
              </div>
              {openTeam === o.project_id && (
                <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={`${o.name} 팀원`}>
                  {o.team.map((m) => (
                    <li key={m.person_id}>
                      <button className={`btn px-2 py-0.5 text-xs ${m.locked ? "border-ink font-semibold" : ""}`} disabled={busy}
                              aria-pressed={!!m.locked} aria-label={`${m.name} ${m.locked ? "고정 해제" : "고정"}`}
                              onClick={() => void mutate(() => postLock(ws.dataset_version, o.project_id, [m.person_id], !m.locked),
                                                         `${m.name} ${m.locked ? "고정을 풀었다" : "을(를) 고정했다"}.`)}>
                        {m.locked ? "🔒" : "🔓"} {m.name} <span className="text-slate-400">{m.grade}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="변경 이력" sub="등록·고정·확정은 작업 데이터의 새 판으로 남습니다(최근 20판)."
             actions={<button className="btn" disabled={busy || ws.history.length < 2}
                              onClick={() => setConfirm({ title: "직전 판으로 되돌리기", body: `“${ws.history[0]?.note}”을(를) 되돌린다.`,
                                run: () => mutate(() => postUndo(ws.dataset_version), "직전 판으로 되돌렸다.") })}>되돌리기</button>}>
        {ws.history.length === 0 ? <EmptyState title="아직 바꾼 것이 없습니다" /> : (
          <ol className="divide-y divide-slate-100 text-sm">
            {ws.history.slice(0, 6).map((h, i) => (
              <li key={`${h.version}-${i}`} className="flex justify-between gap-3 px-5 py-2">
                <span>{h.note}</span><span className="num shrink-0 text-xs text-slate-400">{localTime(h.at)}</span>
              </li>
            ))}
          </ol>
        )}
      </Panel>

      {formOpen && (
        <ProjectForm start={ws.horizon_start} predecessors={predecessors} onCancel={() => setFormOpen(false)}
                     onSubmit={async (form) => {
                       let res;
                       try {
                         res = await postProject(ws.dataset_version, form);
                       } catch (e) {
                         if (isDatasetChanged(e)) { void load(); onDatasetChanged(); }
                         throw e;
                       }
                       setFormOpen(false);
                       setWs(res.workspace);
                       setSelected(res.project_id);
                       setNotice(`${form.name}(${res.project_id})을(를) 등록했다.`);
                       onDatasetChanged();
                     }} />
      )}
      {confirm && (
        <Dialog title={confirm.title} actions={<>
          <button className="btn-ghost" onClick={() => setConfirm(null)} disabled={busy}>취소</button>
          <button className="btn-primary" disabled={busy} onClick={() => void confirm.run()}>{busy ? "처리 중…" : "확정"}</button>
        </>}>{confirm.body}</Dialog>
      )}
      {justify && (
        <Dialog title={justify.title} actions={<button className="btn-primary" onClick={() => setJustify(null)}>닫기</button>}>
          <p className="max-h-[50vh] overflow-y-auto whitespace-pre-wrap">{justify.text}</p>
        </Dialog>
      )}
    </div>
  );
}
