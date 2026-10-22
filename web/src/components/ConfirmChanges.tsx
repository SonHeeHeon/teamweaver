/** 확정 창(2026-10-21 -- 연간 계획 "이 안으로 확정"과 What-if 확정이 함께 쓴다). 서버 미리 계산(`/api/workspace/preview`)으로
 *  지금 배치 대비 옮김·새로·빠짐, 등급 대체(사유), 예산 증액(대체로 넘는 몫 + 새로 넘친 몫의 사유, 2026-10-22), 새 위반, 고정 충돌, 빼 온 자리(메움·메우지 않음)를 보이고,
 *  막을 것이 없을 때만 확정한다. 확정 요청은 미리 계산의 대체·증액을 그대로 보낸다 -- 서버 확정이 같은 함수로 다시 맞춰 본다. */
import { useEffect, useMemo, useState } from "react";
import type { AssignEntry, Project } from "../api/types";
import { Dialog, Spinner } from "../ui";
import { BudgetRaiseBox, GradeSwapBox, raiseBody, raisesReady, swapBody, swapList, swapsReady, type RaiseReasons,
         type SwapReasons } from "../scenarios/GradeSwapBox";
import { SeatHoleBox, holesReady, waiverBody, type WaiverReasons } from "../scenarios/SeatHoleBox";
import { isDatasetChanged, postApply, postPreview, withPersistNote, type ConfirmPreview, type PreviewMove } from "../scenarios/workspaceApi";

function moveLine(m: PreviewMove): string {
  const from = m.from.join("·") || "대기";
  const to = m.to.join("·") || "대기";
  return `${m.name}: ${from} → ${to}`;
}

