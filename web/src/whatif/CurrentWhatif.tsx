/** What-if — 지금 배치에서 바꿔 보기(2026-10-21 사용자 "what if는 현재 프로젝트 인력 배치에서 부서장이 이렇게 바꿔봤을때 어떤지 보기 위한 메뉴",
 *  claude-a). 예전 What-if 대시보드(계산한 안 A~D 위에서 바꿔 보기)는 연간 계획 ② 편성안 비교·확정으로 옮겼다.
 *
 *  기준은 작업 데이터의 현재 배치다. 교체 검토 부품(SwapControl·BriefingPanel·TeamRelations)을 그대로 쓰고, 평가는 `/api/whatif`(편성안 서명 없이
 *  아무 명단이나 평가), 담기는 `/api/plans/apply-swap`(서버가 교체를 적용한 명단을 돌려줌, 저장 안 함). 담은 변경은 한 번에 확정한다
 *  (`ConfirmChanges` -- 등급 대체 사유·예산 증액·고정 충돌, 손 편성 확정이라 인사팀 검토 자료로 이어진다). 가중치는 기본값(확정·검토 자료와 같은 기준). */
import { useEffect, useMemo, useState } from "react";
import { DatasetChangedError, applySwap, postWhatif } from "../api/client";
import type { AssignEntry, Meta, PlacementSettings, Swap, WhatifResponse } from "../api/types";
import { AssignmentTable } from "../components/AssignmentTable";
import { BriefingPanel } from "../components/BriefingPanel";
import { ConfirmChanges } from "../components/ConfirmChanges";
import { SwapControl, type SwapPick } from "../components/SwapControl";
import { TeamRelations } from "../components/TeamRelations";
import { signedPts } from "../components/scoreSplit";
import { EmptyState, Notice, Panel, Spinner } from "../ui";
import { currentEntries, fetchWorkspace, type Workspace } from "../scenarios/workspaceApi";

interface Staged { entries: AssignEntry[]; swap: Swap; delta: number; feasible: boolean; warnings: string[]; swaps: string[] }

/** 지금 배치 대비 등급 대체 줄("J001 특급 자리 1개 → 초급") -- 서버가 확정과 같은 규칙으로 센 값 */
function swapLines(bySite: Record<string, { seat: string; grade: string; count: number }[]> | undefined): string[] {
  return Object.entries(bySite ?? {}).flatMap(([j, xs]) => xs.map((s) => `${j} ${s.seat} 자리 ${s.count}개 → ${s.grade}`));
}

function swapLine(s: Swap, nameOf: (id: string) => string): string {
  const from = s.in_from_project_id ? `(${s.in_from_project_id}에서${s.exchange ? ", 맞바꾸기" : ""})` : "";
  return `${s.project_id}: ${nameOf(s.out_person_id)} → ${nameOf(s.in_person_id)}${from}`;
}

