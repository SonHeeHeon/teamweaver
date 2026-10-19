/** 상황 2 -- 신규 사업 편성(첫 화면, 2026-10-19 claude-b). 설계 `.omc/plan/2026-10-18-three-scenarios.md`.
 *
 *  왼쪽: 신규 사업(파이프라인) 목록과 "신규 사업 등록". 오른쪽: 고른 사업의 자리·팀·단계 → 후보 세 갈래(쉬는 인력·끝나 가는 인력·
 *  진행 사업에서 빼 오기)로 손 편성 → 확정. 아래: 자동 편성(몇 명까지 옮길지 K 비교) → 확정, 진행 사업 고정(사업·사람),
 *  변경 이력과 되돌리기. 확정·등록·고정은 모두 작업 데이터의 새 판이 된다(서버가 업로드와 같은 검증을 한다). */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DatasetChangedError, postJustification, streamOperatingCompare } from "../api/client";
import type { AssignEntry, OperatingRow, Person, PlacementSettings, Project } from "../api/types";
import { PersonName, ProjectCodes } from "../components/PersonName";
import { ActionBar, Chip, Dialog, EmptyState, GRADE_TONE, Notice, Panel, Tabs } from "../ui";
import { UnavailableList } from "./UnavailableList";
import { ConcurrencyPanel } from "./ConcurrencyPanel";
import { LifecycleChips } from "../components/LifecycleChips";
import { seatsText, signedPts } from "../components/scoreSplit";
import { ProjectForm } from "./ProjectForm";
import { CandidateGuide, PickDraft } from "./PickDraft";
import { partnersText, seatLines, skillCollab, violationReasons } from "./pickView";
import {
  currentEntries, dropProject, isDatasetChanged, fetchCandidates, fetchWorkspace, patchStage, postApply, postConcurrency, postLock,
  postProject, postUndo,
  STAGE_LABEL, withPersistNote, type CandidateRow, type Candidates, type OngoingItem, type PipelineItem, type Stage, type Workspace,
} from "./workspaceApi";

type Group = "bench" | "ending" | "pull";
const GROUP_LABEL: Record<Group, string> = { bench: "쉬는 인력", ending: "끝나 가는 인력", pull: "진행 사업에서 빼 오기" };
type SignedRow = OperatingRow & { plan_label?: string; plan_token?: string };

/** 등급별 자리(채움/정원). after가 있으면 "넣기로 고른 사람"을 넣었을 때의 값을 화살표로 함께(확정 전 미리 계산, 2026-10-20). */
export function Seats({ seats, after }: { seats: PipelineItem["seats"]; after?: PipelineItem["seats"] | null }) {
  const grades = [...new Set([...Object.keys(seats), ...Object.keys(after ?? {})])];
  if (!grades.length) return <span className="text-xs text-slate-400">정원 없음</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {grades.map((g) => {
        const s = seats[g] ?? { need: after?.[g]?.need ?? 0, filled: 0 };
        const a = after?.[g];
        const shown = a ?? s;
        const changed = a && a.filled !== s.filled;
        return (
          // 정원표에 없는 등급(need 0)은 넘쳐도 빨갛게 칠하지 않는다 -- 그 판정은 서버 위반 문장이 한다(리뷰 N3)
          <span key={g} className={`num rounded px-1.5 text-xs ${shown.filled < shown.need ? "bg-amber-50 text-amber-800"
                                     : shown.need > 0 && shown.filled > shown.need ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-600"}`}
                title={changed ? `지금 ${s.filled}/${s.need} → 고른 사람 포함 ${a.filled}/${a.need}` : undefined}>
            {g} {changed ? <>{s.filled}/{s.need} → <b>{a.filled}/{a.need}</b></> : `${shown.filled}/${shown.need}`}
          </span>
        );
      })}
    </span>
  );
}

/** 서버는 UTC로 남긴다 -- 화면은 브라우저 지역 시각으로 */
const localTime = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
};

