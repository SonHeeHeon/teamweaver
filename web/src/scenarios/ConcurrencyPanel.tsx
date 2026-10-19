/** 빈자리 → 프로젝트 동시 수행 허용 후보(2026-10-19, claude-a -- 사용자 결정 "동시 수행은 사람 단위로, 사유와 함께").
 *  전일 투입 결과에 빈자리가 남으면 "이 사람을 동시 수행으로 허용하면 그 자리를 채울 수 있다"를 보여 준다(다시 풀지 않는다).
 *  허용하면 작업 데이터에 사유와 함께 적히고(새 판, 되돌리기 가능) 화면이 다시 계산한다. */
import { useEffect, useState } from "react";
import type { AssignEntry } from "../api/types";
import {
  CONCURRENCY_REASONS, type ConcurrencyReason, fetchConcurrencyCandidates, isDatasetChanged, postConcurrency, type ShortfallHint,
  withPersistNote,
} from "./workspaceApi";

interface Props {
  datasetVersion: string;
  entries: AssignEntry[];
  projectName: (id: string) => string;
  weights?: Record<string, number>;
  /** 허용이 새 판을 만들었다 -- 화면이 새 데이터로 다시 읽고 다시 계산한다. */
  onGranted: (message: string) => void;
  /** 다른 곳에서 데이터가 바뀌어 거절됐다(409) -- 화면이 새 판을 다시 읽는다. */
  onStale?: () => void;
}

export function ConcurrencyPanel({ datasetVersion, entries, projectName, weights, onGranted, onStale }: Props) {
  const [rows, setRows] = useState<ShortfallHint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);              // "사업:사람"
  const [reason, setReason] = useState<ConcurrencyReason>("인력 부족");
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const key = JSON.stringify(entries.map((e) => [e.person_id, e.project_id]));

  useEffect(() => {
    let live = true;
    setRows(null);
    setError(null);
    fetchConcurrencyCandidates(datasetVersion, entries, weights)
      .then((r) => { if (live) setRows(r.shortfalls); })
      .catch((e) => { if (live) { setError((e as Error).message); if (isDatasetChanged(e)) onStale?.(); } });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- entries는 key로 비교한다
  }, [datasetVersion, key]);

  async function grant(personId: string, name: string, maxProjects: 2 | 3) {
    setBusy(true);
    setError(null);
    try {
      // 이미 허용된(2곳) 사람이 지금 두 사업에 있으면 상한을 3으로 올린다 -- 2로 다시 걸면 바뀌는 것이 없다(폴백 리뷰 P2)
      const res = await postConcurrency(datasetVersion, personId, true, reason, memo, maxProjects);
      setOpen(null);
      setMemo("");
      onGranted(withPersistNote(`${name}에게 프로젝트 동시 수행을 허용했다(${reason}). 새 판이 됐으니 다시 계산하면 반영된다.`, res));
    } catch (e) {
      setError((e as Error).message);
      if (isDatasetChanged(e)) onStale?.();
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p role="alert" className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">{error}</p>;
  if (rows === null) return <p className="text-xs text-slate-500">빈자리를 채울 사람을 찾는 중…</p>;
  if (rows.length === 0) return null;
  return (
    <section aria-label="빈자리 동시 수행 후보" className="rounded-lg border border-violet-200 bg-violet-50/40 p-3 text-sm">
      <h3 className="font-medium text-violet-900">빈자리 — 프로젝트 동시 수행으로 채울 수 있는 사람</h3>
      <p className="mt-1 text-xs text-slate-600">
        전일 투입에서는 한 사람이 한 번에 한 사업만 맡습니다. 사람이 부족하거나 잠깐 봐줘야 하는 사정이 있으면, 부서장이 사유와 함께
        특정 사람에게 <b>프로젝트 동시 수행</b>을 허용할 수 있습니다. 허용된 사람은 두 사업 모두 100%로 일하며, 예산도 두 사업에서
        각각 전액이 나갑니다. 허용한 뒤 다시 계산하면 반영됩니다.
      </p>
      <ul className="mt-2 space-y-2">
        {rows.map((r) => (
          <li key={`${r.project_id}-${r.grade}`} className="rounded-md bg-white px-3 py-2">
            <p className="text-slate-900">{projectName(r.project_id)} · {r.grade} <b>{r.missing}석</b> 빈자리
              <span className="ml-2 text-xs text-slate-500">남은 월 예산 {r.budget_left.toLocaleString()}</span></p>
            {r.free_eligible > 0 && (
              <p className="text-xs text-slate-500">비어 있는 같은 등급 {r.free_eligible}명이 있다 — 시간을 늘려 다시 계산하면 채워질 수 있다.</p>
            )}
            {r.candidates.length === 0 ? (
              <p className="text-xs text-slate-500">동시 수행으로 채울 수 있는 사람이 없다(같은 등급·그 기간 일할 수 있음·예산 안).</p>
            ) : (
              <ul className="mt-1 space-y-1">
                {r.candidates.map((c) => {
                  const k = `${r.project_id}:${c.person_id}`;
                  return (
                    <li key={c.person_id} className="text-xs text-slate-700">
                      <span className="font-medium text-slate-900">{c.name}</span>
                      <span className="ml-1">({c.grade}) · 지금 {c.busy_with.map(projectName).join(", ")} · 적합도 {c.skill_fit.toFixed(2)}
                        · 월 {c.monthly_cost.toLocaleString()}</span>
                      {c.already_allowed && !c.needs_more ? <span className="ml-1 text-violet-700">이미 허용됨</span> : (
                        <button type="button" className="ml-2 rounded border border-violet-300 px-1.5 py-0.5 text-violet-800"
                                onClick={() => setOpen(open === k ? null : k)} aria-expanded={open === k}
                                aria-label={`${c.name} ${c.already_allowed ? "동시 수행 상한 3으로 올리기" : "동시 수행 허용"}`}>
                          {c.already_allowed ? "상한 3으로 올리기…" : "동시 수행 허용…"}
                        </button>
                      )}
                      {open === k && (
                        <span className="mt-1 flex flex-wrap items-center gap-2">
                          <label>사유{" "}
                            <select value={reason} onChange={(e) => setReason(e.target.value as ConcurrencyReason)}
                                    className="rounded border border-slate-300 px-1 py-0.5">
                              {CONCURRENCY_REASONS.map((x) => <option key={x} value={x}>{x}</option>)}
                            </select>
                          </label>
                          <input aria-label="메모" placeholder="메모(선택)" maxLength={200} value={memo}
                                 onChange={(e) => setMemo(e.target.value)}
                                 className="w-48 rounded border border-slate-300 px-1 py-0.5" />
                          <button type="button" className="btn-primary px-2 py-0.5 text-xs" disabled={busy}
                                  onClick={() => void grant(c.person_id, c.name, c.already_allowed ? 3 : 2)}>
                            {busy ? "처리 중…" : "허용"}</button>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
