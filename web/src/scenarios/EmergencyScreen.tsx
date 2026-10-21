/** 상황 3 -- 긴급 투입(2026-10-19 claude-b). 진행 사업에 이슈·사고가 나서 사람을 더 넣는다 -- 넣은 사람은 지금부터 사업이 끝날
 *  때까지 남는다(사용자 결정: "계속 남는 경우로만"). 사업을 고르면 밤 배치가 뽑아 둔 "지금 바로 넣을 수 있는 사람"이 바로 뜨고,
 *  상황(필요 기술·인원·등급·빼 오기 허용)을 적으면 최선 조합과 후보 순위가 나온다. 확정하면 그 사업의 정원·예산·요구 기술이
 *  늘어난 새 판이 된다(서버가 계산한 apply_hint 그대로). */
import { useEffect, useMemo, useRef, useState } from "react";
import { ActionBar, Chip, Dialog, EmptyState, GRADE_TONE, Notice, Panel, Spinner, Tabs } from "../ui";
import { UnavailableList } from "./UnavailableList";
import { ScoreSplitNote } from "../components/ScoreSplitNote";
import { seatsText, signedPts, splitText } from "../components/scoreSplit";
import { hintText, partnersText, skillCollab, violationReasons } from "./pickView";
import { PersonName, ProjectCodes } from "../components/PersonName";
import { PickDraft } from "./PickDraft";
import { GradeSwapBox, hintBody, swapList, swapsReady, type SwapReasons } from "./GradeSwapBox";
import { SeatHoleBox, holesReady, waiverBody, type WaiverReasons } from "./SeatHoleBox";
import type { Person, Project } from "../api/types";
import {
  currentEntries, fetchSkillDictionary, fetchStandby, fetchWorkspace, GRADES, isDatasetChanged, postApply, postEmergency,
  postEmergencyPreview, withPersistNote, type CandidateRow, type EmergencyDraft, type EmergencyPlan, type FillPick, type Grade, type SkillDictEntry,
  type StandbyRow, type Workspace,
} from "./workspaceApi";

type Group = "bench" | "spare" | "pull";
// 확정 요청 한도(api/routes/workspace.ApplyIn.note 300 · GrantIn.memo 200). 상황 글(300자)에 머리말이 붙으면 넘쳐 확정이 422였다 --
// 원문은 situation으로 따로 가니 이 둘은 잘라 보낸다(폴백 리뷰 S2, 2026-10-19)
const NOTE_MAX = 300;
const GRANT_MEMO_MAX = 200;
/** 한 번에 고를 수 있는 사람 수(서버 EmergencyPreviewIn.picks 10) */
const PICK_MAX = 10;
/** 글자(코드 포인트) 단위로 자른다 -- UTF-16 단위로 자르면 이모지가 경계에서 반쪽이 되어 서버가 422로 거절한다(2라운드 리뷰 NIT) */
const clip = (s: string, n: number) => Array.from(s).slice(0, n).join("");
const GROUP_LABEL: Record<Group, string> = { bench: "쉬는 인력", spare: "여유 있는 인력", pull: "다른 사업에서 빼 오기" };