export function ConfirmChanges({ datasetVersion, entries, source, title, lead, note, projects, keepLocked, onDone, onClose, onStale }: {
  datasetVersion: string;
  entries: AssignEntry[];
  source: "plan" | "manual" | "auto";
  /** 연간 계획 안의 고정 선택(2026-10-21): false = "고정 풀고 다시 배치"로 짰다(확정하면 빠지는 고정을 함께 푼다 -- 미리 체크),
   *  true = 고정을 지켜 짰다, undefined = 묻지 않았다(편성 기록 등 -- 확정 창에서 풀지 정한다) */
  keepLocked?: boolean;
  title: string;
  /** 창 첫 문장 */
  lead: string;
  /** 변경 이력에 남길 말 */
  note: string;
  projects?: Project[];
  /** 확정 끝: 완료 문장과 새 판 버전(검토 자료 열기용) */
  onDone: (message: string, version: string) => void;
  onClose: () => void;
  /** 판이 바뀌어 거절됐다 */
  onStale: () => void;
}) {
  const [pv, setPv] = useState<ConfirmPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reasons, setReasons] = useState<SwapReasons>({});
  /** 예산 증액 사유(2026-10-22 P3-2) -- 열쇠 = 사업 */
  const [raiseReasons, setRaiseReasons] = useState<RaiseReasons>({});
  /** 빼 온 자리를 메울 사람(화면에서 고름) · 메우지 않음 사유 */
  const [fills, setFills] = useState<AssignEntry[]>([]);
  const [waivers, setWaivers] = useState<WaiverReasons>({});
  const [release, setRelease] = useState(keepLocked === false);
  const named = (id: string) => { const n = projects?.find((p) => p.id === id)?.name; return n ? `${id} ${n}` : id; };
  const all = useMemo(() => [...entries, ...fills], [entries, fills]);

  useEffect(() => {
    const ctl = new AbortController();
    setPv(null);
    postPreview(datasetVersion, all, ctl.signal).then(setPv).catch((e) => {
      if (ctl.signal.aborted) return;
      if (isDatasetChanged(e)) { onStale(); return; }
      setError((e as Error).message);
    });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetVersion, all]);

  const swaps = swapList(pv?.grade_swaps);
  const budget = Object.values(pv?.budget_add ?? {}).reduce((a, b) => a + b, 0);
  /** 연간 계획 안의 고정 충돌은 "고정을 풀고 확정"으로 넘길 수 있다(다른 확정은 예전처럼 막는다) */
  const lockOk = !!pv && (pv.locked_dropped.length === 0 || (source === "plan" && release));
  const blocked = !!pv && (pv.new_violations.length > 0 || !lockOk || !pv.changed);
  const ready = !!pv && !blocked && swapsReady(swaps, reasons) && raisesReady(pv.budget_raises, raiseReasons) && holesReady(pv.holes, waivers);

  async function confirm() {
    if (!pv || !ready) return;
    setBusy(true);
    setError(null);
    try {
      const seatWaivers = waiverBody(pv.holes, waivers);
      const res = await postApply(datasetVersion, {
        entries: all, source, note,
        ...(swaps.length ? { grade_swaps: swapBody(swaps, reasons) } : {}),
        ...(budget > 0 ? { budget_add: pv.budget_add } : {}),
        ...(seatWaivers.length ? { seat_waivers: seatWaivers } : {}),
        ...(Object.keys(pv.budget_raises ?? {}).length ? { budget_raises: raiseBody(pv.budget_raises, raiseReasons) } : {}),
        ...(source === "plan" && pv.locked_dropped.length && release ? { release_locks: true } : {}),
      });
      const n = pv.moved.length + pv.joined.length + pv.left.length;
      onDone(withPersistNote(`확정했습니다 — ${n}명의 배치가 바뀐 새 판입니다(변경 이력에서 되돌릴 수 있음).`, res), res.workspace.dataset_version);
    } catch (e) {
      if (isDatasetChanged(e)) { onStale(); return; }
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog wide title={title} actions={<>
      <button className="btn-ghost" onClick={onClose} disabled={busy}>취소</button>
      <button className="btn-primary" disabled={busy || !ready} onClick={() => void confirm()}
              title={!pv ? "미리 계산 중입니다" : blocked ? "확정할 수 없는 이유가 위에 있습니다"
                : !ready ? "등급 대체·예산 증액 사유와 빼 온 자리 결정을 마쳐야 확정할 수 있습니다" : undefined}>
        {busy ? <Spinner status={false} label="확정하는 중…" /> : "확정"}
      </button>
    </>}>
      <p>{lead}</p>
      {!pv && !error && <p className="mt-2"><Spinner label="지금 배치와 비교하는 중…" /></p>}
      {pv && (
        <div className="mt-2 space-y-2">
          <p className="font-medium text-slate-800">
            지금 배치 대비: 옮김 {pv.moved.length}명 · 새로 {pv.joined.length}명 · 빠짐 {pv.left.length}명
          </p>
          {pv.changed && (
            <details>
              <summary className="cursor-pointer text-xs text-slate-500">누가 어디로(펼치기)</summary>
              <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto text-xs text-slate-600" aria-label="바뀌는 사람">
                {[...pv.moved, ...pv.joined, ...pv.left].map((m) => <li key={m.person_id}>{moveLine(m)}</li>)}
              </ul>
            </details>
          )}
          {!pv.changed && <p className="text-amber-800">지금 배치와 같습니다 — 확정할 것이 없습니다.</p>}
          {pv.locked_dropped.length > 0 && (source === "plan" ? (
            <section aria-label="고정 풀기" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-[13px] text-amber-950">
              <p>
                {keepLocked === false ? "이 안은 고정을 풀고 다시 배치한 안입니다" : "이 안은 고정한 배치를 옮깁니다"} — 고정 {pv.locked_dropped.length}건:
                {" "}{pv.locked_dropped.join(", ")}. 확정하면 이 고정이 함께 풀리고 확정 기록에 남습니다.
              </p>
              <label className="mt-1 inline-flex items-center gap-1 text-xs">
                <input type="checkbox" checked={release} disabled={busy} onChange={(e) => setRelease(e.target.checked)} />
                고정을 풀고 확정합니다
              </label>
              {!release && <p className="mt-0.5 text-xs text-slate-600">고정을 지키려면 연간 계획을 "고정 유지하고 짜기"로 다시 계산하세요.</p>}
            </section>
          ) : (
            <p role="alert" className="text-red-700">고정한 배치를 빼는 안이라 확정할 수 없습니다: {pv.locked_dropped.join(", ")} — 신규 사업 편성의 "진행 사업 — 고정"에서 풀거나 다른 안을 고르세요.</p>
          ))}
          {pv.new_violations.length > 0 && (
            <div role="alert" className="text-red-700">
              확정하면 규칙 위반이 생겨 확정할 수 없습니다:
              <ul className="ml-4 list-disc text-xs">{pv.new_violations.slice(0, 8).map((v) => <li key={v}>{v}</li>)}</ul>
            </div>
          )}
          <GradeSwapBox items={swaps} budgetAdd={budget} reasons={reasons} named={named} disabled={busy}
                        onReason={(k, v) => setReasons((r) => ({ ...r, [k]: v }))} />
          <BudgetRaiseBox raises={pv.budget_raises} reasons={raiseReasons} named={named} disabled={busy}
                          onReason={(j, v) => setRaiseReasons((r) => ({ ...r, [j]: v }))} />
          <SeatHoleBox holes={pv.holes} fillOptions={pv.fill_options} named={named} disabled={busy}
                       // 전부 다시 짠 안은 거의 모든 사업에서 사람이 바뀐다 -- 메운 사람 줄은 화면에서 고른 것만
                       fills={source === "plan" ? (pv.fills ?? []).filter((f) => fills.some((x) => x.person_id === f.person_id)) : pv.fills}
                       waivers={waivers} removable={new Set(fills.map((f) => f.person_id))}
                       onWaive={(k, v) => setWaivers((w) => { const n = { ...w }; if (v === null) delete n[k]; else n[k] = v; return n; })}
                       onFill={(f) => setFills((xs) => [...xs.filter((x) => x.person_id !== f.person_id), f])}
                       onUnfill={(pid) => setFills((xs) => xs.filter((x) => x.person_id !== pid))} />
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}
    </Dialog>
  );
}