interface Props {
  milpParams: PlacementSettings | null;
  /** App이 아는 지금 데이터 버전 -- 다른 화면에서 바뀌면 다시 읽는다 */
  datasetVersion?: string | null;
  onDatasetChanged: () => void;
  /** 확정 뒤 "인사팀 검토 자료 만들기"(claude-a 2026-10-19): 확정한 판과 사업(자동 편성은 여러 사업이라 null = 목록)으로 검토 자료 화면을 연다 */
  onReviewDoc?: (version: string, projectId: string | null) => void;
  /** 이름을 누르면 기술 창, 사업 코드에 마우스를 올리면 이름(2026-10-20 화면 친절하게) -- 없으면 이름·코드만 */
  people?: Person[];
  projects?: Project[];
}

export function NewProjectsScreen({ milpParams, datasetVersion, onDatasetChanged, onReviewDoc, people, projects }: Props) {
  const pById = useMemo(() => new Map((people ?? []).map((p) => [p.id, p])), [people]);
  const [ws, setWs] = useState<Workspace | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [cands, setCands] = useState<Candidates | null>(null);
  const [group, setGroup] = useState<Group>("bench");
  const [draft, setDraft] = useState<Record<string, CandidateRow>>({});
  const [formOpen, setFormOpen] = useState(false);
  /** 진행 사업 행의 "추가 과제 붙이기"로 연 양식의 상위 사업(2026-10-19) */
  const [formParent, setFormParent] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | { title: string; body: string; run: () => Promise<void> }>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<{ version: string; projectId: string | null; seen: string | null } | null>(null);
  // 다른 화면에서 판이 바뀌면 옛 확정의 버튼은 지운다(이미 만든 자료는 "검토 자료" 목록에서 연다, 리뷰 P3).
  // 확정 직후에는 App이 아직 옛 판(seen)을 들고 있다 -- 그 값은 "바뀜"이 아니다(Codex 리뷰: 버튼이 바로 사라졌다)
  useEffect(() => {
    if (!confirmed || !datasetVersion) return;
    // 옛 판(seen) 예외는 새 판과 한 번 맞춰질 때까지만 -- 그 뒤 되돌림 등으로 옛 판에 돌아가면 버튼을 지운다(Codex 리뷰 2라운드)
    if (datasetVersion === confirmed.version) { if (confirmed.seen !== null) setConfirmed({ ...confirmed, seen: null }); }
    else if (datasetVersion !== confirmed.seen) setConfirmed(null);
  }, [datasetVersion, confirmed]);
  const [rows, setRows] = useState<SignedRow[]>([]);
  // 자동 편성 결과가 어느 판·설정으로 계산됐는지 -- 확정·소명 요청은 이 값을 쓴다(Codex 리뷰 2026-10-19: 판이 바뀐 뒤 옛 명단이
  // 새 판 번호로 확정될 수 있었다, 설정을 바꾸면 소명 서명이 어긋났다)
  const autoOf = useRef<{ version: string; params: PlacementSettings | null } | null>(null);
  const [autoState, setAutoState] = useState<"idle" | "running" | "done">("idle");
  const [justify, setJustify] = useState<{ title: string; text: string } | null>(null);
  const [openTeam, setOpenTeam] = useState<string | null>(null);
  /** 고른 사람을 바꾼 뒤 후보를 다시 계산하는 중(이전 표는 흐리게 남긴다) */
  const [recalc, setRecalc] = useState(false);
  /** 지금 cands가 어느 선택(판·사업·고른 사람)으로 계산됐는지 -- 지금 선택과 다르면 미리 계산을 보이지 않고 확정을 막는다(Codex 리뷰 2026-10-20:
   *  다시 계산이 실패하면 이전 선택의 자리·점수가 지금 선택의 것처럼 남았다) */
  const [candsFor, setCandsFor] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  /** 후보·미리 계산 요청의 오류와 그 요청의 선택 키(다른 오류 문구와 따로 -- 지금 선택의 오류만 보인다) */
  const [candError, setCandError] = useState<{ key: string; msg: string } | null>(null);
  /** 자동 편성에서 옮길 수 있는 최대 인원(사용자 2026-10-20 "k는 최대 5까지") -- 0~kMax를 비교한다 */
  const [kMax, setKMax] = useState(5);
  // 미리 계산된 K가 0~3뿐이면(예전 편성 기록) 기본을 3으로 -- 바로 나오게(Opus 대체 리뷰 S2). 사람이 고르면 그대로 둔다
  const kTouched = useRef(false);
  useEffect(() => {
    const ks = ws?.operating_ks ?? [];
    if (!kTouched.current && ks.length && ks[0] === 0 && ks.every((k, i) => k === i) && ks[ks.length - 1] >= 3)
      setKMax(Math.min(5, ks[ks.length - 1]));
  }, [ws?.operating_ks]); // eslint-disable-line react-hooks/exhaustive-deps
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
  useEffect(() => { setDraft({}); setCands(null); setCandsFor(null); setCandError(null); }, [ws, selected]);
  // "넣기"로 고른 사람을 팀원으로 쳐서 후보를 다시 채점하고 고른 사람 전체를 미리 계산한다(2026-10-20 사용자 요청 2·5번).
  // 고를 때마다 서버를 부르되(1~2초) 짧게 몰아 누르면 마지막 것만, 이전 표는 흐리게 남긴다.
  const picks = useMemo(() => Object.values(draft).map((r) => ({ person_id: r.person_id, alloc: r.alloc, pulled_from: r.pulled_from })),
                        [draft]);
  const pickKey = JSON.stringify(picks);
  const forKey = `${ws?.dataset_version}|${selected}|${pickKey}`;
  useEffect(() => {
    if (!ws || !selected) return;
    let live = true;
    const key = forKey;
    setRecalc(true);
    const go = () => fetchCandidates(selected, ws.dataset_version, picks)
      .then((c) => { if (live) { setCands(c); setCandsFor(key); setCandError(null); } })
      .catch((e) => { if (live) setCandError({ key, msg: `후보·미리 계산을 하지 못했습니다: ${(e as Error).message}` }); })
      .finally(() => { if (live) setRecalc(false); });
    const t = picks.length ? window.setTimeout(go, 250) : (void go(), undefined);
    return () => { live = false; if (t !== undefined) window.clearTimeout(t); };
  }, [ws, selected, pickKey, retry]); // eslint-disable-line react-hooks/exhaustive-deps
  /** 화면의 후보·미리 계산이 지금 선택의 것인가 */
  const fresh = candsFor === forKey && !recalc;
  /** 지금 선택의 계산이 실패했다(고른 직후 요청 전 한 프레임은 실패가 아니다 -- 리뷰 N1) */
  const failedNow = !recalc && candError?.key === forKey;
  const draftBlocked = fresh && (cands?.draft?.new_violations.length ?? 0) > 0;

  /** 판을 바꾸는 요청 하나 -- 성공하면 새 작업 데이터로 바꾸고 App에도 알린다. */
  async function mutate(run: () => Promise<{ workspace: Workspace }>, done: string, review?: { projectId: string | null }) {
    setBusy(true);
    setError(null);
    setConfirmed(null);
    try {
      const res = await run();
      setWs(res.workspace);
      if (review) setConfirmed({ version: res.workspace.dataset_version, projectId: review.projectId, seen: datasetVersion ?? null });
      setRows([]);
      setAutoState("idle");
      setNotice(withPersistNote(done, res));
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
                                                       ks: Array.from({ length: kMax + 1 }, (_, k) => k) }, ctl.signal)) {
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
      setJustify({ title: `${p.name} — 소명 미리보기(K=${row.k}, 확정 전)`, text: j.text ?? "" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const predecessors = useMemo(() => (ws ? [...ws.ongoing, ...ws.pipeline].map((p) => ({ project_id: p.project_id, name: p.name, end: p.end })) : []),
                               [ws]);
  const parents = useMemo(() => (ws ? ws.ongoing.map((o) => ({ project_id: o.project_id, name: o.name, start: o.start, end: o.end })) : []),
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
  /** 사업 번호 → "J003 은행 A 데이터"(번호만 보이면 부서장이 다시 찾아야 한다 -- 점검표 f7 기조, 2026-10-20) */
  const named = (id: string) => { const n = [...ws.ongoing, ...ws.pipeline].find((x) => x.project_id === id)?.name; return n ? `${id} ${n}` : id; };
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
               actions={<button className="btn-primary" onClick={() => { setFormParent(null); setFormOpen(true); }}>신규 사업 등록</button>}>
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
                        {p.predecessor && <span>이전 사업 {named(p.predecessor)}</span>}
                        {p.parent && <span>추가 과제 — 상위 {named(p.parent)}{p.external_headcount ? ` · 외주 ${p.external_headcount}명(참고)` : ""}</span>}
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
                <span className="text-slate-500">자리</span><Seats seats={project.seats} after={draftList.length && fresh ? cands?.draft?.seats : null} />
                {project.predecessor && <span className="text-xs text-slate-500">고도화 과제 — 이전 사업 {named(project.predecessor)} 팀원이 후보 맨 위에 옵니다</span>}
                {project.parent && (
                  <span className="text-xs text-slate-500">
                    추가 과제 — 상위 사업 {named(project.parent)} 팀원이 후보 맨 위에 옵니다
                    {project.external_headcount ? ` · 외주 ${project.external_headcount}명(참고, 계산에 쓰지 않음)` : ""}
                  </span>
                )}
              </div>
              <div className="text-sm">
                <span className="text-slate-500">지금 팀</span>{" "}
                {project.team.length ? project.team.map((m) => (
                  <span key={m.person_id} className="mr-1.5 inline-flex items-center gap-1">
                    {m.grade && <Chip tone={GRADE_TONE[m.grade]}>{m.grade}</Chip>}{m.name}
                  </span>
                )) : <span className="text-slate-400">아직 없음</span>}
              </div>
              <PickDraft projectId={project.project_id} picks={draftList} summary={fresh ? cands?.draft : null} recalculating={recalc}
                         failed={failedNow} onRetry={() => setRetry((n) => n + 1)} named={named}
                         onRemove={(id) => setDraft((d) => { const n = { ...d }; delete n[id]; return n; })} />
              {failedNow && candError && <Notice tone="bad">{candError.msg}</Notice>}
              <Tabs label="후보 갈래" value={group} onChange={setGroup}
                    items={(Object.keys(GROUP_LABEL) as Group[]).map((g) => [g, `${GROUP_LABEL[g]} ${cands ? cands[g].length : ""}`])} />
              {!cands ? (failedNow ? null : <p className="text-sm text-slate-500">후보를 계산하는 중…(1~2초)</p>) : groupRows.length === 0 ? (
                <p className="text-sm text-slate-500">이 갈래에는 후보가 없습니다.</p>
              ) : (
                <table className={`w-full text-sm ${fresh ? "" : "opacity-60"}`} aria-busy={recalc}>
                  <thead className="text-left text-xs text-slate-500">
                    <tr><th className="py-1">이름</th><th>등급</th>
                      <th title="이 사람을 넣으면 사업별 정원(자리)이 어떻게 바뀌는지 -- 아래 '표 읽는 법'">정원 변화 ⓘ</th>
                      <th className="text-right" title="이 사업 요구 기술에 맞는 정도">기술</th>
                      <th className="pr-3 text-right" title="두 사람 사이 점수 -- 이 사업 팀원·넣기로 고른 사람과의 쌍만 셉니다">협업 ⓘ</th>
                      <th>비고</th><th /></tr>
                  </thead>
                  <tbody>
                    {groupRows.map((r) => {
                      const picked = !!draft[r.person_id];
                      const lines = r.seat_effects ? seatLines(r.seat_effects, project.project_id) : null;
                      const sc = skillCollab(r);
                      const who = partnersText(r);
                      return (
                        <tr key={r.person_id} className="border-t border-slate-100 align-top">
                          <td className="py-1.5"><PersonName name={r.name} person={pById.get(r.person_id)} />{r.predecessor_team && <span className="ml-1.5"><Chip tone="stage">이전 팀원</Chip></span>}
                            {r.parent_team && <span className="ml-1.5"><Chip tone="stage">상위 사업 팀원</Chip></span>}</td>
                          <td className="py-1.5"><Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip></td>
                          <td className="py-1.5 text-xs">
                            {lines ? (lines.length ? lines.map((l, i) => (
                              <div key={i} className={l.tone === "warn" ? "text-amber-700" : "text-pine-800"} title={l.title}>{l.text}</div>
                            )) : <span className="text-slate-400">—</span>) : seatsText(r.unfilled_seats_delta)}
                          </td>
                          <td className="num py-1.5 text-right">{signedPts(sc.skill)}</td>
                          <td className="py-1.5 pr-3 text-right">
                            <span className="num">{sc.collab === undefined ? "–" : signedPts(sc.collab)}</span>
                            {who && <div className="text-[11px] text-slate-500">{who}</div>}
                          </td>
                          <td className="text-xs text-slate-500">
                            {r.pulled_from.length ? <><ProjectCodes ids={r.pulled_from} projects={projects} />에서 빼 옴</> : r.source === "partly_free" ? "남는 시간으로"
                              : r.source === "concurrent" ? "원래 사업 유지(프로젝트 동시 수행)" : ""}
                            {r.new_violations.length > 0 && <span className="ml-1 text-amber-700">넣으면 위반({violationReasons(r.new_violations)})</span>}
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
              {cands && groupRows.length > 0 && <CandidateGuide />}
              {cands && <UnavailableList rows={cands.unavailable} />}
              {cands && <p className="text-xs text-slate-400">{cands.note}</p>}
            </div>
          </Panel>
        ) : (
          <Panel title="신규 사업을 고르세요"><EmptyState title="왼쪽에서 사업을 고르면 후보가 세 갈래로 나옵니다" /></Panel>
        )}
      </div>

      {draftList.length > 0 && project && (
        <ActionBar note="넣기로 고른 사람을 이 사업에 확정합니다. 빼 오는 사람은 원래 사업에서 빠집니다.">
          <button className="btn-primary" disabled={busy || !fresh || draftBlocked} onClick={applyDraft}
                  title={!fresh ? "고른 사람의 미리 계산이 끝나야 확정할 수 있습니다"
                    : draftBlocked ? "확정하면 규칙 위반이 생깁니다(위 '넣기로 고른 사람' 참고)" : undefined}>{draftList.length}명 확정</button>
          <button className="btn-ghost" onClick={() => setDraft({})}>고른 것 지우기</button>
        </ActionBar>
      )}

      <Panel title="자동 편성 — 몇 명까지 옮길까" sub={`신규 사업을 채울 때 진행 사업에서 데려와도 되는 사람 수(K)를 0~${kMax}명으로 바꿔 가며 계산해 나란히 보여 줍니다. 품질이 높고 빈자리가 적은 줄을 고르면 됩니다. 고정한 사람은 옮기지 않습니다.`}
             actions={<>
               <label className="flex items-center gap-1.5 text-xs text-slate-600">최대
                 <select className="rounded border border-slate-300 px-1.5 py-1 text-xs" value={kMax} disabled={autoState === "running"}
                         aria-label="옮길 수 있는 최대 인원" onChange={(e) => { kTouched.current = true; setKMax(Number(e.target.value)); }}>
                   {[3, 4, 5].map((k) => <option key={k} value={k}>{k}명</option>)}
                 </select>
               </label>
               <button className="btn" disabled={autoState === "running" || busy} onClick={() => void runAuto()}>
                 {autoState === "running" ? `계산 중…(100명 약 ${kMax >= 5 ? 45 : kMax === 4 ? 35 : 30}초)` : "옮길 인원별로 계산"}
               </button>
             </>}>
        {rows.length === 0 ? (
          <EmptyState title={autoState === "running" ? "계산하고 있습니다" : "아직 계산하지 않았습니다"}
                      body="밤 배치가 미리 계산해 두었으면 바로 나옵니다." />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr><th className="px-5 py-1.5" title="진행 사업에서 데려와도 되는 최대 인원">옮겨도 되는 인원(K)</th>
                <th className="text-right" title="빈자리 감점을 뺀 배치 품질 = 기술 적합 + 협업 보상 − 오래 함께한 사이 감점. 높을수록 좋고, 같은 데이터에서 K끼리 비교할 때만 뜻이 있습니다(실제 성과는 검증 전)">품질 ⓘ</th>
                <th className="text-right" title="모든 사업에서 채우지 못한 정원 자리 수">빈자리</th>
                <th>옮김</th><th>소명 미리보기</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.k} className="border-t border-slate-100">
                  <td className="px-5 py-2 font-semibold">{r.k === 0 ? "옮기지 않음" : `최대 ${r.k}명`} <span className="text-xs font-normal text-slate-400">K={r.k}</span></td>
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
                              aria-label={`K=${r.k} ${p.name} 소명 미리보기`} title={p.name}
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
        {(() => {
          // 전일 투입에서 가장 큰 K까지 풀어도 빈자리가 남으면 -- 동시 수행 허용 후보(2026-10-19 사용자 결정, 사람 단위)
          const last = [...rows].reverse().find((r) => r.accepted && (r.unfilled_seats ?? 0) > 0 && r.entries);
          // K 행을 계산한 설정으로 정한다(지금 설정이 아니라, 폴백 리뷰 P3)
          return last?.entries && (autoOf.current?.params ?? milpParams)?.allocation_mode === "full" ? (
            <div className="px-5 pb-4">
              <ConcurrencyPanel datasetVersion={ws.dataset_version} entries={last.entries}
                                projectName={(id) => [...ws.pipeline, ...ws.ongoing].find((p) => p.project_id === id)?.name ?? id}
                                onGranted={(msg) => { setRows([]); setAutoState("idle"); void load(); onDatasetChanged(); setNotice(msg); }}
                                onStale={() => { void load(); onDatasetChanged(); }} />
            </div>
          ) : null;
        })()}
      </Panel>

      {(ws.concurrency?.length ?? 0) > 0 && (
        <Panel title="프로젝트 동시 수행 허용 인력" sub="부서장이 사유와 함께 허용한 사람만 같은 달 여러 사업을 맡습니다(사업마다 100%).">
          <ul className="divide-y divide-slate-100">
            {ws.concurrency!.map((g) => (
              <li key={g.person_id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-2 text-sm">
                <span>{g.name} <span className="text-xs text-slate-500">({g.grade}) · 최대 {g.max_projects}개 · {g.reason}
                  {g.memo ? ` · ${g.memo}` : ""}{g.granted_at ? ` · ${g.granted_at}` : ""}</span></span>
                <button className="btn px-2 py-0.5 text-xs" disabled={busy} aria-label={`${g.name} 동시 수행 해제`}
                        onClick={() => void mutate(() => postConcurrency(ws.dataset_version, g.person_id, false),
                                                   `${g.name}의 프로젝트 동시 수행을 해제했다.`)}>해제</button>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel title="진행 사업 — 고정" sub="자물쇠를 채운 사업·사람은 어떤 계산도 빼 가지 않습니다(사람을 더 넣는 것은 됩니다).">
        <ul className="divide-y divide-slate-100">
          {ws.ongoing.map((o: OngoingItem) => (
            <li key={o.project_id} className="px-5 py-2.5 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <button className="text-left" onClick={() => setOpenTeam(openTeam === o.project_id ? null : o.project_id)}
                        aria-expanded={openTeam === o.project_id}>
                  <span className="font-medium">{o.name}</span>{" "}
                  <span className="num text-xs text-slate-500">{o.project_id} · {o.start ? `${o.start}~` : "~"}{o.end} · {o.team.length}명</span>
                  <LifecycleChips lc={o.lifecycle} name={o.name} />
                  {o.ends_in_horizon && <span className="ml-1.5"><Chip tone="warn">곧 종료 {o.end}</Chip></span>}
                  {o.locked_count > 0 && !o.locked_all && <span className="ml-1.5 text-xs text-slate-500">고정 {o.locked_count}명</span>}
                  {(o.addons ?? []).map((a) => <span key={a} className="ml-1.5"><Chip tone="stage">추가 과제 {a}</Chip></span>)}
                </button>
                <button className="btn ml-auto px-2.5 py-1 text-xs" disabled={busy} aria-label={`${o.name}에 추가 과제 붙이기`}
                        onClick={() => { setFormParent(o.project_id); setFormOpen(true); }}>
                  + 추가 과제
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
        <ProjectForm start={ws.horizon_start} predecessors={predecessors} parents={parents} initialParent={formParent}
                     onCancel={() => { setFormOpen(false); setFormParent(null); }}
                     onSubmit={async (form) => {
                       let res;
                       try {
                         res = await postProject(ws.dataset_version, form);
                       } catch (e) {
                         if (isDatasetChanged(e)) { void load(); onDatasetChanged(); }
                         throw e;
                       }
                       setFormOpen(false);
                       setFormParent(null);
                       setWs(res.workspace);
                       setSelected(res.project_id);
                       setNotice(withPersistNote(`${form.name}(${res.project_id})을(를) 등록했다.`, res));
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