export function EmergencyScreen({ datasetVersion, onDatasetChanged, onReviewDoc, people, projects }: {
  datasetVersion?: string | null; onDatasetChanged: () => void;
  /** 이름을 누르면 기술 창, 사업 코드에 마우스를 올리면 이름(2026-10-20 화면 친절하게) -- 없으면 이름·코드만 */
  people?: Person[]; projects?: Project[];
  /** 확정 뒤 "인사팀 검토 자료 만들기"(claude-a 2026-10-19 -- 사용자 결정: 긴급 투입도 자료) */
  onReviewDoc?: (version: string, projectId: string | null) => void;
}) {
  const [confirmed, setConfirmed] = useState<{ version: string; projectId: string | null; seen: string | null } | null>(null);
  useEffect(() => {                                  // 판이 바뀌면 옛 확정의 버튼은 지운다(리뷰 P3)
    if (!confirmed || !datasetVersion) return;
    // 옛 판(seen) 예외는 새 판과 한 번 맞춰질 때까지만 -- 그 뒤 되돌림 등으로 옛 판에 돌아가면 버튼을 지운다(Codex 리뷰 2라운드)
    if (datasetVersion === confirmed.version) { if (confirmed.seen !== null) setConfirmed({ ...confirmed, seen: null }); }
    else if (datasetVersion !== confirmed.seen) setConfirmed(null);
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
  // 프로젝트 동시 수행으로 지원(2026-10-19 사용자 결정): 원래 사업은 그대로 두고 이 사업에도 100% -- 확정이 허용(사유 "긴급 지원")을 함께 적는다
  const [support, setSupport] = useState<CandidateRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // 손 편성 미리 보기(2026-10-20 사용자 "긴급 투입에도 미리 보기"): 후보 표에서 "넣기"로 고른 사람 -- 확정 전, 서버가 증원까지 계산한다.
  // 미리 계산은 그것을 계산한 선택(판·사업·기술·고른 사람)의 키와 함께 둔다 -- 지금 선택과 다르면 보이지 않고 확정을 막는다(신규 사업 화면과 같은 규칙)
  const [picks, setPicks] = useState<Record<string, CandidateRow>>({});
  const [preview, setPreview] = useState<{ key: string; draft: EmergencyDraft; candidates: EmergencyPlan["candidates"] } | null>(null);
  const [previewErr, setPreviewErr] = useState<{ key: string; msg: string } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewRetry, setPreviewRetry] = useState(0);
  const [pickConfirm, setPickConfirm] = useState(false);
  /** 등급 대체 사유(2026-10-21) -- 열쇠 = 사업:자리>등급, 세 확정 경로가 함께 쓴다 */
  const [swapReasons, setSwapReasons] = useState<SwapReasons>({});
  const onSwapReason = (k: string, v: string) => setSwapReasons((r) => ({ ...r, [k]: v }));
  /** 빼 온 자리(2026-10-21): 메울 사람(손 편성 -- 사람별) · 메우지 않음 사유(열쇠 = 사업:등급, 최선 조합·손 편성이 함께 쓴다) */
  const [fills, setFills] = useState<Record<string, FillPick>>({});
  const [waivers, setWaivers] = useState<WaiverReasons>({});
  const onWaive = (k: string, v: string | null) => setWaivers((w) => { const nx = { ...w }; if (v === null) delete nx[k]; else nx[k] = v; return nx; });
  /** "후보 찾기" 때의 빼 오기 허용 -- 다시 매기기는 그 값으로(입력칸을 나중에 바꿔도 결과와 맞게, 2라운드 리뷰 NIT) */
  const [searchedPull, setSearchedPull] = useState(0);

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
    setSupport(null);                            // 옛 결과의 동시 수행 지원 대화창이 다시 뜨지 않게(폴백 리뷰 P3)
    setBusy(false);                              // 진행 중이던 검색은 버렸다 -- "찾는 중"에 멈추지 않게(대체 리뷰 MUST)
    if (!ws || !projectId) return;
    let live = true;
    fetchStandby(ws.dataset_version, projectId)
      .then((s) => { if (live) setStandby({ rows: s.projects[projectId] ?? [], source: s.source }); })
      .catch(() => { if (live) setStandby({ rows: [], source: null }); });
    return () => { live = false; };
  }, [ws, projectId]);

  useEffect(() => { setPicks({}); setFills({}); setWaivers({}); setPreview(null); setPreviewErr(null); setPickConfirm(false); }, [plan]);
  const pickList = Object.values(picks);
  const pickBody = pickList.map((r) => ({ person_id: r.person_id, alloc: r.alloc, pulled_from: r.pulled_from,
                                         grant: r.source === "concurrent" && !!r.needs_grant }));
  /** 메울 사람은 지금 고른 사람이 빼 온 사업에만(고른 사람을 지우면 그 사업 메우기도 빠진다) */
  const sources = new Set(pickList.flatMap((r) => r.pulled_from));
  const fillBody = Object.values(fills).filter((f) => sources.has(f.project_id) && !picks[f.person_id]);
  const pickKey = `${plan?.dataset_version}|${plan?.project_id}|${(plan?.skills ?? []).join(",")}|${JSON.stringify(pickBody)}|${JSON.stringify(fillBody)}`;
  useEffect(() => {
    if (!plan || !pickBody.length) { setPreviewing(false); return; }
    let live = true;
    const key = pickKey;
    const ctl = new AbortController();                  // 바뀐 선택의 옛 요청은 끊는다(서버 계산 자리를 오래 잡지 않게, 대체 리뷰 SHOULD)
    setPreviewing(true);
    const t = window.setTimeout(() => {
      postEmergencyPreview({ dataset_version: plan.dataset_version, project_id: plan.project_id, skills: plan.skills, picks: pickBody,
                             pull_budget: searchedPull, fills: fillBody }, ctl.signal)
        .then((r) => { if (live) { setPreview({ key, draft: r.draft, candidates: r.candidates }); setPreviewErr(null); } })
        .catch((e) => { if (live && (e as Error).name !== "AbortError") setPreviewErr({ key, msg: `미리 계산을 하지 못했습니다: ${(e as Error).message}` }); })
        .finally(() => { if (live) setPreviewing(false); });
    }, 250);
    return () => { live = false; window.clearTimeout(t); ctl.abort(); };
  }, [pickKey, previewRetry]); // eslint-disable-line react-hooks/exhaustive-deps
  const pickFresh = !!preview && preview.key === pickKey && !previewing;
  const pickDraft = pickFresh ? preview!.draft : null;
  const pickBlocked = !pickDraft || pickDraft.new_violations.length > 0;
  const pickSwapMissing = !!pickDraft && !swapsReady(swapList(pickDraft.apply_hint.grade_swaps), swapReasons);
  const bestSwapMissing = !swapsReady(swapList(plan?.apply_hint?.grade_swaps), swapReasons);
  const pickHoleMissing = !!pickDraft && !holesReady(pickDraft.holes, waivers);
  const bestHoleMissing = !holesReady(plan?.best.holes, waivers);
  /** 고른 사람이 있으면 그 사람들을 팀원으로 친 후보(미리 계산과 함께 온 다시 매기기 -- 다시 계산하는 동안은 직전 것을 흐리게), 없으면 "후보 찾기" 결과 */
  const shown = pickList.length && preview ? preview.candidates : plan?.candidates;
  const shownStale = pickList.length > 0 && !pickFresh;

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
      if (gen === reqGen.current && res.project_id === projectId) { setSearchedPull(pull); setPlan(res); }
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
      // 결과를 계산한 판·사업 그대로 확정한다(등급 대체는 사유를 붙여서)
      const res = await postApply(plan.dataset_version, { entries: plan.best.entries, source: "emergency",
        note: clip(`${plan.project_id} 긴급 투입${note ? `: ${note}` : ""}`, NOTE_MAX), situation: note.trim(), ...hintBody(hint, swapReasons),
        ...(waiverBody(plan.best.holes, waivers).length ? { seat_waivers: waiverBody(plan.best.holes, waivers) } : {}) });
      setWs(res.workspace);
      setConfirmed({ version: res.workspace.dataset_version, projectId: plan.project_id, seen: datasetVersion ?? null });
      setPlan(null);
      setNotice(withPersistNote(`${project?.name ?? projectId}에 ${plan.best.added?.map((a) => a.name).join(", ")}을(를) 넣었다(사업 끝까지).`, res));
      onDatasetChanged();
    } catch (e) {
      setError((e as Error).message);
      if (isDatasetChanged(e)) { setPlan(null); reloadWorkspace(); onDatasetChanged(); }
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  async function applySupport(row: CandidateRow) {
    // 지금 결과의 후보인지(옛 결과·다른 사업의 행이 아닌지) 확인한다(폴백 리뷰 P3)
    if (!ws || !plan || plan.dataset_version !== ws.dataset_version || plan.project_id !== projectId
        || !plan.candidates.spare.some((r) => r.person_id === row.person_id) || !row.apply_hint) {
      setSupport(null);
      return;
    }
    setBusy(true);
    try {
      const pid = plan.project_id;
      const res = await postApply(ws.dataset_version, {
        entries: [...currentEntries(ws), { person_id: row.person_id, project_id: pid, alloc: 1 }], source: "emergency",
        note: clip(`${pid} 긴급 투입(프로젝트 동시 수행 지원)${note ? `: ${note}` : ""}`, NOTE_MAX), situation: note.trim(),
        ...hintBody(row.apply_hint, swapReasons),   // 서버가 이 사람으로 계산한 증원(넘친 만큼의 정원·예산 + 더할 요구 기술)·등급 대체
        // 이미 허용된 사람은 기존 허용 기록(사유·메모)을 그대로 둔다 -- 새 허용이 필요할 때만 긴급 지원으로 적는다
        ...(row.needs_grant ? { concurrency_add: [{ person_id: row.person_id, memo: clip(note || `${pid} 긴급 투입`, GRANT_MEMO_MAX) }] } : {}),
      });
      setWs(res.workspace);
      setConfirmed({ version: res.workspace.dataset_version, projectId: pid, seen: datasetVersion ?? null });
      setPlan(null);
      setNotice(withPersistNote(`${row.name}이(가) 원래 사업은 그대로 두고 ${project?.name ?? pid}도 함께 맡는다(프로젝트 동시 수행, ${row.needs_grant ? "사유 긴급 지원" : "이미 허용됨"}).`, res));
      onDatasetChanged();
    } catch (e) {
      setError((e as Error).message);
      if (isDatasetChanged(e)) { setPlan(null); reloadWorkspace(); onDatasetChanged(); }
    } finally {
      setBusy(false);
      setSupport(null);
    }
  }

  async function applyPicks() {
    // 지금 선택의 미리 계산(서버가 만든 배치·증원)으로만 확정한다
    if (!ws || !plan || !pickDraft || pickBlocked || plan.dataset_version !== ws.dataset_version || plan.project_id !== projectId) {
      setPickConfirm(false);
      return;
    }
    setBusy(true);
    try {
      const pid = plan.project_id;
      const grants = pickBody.filter((p) => p.grant);
      const res = await postApply(plan.dataset_version, {
        entries: pickDraft.entries, source: "emergency",
        note: clip(`${pid} 긴급 투입(손 편성)${note ? `: ${note}` : ""}`, NOTE_MAX), situation: note.trim(), ...hintBody(pickDraft.apply_hint, swapReasons),
        ...(waiverBody(pickDraft.holes, waivers).length ? { seat_waivers: waiverBody(pickDraft.holes, waivers) } : {}),
        ...(grants.length ? { concurrency_add: grants.map((g) => ({ person_id: g.person_id, memo: clip(note || `${pid} 긴급 투입`, GRANT_MEMO_MAX) })) } : {}),
      });
      setWs(res.workspace);
      setConfirmed({ version: res.workspace.dataset_version, projectId: pid, seen: datasetVersion ?? null });
      setPlan(null);
      setNotice(withPersistNote(`${project?.name ?? pid}에 ${pickList.map((r) => r.name).join(", ")}을(를) 넣었다(사업 끝까지).`, res));
      onDatasetChanged();
    } catch (e) {
      setError((e as Error).message);
      if (isDatasetChanged(e)) { setPlan(null); reloadWorkspace(); onDatasetChanged(); }
    } finally {
      setBusy(false);
      setPickConfirm(false);
    }
  }

  if (!ws) return error ? <Notice tone="bad">{error}</Notice> : <p className="text-sm text-slate-500">작업 데이터를 불러오는 중…</p>;
  if (!ws.editable || ws.ongoing.length === 0) {
    return <Panel title="긴급 투입"><EmptyState title="진행 중인 사업이 없습니다"
                  body={ws.reason ?? "현재 배치가 있는 운영 중 데이터에서 씁니다 — 편성 기록에서 운영 중 데이터를 여세요."} /></Panel>;
  }
  const rows: CandidateRow[] = shown ? shown[group] : [];
  const pulled = plan?.best.diff?.moved ?? [];
  const named = (id: string) => { const nm = projects?.find((x) => x.id === id)?.name; return nm ? `${id} ${nm}` : id; };
  const pById = new Map((people ?? []).map((p) => [p.id, p]));
  // 전일 투입이면 모두 100% -- 투입률 칸은 100%가 아닌 후보가 있을 때만(사용자 2026-10-20 "100프로는 빼도")
  const splitAlloc = (shown ? Object.values(shown).flat() : []).some((r) => Math.abs(r.alloc - 1) > 1e-9);
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
              {busy ? <Spinner status={false} label="찾는 중…" /> : "후보 찾기"}
            </button>
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="지금 바로 넣을 수 있는 사람" sub={`${standby?.source === "nightly" ? "밤사이 미리 뽑아 둔" : "지금 계산한"} 순위 — 다른 사업에서 빼 오지 않고 규칙 위반 없이 넣을 수 있는 상위 5명`}>
            {!projectId ? <EmptyState title="사업을 고르면 바로 보입니다" />
              : !standby ? <p className="px-5 py-4 text-sm text-slate-500">불러오는 중…</p>
              : standby.rows.length === 0 ? <EmptyState title="빼 오지 않고 넣을 수 있는 사람이 없습니다" body="빼 오기를 허용해 후보를 찾으세요." />
              : (
                <ul className="flex flex-wrap gap-2 px-5 py-4" aria-label="대기 명단">
                  {standby.rows.map((r) => (
                    <li key={r.person_id} className="rounded-md border border-line px-3 py-1.5 text-sm">
                      <Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip> <PersonName name={r.name} person={pById.get(r.person_id)} />
                      <span className="num ml-1.5 text-xs text-slate-500">{Math.abs(r.alloc - 1) > 1e-9 ? `${Math.round(r.alloc * 100)}% · ` : ""}{splitText(r)}</span>
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
                      <span className="num ml-2 text-xs text-slate-500">{splitText({ ...plan.best, delta_total: plan.best.delta?.total })} · 월 비용 +{(plan.best.added_cost ?? 0).toLocaleString()}(가상 비용 점수)</span></p>
                    {pulled.length > 0 && <p className="text-xs text-slate-600">빼 오는 사람: {pulled.map((m, i) => (
                      // 사업 코드는 코드 그대로 + 마우스를 올리면 이름(사용자 2026-10-20 -- 후보 표 비고와 같게)
                      <span key={m.person_id}>{i > 0 && " · "}{m.name ?? m.person_id}(<ProjectCodes ids={[m.from]} projects={projects} />→<ProjectCodes ids={m.to} projects={projects} />)</span>
                    ))}</p>}
                    {(plan.best.new_violations?.length ?? 0) > 0 && <Notice tone="warn">이 조합은 새 위반({violationReasons(plan.best.new_violations ?? [])})이 있어 확정할 수 없습니다.</Notice>}
                    {plan.apply_hint && (Object.keys(plan.apply_hint.headcount_add).length > 0 || Object.keys(plan.apply_hint.budget_add).length > 0) && (
                      <p className="text-xs text-slate-500">확정하면 사업 정원
                        {Object.values(plan.apply_hint.headcount_add).flatMap((h) => Object.entries(h)).map(([g, c]) => ` ${g} +${c}`)}
                        {Object.values(plan.apply_hint.budget_add).map((b) => ` · 월 예산 +${b}`)}이(가) 늘어납니다(증원).</p>
                    )}
                    {pickList.length === 0 && (
                      <GradeSwapBox items={swapList(plan.apply_hint?.grade_swaps)} reasons={swapReasons} onReason={onSwapReason} disabled={busy} />
                    )}
                    {pickList.length === 0 && (
                      // 최선 조합은 빼 온 자리를 같은 조합에서 메운다 -- 누가 메우는지 보이고, 못 메운 자리는 "메우지 않음"(메울 사람을 고르려면 손 편성)
                      <SeatHoleBox holes={plan.best.holes} fills={plan.best.fills} waivers={waivers} onWaive={onWaive} named={named} disabled={busy} />
                    )}
                  </>
                )}
                <PickDraft projectId={plan.project_id} picks={pickList} summary={pickDraft} recalculating={previewing}
                           noSeatChange="이 사업은 아래 증원으로 채웁니다"
                           failed={!previewing && previewErr?.key === pickKey} onRetry={() => setPreviewRetry((x) => x + 1)}
                           named={named}
                           onRemove={(id) => setPicks((d) => { const nx = { ...d }; delete nx[id]; return nx; })} />
                {pickDraft && <p className="text-xs text-slate-500">{hintText(pickDraft.apply_hint, "고른 사람으로 확정하면")}</p>}
                {pickDraft && <GradeSwapBox items={swapList(pickDraft.apply_hint.grade_swaps)} reasons={swapReasons} onReason={onSwapReason}
                                            budgetAdd={Object.values(pickDraft.apply_hint.budget_add).reduce((a, b) => a + b, 0)} disabled={busy} />}
                {pickDraft && <SeatHoleBox holes={pickDraft.holes} fillOptions={pickDraft.fill_options} fills={pickDraft.fills}
                                           waivers={waivers} onWaive={onWaive} named={named} disabled={busy}
                                           removable={new Set(fillBody.map((f) => f.person_id))}
                                           onFill={(f) => setFills((d) => ({ ...d, [f.person_id]: f }))}
                                           onUnfill={(pid) => setFills((d) => { const nx = { ...d }; delete nx[pid]; return nx; })} />}
                {pickList.length > 0 && <p className="text-xs text-slate-500">{pickFresh
                  ? "아래 후보는 고른 사람을 팀원으로 친 다시 계산입니다(위 최선 조합은 그대로)." : "고른 사람을 반영해 후보를 다시 계산하는 중…"}</p>}
                <Tabs label="후보 갈래" value={group} onChange={setGroup}
                      items={(Object.keys(GROUP_LABEL) as Group[]).map((g) => [g, `${
                        g === "spare" && (shown ?? plan.candidates).spare.some((r) => r.source === "concurrent") ? "동시 수행으로 지원" : GROUP_LABEL[g]
                      } ${(shown ?? plan.candidates)[g].length}`])} />
                {rows.length === 0 ? <p className="text-slate-500">이 갈래에는 후보가 없습니다.</p> : (
                  <table className={`w-full ${shownStale ? "opacity-60" : ""}`} aria-busy={shownStale}>
                    <thead className="text-left text-xs text-slate-500"><tr><th>이름</th><th>등급</th>{splitAlloc && <th className="text-right">투입률</th>}
                      <th className="text-right" title="모든 사업 빈자리 합의 변화 -- 아래 설명">빈자리 변화 ⓘ</th>
                      <th className="text-right" title="이 사업 요구 기술에 맞는 정도">기술</th>
                      <th className="pr-3 text-right" title="두 사람 사이 점수 -- 이 사업 팀원과의 쌍만 셉니다">협업 ⓘ</th><th>비고</th><th><span className="sr-only">넣기</span></th></tr></thead>
                    <tbody>
                      {rows.map((r) => { const sc = skillCollab(r); const who = partnersText(r); return (
                        <tr key={r.person_id} className="border-t border-slate-100 align-top">
                          <td className="py-1.5"><PersonName name={r.name} person={pById.get(r.person_id)} /></td><td><Chip tone={GRADE_TONE[r.grade]}>{r.grade}</Chip></td>
                          {splitAlloc && <td className="num text-right">{Math.round(r.alloc * 100)}%</td>}
                          <td className={`num text-right text-xs ${(r.unfilled_seats_delta ?? 0) < 0 ? "text-amber-700" : ""}`}>{seatsText(r.unfilled_seats_delta)}</td>
                          <td className="num text-right">{signedPts(sc.skill)}</td>
                          <td className="pr-3 text-right"><span className="num">{sc.collab === undefined ? "–" : signedPts(sc.collab)}</span>
                            {who && <div className="text-[11px] text-slate-500">{who}</div>}</td>
                          <td className="text-xs text-slate-500">{r.pulled_from.length ? <><ProjectCodes ids={r.pulled_from} projects={projects} />에서 빼 옴</> : ""}
                            {r.source === "concurrent" && <span>원래 사업 유지{r.needs_grant ? " · 허용 필요" : ""}</span>}
                            {r.new_violations.length > 0 && <span className="ml-1 text-amber-700">넣으면 위반({violationReasons(r.new_violations)})</span>}
                            {r.source === "concurrent" && r.new_violations.length === 0 && r.apply_hint && (
                              <button type="button" className="ml-2 rounded border border-violet-300 px-1.5 py-0.5 text-violet-800"
                                      aria-label={`${r.name} 동시 수행으로 지원`}
                                      disabled={busy || pickList.length > 0} title={pickList.length > 0 ? "넣기로 고른 사람이 있으면 그 확정을 먼저 합니다" : undefined}
                                      onClick={() => setSupport(r)}>동시 수행으로 지원</button>
                            )}</td>
                          <td className="text-right">
                            <button type="button" className={`btn px-2 py-0.5 text-xs ${picks[r.person_id] ? "border-ink font-semibold" : ""}`}
                                    aria-pressed={!!picks[r.person_id]} aria-label={`${r.name} 넣기`}
                                    disabled={busy || (!picks[r.person_id] && pickList.length >= PICK_MAX)}
                                    title={!picks[r.person_id] && pickList.length >= PICK_MAX ? `한 번에 ${PICK_MAX}명까지 고릅니다` : undefined}
                                    onClick={() => setPicks((d) => { const nx = { ...d }; if (nx[r.person_id]) delete nx[r.person_id]; else nx[r.person_id] = r; return nx; })}>
                              {picks[r.person_id] ? "넣음" : "넣기"}
                            </button>
                          </td>
                        </tr>
                      ); })}
                    </tbody>
                  </table>
                )}
                {rows.length > 0 && <ScoreSplitNote />}
                <UnavailableList rows={plan.unavailable} />
              </div>
            </Panel>
          )}
        </div>
      </div>
      {plan && pickList.length > 0 && (
        <ActionBar note="넣기로 고른 사람을 이 사업에 확정합니다. 빼 오는 사람은 원래 사업에서 빠지고, 사업 정원·예산은 미리 계산한 만큼 늘어납니다.">
          <button className="btn-primary" disabled={busy || pickBlocked || pickSwapMissing || pickHoleMissing} onClick={() => setPickConfirm(true)}
                  title={!pickDraft ? "미리 계산이 끝나야 확정할 수 있습니다" : pickBlocked ? "확정하면 규칙 위반이 생깁니다"
                    : pickSwapMissing ? "등급 대체 사유를 적어야 확정할 수 있습니다"
                    : pickHoleMissing ? "빼 온 자리를 메우거나 '메우지 않음'과 사유를 적어야 확정할 수 있습니다" : undefined}>
            고른 {pickList.length}명 확정</button>
          <button className="btn-ghost" onClick={() => setPicks({})}>고른 것 지우기</button>
        </ActionBar>
      )}
      {plan?.best.accepted && (plan.best.new_violations?.length ?? 0) === 0 && pickList.length === 0 && (
        <ActionBar note="확정하면 지금 배치가 바뀐 새 판이 됩니다(변경 이력에서 되돌릴 수 있음).">
          <button className="btn-primary" disabled={busy || bestSwapMissing || bestHoleMissing} onClick={() => setConfirming(true)}
                  title={bestSwapMissing ? "등급 대체 사유를 적어야 확정할 수 있습니다(결과 아래 '등급 대체')"
                    : bestHoleMissing ? "못 메운 빼 온 자리에 '메우지 않음'과 사유를 적어야 확정할 수 있습니다" : undefined}>이 조합 확정</button>
        </ActionBar>
      )}
      {support && plan && (
        <Dialog title={`${project?.name ?? projectId} — 프로젝트 동시 수행으로 지원`} actions={<>
          <button className="btn-ghost" onClick={() => setSupport(null)} disabled={busy}>취소</button>
          <button className="btn-primary" disabled={busy || !swapsReady(swapList(support.apply_hint?.grade_swaps), swapReasons)}
                  onClick={() => void applySupport(support)}>{busy ? <Spinner status={false} label="처리 중…" /> : "확정"}</button>
        </>}>
          {support.name}은(는) 지금 사업을 그대로 하면서 이 사업도 사업 끝까지 100%로 맡습니다(두 사업 모두 100%, 예산도 각각 전액).
          {support.needs_grant ? " 확정하면 이 사람에게 프로젝트 동시 수행 허용(사유: 긴급 지원)이 함께 기록됩니다." : ""}
          {support.apply_hint && (Object.keys(support.apply_hint.headcount_add).length > 0 || Object.keys(support.apply_hint.budget_add).length > 0)
            ? <> 확정하면 사업 정원{Object.values(support.apply_hint.headcount_add).flatMap((h) => Object.entries(h)).map(([g, c]) => ` ${g} +${c}`)}
                {Object.values(support.apply_hint.budget_add).map((b) => ` · 월 예산 +${b.toLocaleString()}`)}이(가) 함께 늘어납니다.</>
            : " 지금 정원·예산 안에서 들어갑니다."}
          <div className="mt-2"><GradeSwapBox items={swapList(support.apply_hint?.grade_swaps)} reasons={swapReasons} onReason={onSwapReason} disabled={busy} /></div>
        </Dialog>
      )}
      {pickConfirm && plan && pickDraft && (
        <Dialog title={`${project?.name ?? projectId} 긴급 투입 확정(고른 ${pickList.length}명)`} actions={<>
          <button className="btn-ghost" onClick={() => setPickConfirm(false)} disabled={busy}>취소</button>
          <button className="btn-primary" disabled={busy} onClick={() => void applyPicks()}>{busy ? <Spinner status={false} label="처리 중…" /> : "확정"}</button>
        </>}>
          {pickList.map((r) => r.name).join(", ")}을(를) 지금부터 사업이 끝날 때까지 넣습니다. {hintText(pickDraft.apply_hint, "함께")}
          {pickBody.some((p) => p.grant) ? " 원래 사업을 유지하는 사람에게는 프로젝트 동시 수행 허용(사유: 긴급 지원)이 함께 기록됩니다." : ""}
        </Dialog>
      )}
      {confirming && plan && (
        <Dialog title={`${project?.name ?? projectId} 긴급 투입 확정`} actions={<>
          <button className="btn-ghost" onClick={() => setConfirming(false)} disabled={busy}>취소</button>
          <button className="btn-primary" disabled={busy} onClick={() => void apply()}>{busy ? <Spinner status={false} label="처리 중…" /> : "확정"}</button>
        </>}>
          {plan.best.added?.map((a) => a.name).join(", ")}을(를) 지금부터 사업이 끝날 때까지 넣습니다.
        </Dialog>
      )}
    </div>
  );
}