export function CurrentWhatif({ active, datasetVersion, meta, settings, onDatasetChanged, onReviewDoc }: {
  /** 이 화면이 보이는가(보일 때 작업 데이터를 읽는다) */
  active: boolean;
  datasetVersion: string | null;
  meta: Meta | null;
  settings: PlacementSettings | null;
  onDatasetChanged: () => void;
  onReviewDoc?: (version: string, projectId: string | null) => void;
}) {
  const [ws, setWs] = useState<Workspace | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [stack, setStack] = useState<Staged[]>([]);
  const [result, setResult] = useState<WhatifResponse | null>(null);
  const [lastSwap, setLastSwap] = useState<Swap | null>(null);
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState<SwapPick | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmedVersion, setConfirmedVersion] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 데이터 판이 바뀌면(확정·다른 화면의 변경) 지금 배치를 다시 읽고 담은 변경은 버린다 -- 옛 판 위의 변경이다
  useEffect(() => {
    if (!active || !datasetVersion || ws?.dataset_version === datasetVersion) return;
    let live = true;
    setLoadErr(null);
    fetchWorkspace().then((w) => {
      if (!live) return;
      setWs(w);
      setStack([]);
      setResult(null);
      setLastSwap(null);
    }).catch((e) => live && setLoadErr((e as Error).message));
    return () => { live = false; };
  }, [active, datasetVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  const base = useMemo(() => (ws ? currentEntries(ws) : []), [ws]);
  const working = stack.at(-1)?.entries ?? base;
  const nameOf = (id: string) => meta?.people.find((p) => p.id === id)?.name ?? id;
  const total = stack.reduce((s, x) => s + x.delta, 0);

  function stale(e: unknown): boolean {
    if (e instanceof DatasetChangedError) { onDatasetChanged(); return true; }
    return false;
  }

  async function review(swap: Swap) {
    if (!ws) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setLastSwap(swap);
    setResult(null);
    try {
      setResult(await postWhatif(working, swap, {}, settings, ws.dataset_version, { gradeSwap: true }));
    } catch (e) {
      if (!stale(e)) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function stage() {
    if (!ws || !lastSwap || !result) return;
    setBusy(true);
    try {
      const res = await applySwap(working, lastSwap, {}, settings, ws.dataset_version, { gradeSwap: true });
      setStack((s) => [...s, { entries: res.entries, swap: lastSwap, delta: res.objective_delta, feasible: res.feasible, warnings: res.warnings,
                               swaps: swapLines(res.grade_swaps) }]);
      setResult(null);
      setLastSwap(null);
    } catch (e) {
      if (!stale(e)) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!active && !ws) return null;
  if (loadErr) return <Notice tone="bad">{loadErr}</Notice>;
  if (!ws || !meta) return <p className="text-sm text-slate-500"><Spinner label="지금 배치를 불러오는 중…" /></p>;
  if (!ws.editable || base.length === 0) {
    return (
      <Panel title="What-if">
        <EmptyState title="지금 배치가 없습니다"
                    body={ws.reason ?? "지금 배치(진행 사업에 누가 있는지)가 있어야 바꿔 볼 수 있습니다. 연간 계획에서 안을 확정하거나, 편성 기록에서 운영 중 데이터를 여세요."} />
      </Panel>
    );
  }
  const warnings = stack.flatMap((s) => s.warnings);
  return (
    <div className="space-y-5">
      {notice && <Notice>{notice}</Notice>}
      {confirmedVersion && onReviewDoc && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-pine-200 bg-pine-50 px-3 py-2 text-[13px] text-pine-800">
          확정한 변경의 인사팀 검토 자료(새로 들어간 사업마다 · 옮겨 온 사람의 원래 사업 영향)를 만듭니다.
          <button type="button" className="btn-primary" onClick={() => onReviewDoc(confirmedVersion, null)}>검토 자료로</button>
        </div>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      <p className="text-[13px] text-slate-600">
        지금 실제 배치(작업 데이터)에서 사람을 바꿔 봅니다. 바꿔 본 결과는 <b>담기</b> 전까지 아무것도 바꾸지 않고, 담은 변경도 <b>확정</b>해야 데이터가 바뀝니다.
        계산한 모범 안(A~D) 위에서 바꿔 보려면 연간 계획 ② 편성안 비교로 갑니다.
      </p>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <SwapControl key={`${ws.dataset_version}-${stack.length}`} people={meta.people} entries={working}
                       onSwap={(s) => void review(s)} busy={busy}
                       onSelectionChange={() => { setResult(null); setLastSwap(null); }}
                       projects={meta.projects} horizonStart={meta.horizon_start} onPick={setPick} />
          <BriefingPanel result={result} loading={busy && !!lastSwap && !result} nameOf={nameOf} />
          {result && lastSwap && swapLines(result.grade_swaps).length > 0 && (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-[13px] text-amber-900">
              등급 대체(지금 배치 대비): {swapLines(result.grade_swaps).join(", ")} — 빈자리가 아니라 대체로 셉니다. 확정할 때 사유를 적고 그 사업 정원이 바뀝니다.
            </p>
          )}
          {result && lastSwap && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-[13px]">
              <span className="text-slate-600">이 변경을 담아 두고 다른 변경을 이어서 볼 수 있습니다.</span>
              <button type="button" className="btn-primary" disabled={busy} onClick={() => void stage()}>
                이 변경 담기{result.feasible ? "" : "(경고 있음)"}</button>
            </div>
          )}
        </div>
        <TeamRelations people={meta.people} projects={meta.projects} coworks={meta.coworks} entries={working} pick={pick} />
      </div>

      <Panel title={`담은 변경 ${stack.length}건`} sub={stack.length ? `지금 배치 대비 계산상 변화 ${signedPts(total)}(같은 평가 기준, 참고값)` : "아직 담은 변경이 없습니다"}
             actions={stack.length ? (
               <div className="flex gap-2">
                 {/* 명단이 바뀌면 화면의 검토 결과는 옛 명단 기준이다 -- 함께 버린다(그대로 두면 "담기"가 다른 명단에 적용됐다) */}
                 <button className="btn" disabled={busy} onClick={() => { setStack((s) => s.slice(0, -1)); setResult(null); setLastSwap(null); }}>마지막 변경 빼기</button>
                 <button className="btn-ghost" disabled={busy} onClick={() => { setStack([]); setResult(null); setLastSwap(null); }}>모두 지우기</button>
                 <button className="btn-primary" disabled={busy} onClick={() => setConfirmOpen(true)}>{stack.length}건 확정</button>
               </div>
             ) : undefined}>
        {stack.length > 0 && (
          <div className="space-y-1 px-5 py-3 text-[13px]">
            <ol className="list-decimal space-y-0.5 pl-5" aria-label="담은 변경">
              {stack.map((s, i) => (
                <li key={i}>{swapLine(s.swap, nameOf)} <span className="num text-slate-500">{signedPts(s.delta)}</span>
                  {!s.feasible && <span className="ml-1 text-amber-700">경고</span>}</li>
              ))}
            </ol>
            {(stack.at(-1)?.swaps.length ?? 0) > 0 && (
              <p className="text-amber-800">확정하면 등급 대체(사유 필요): {stack.at(-1)!.swaps.join(", ")}</p>
            )}
            {warnings.length > 0 && (
              <p className="text-amber-800">경고: {[...new Set(warnings)].slice(0, 4).join(" · ")} — 확정 창에서 규칙 위반이면 막히고, 등급이 바뀐 자리는 사유를 받습니다.</p>
            )}
          </div>
        )}
      </Panel>

      <Panel title="바꾼 뒤 배치 명단" sub={stack.length ? "새로 들어온 사람·옮긴 사람은 표시가 붙습니다" : "지금 배치 그대로입니다"}>
        <div className="px-5 py-4">
          <AssignmentTable entries={working} people={meta.people} projects={meta.projects} horizonStart={meta.horizon_start}
                           baseline={stack.length ? base : undefined} />
        </div>
      </Panel>

      {confirmOpen && (
        <ConfirmChanges datasetVersion={ws.dataset_version} entries={working} source="manual" projects={meta.projects}
                        title={`What-if 변경 ${stack.length}건 확정`}
                        lead="담은 변경을 지금 배치에 적용한 새 판을 만듭니다. 새로 사람이 들어간 사업마다 인사팀 검토 자료를 만들 수 있습니다."
                        note={`What-if 변경 ${stack.length}건: ${stack.map((s) => swapLine(s.swap, nameOf)).join(" / ")}`.slice(0, 300)}
                        onClose={() => setConfirmOpen(false)}
                        onStale={() => { setConfirmOpen(false); onDatasetChanged(); }}
                        onDone={(msg, version) => {
                          setConfirmOpen(false);
                          setNotice(msg);
                          setConfirmedVersion(version);
                          onDatasetChanged();
                        }} />
      )}
    </div>
  );
}
