import { useCallback, useEffect, useState } from "react";
import { AdminLoginRequiredError } from "../api/client";
import { ReviewDoc, confirmLabel } from "./ReviewDoc";
import { ChangeReportPanel } from "./ChangeReportPanel";
import { Spinner } from "../ui";
import {
  ReviewConflictError, createReviewDoc, downloadReviewPdf, draftReviewDoc, fetchConfirms, fetchReviewDoc, saveReviewDoc,
  type ConfirmRow, type EditPatch, type ReviewDocView,
} from "./reviewApi";

/** "검토 자료" 메뉴(2026-10-19, claude-a): 확정(신규 사업 편성·긴급 투입)마다 사업별 인사팀 검토 자료를 만들고 고쳐 PDF로 낸다.
 *  신규 사업 편성·긴급 투입 화면(claude-b)은 확정 뒤 `open`으로 이 화면의 한 자료를 바로 연다. */
export function ReviewDocsPanel({ adminToken = null, onLoginRequired, open = null, onOpened, initialView = "docs" }: {
  adminToken?: string | null;
  onLoginRequired?: (reason: string) => void;
  /** 바로 열 자료(확정 직후 화면이 넘긴다) */
  open?: { version: string; projectId: string } | null;
  /** open을 한 번 쓴 뒤 부른다 -- App이 비워야 메뉴로 다시 들어올 때 옛 자료가 또 열리지 않는다(리뷰 P2) */
  onOpened?: () => void;
  /** 처음 보일 탭: 사업별 검토 자료(docs) · 변경 보고서(changes, 2026-10-22) */
  initialView?: "docs" | "changes";
}) {
  const [view, setView] = useState<"docs" | "changes">(initialView);
  useEffect(() => { setView(initialView); }, [initialView]);
  const [rows, setRows] = useState<ConfirmRow[] | null>(null);
  const [doc, setDoc] = useState<ReviewDocView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fail = useCallback((e: unknown) => {
    if (e instanceof AdminLoginRequiredError) { onLoginRequired?.(e.message); return; }
    setError(e instanceof Error ? e.message : String(e));
  }, [onLoginRequired]);
  const reload = useCallback(() => { fetchConfirms().then(setRows).catch(fail); }, [fail]);
  useEffect(() => { reload(); }, [reload]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  /** 여는 중인 자료(그 버튼에 "여는 중…" -- 새로 만들 때는 확정 전후를 평가해 몇 초 걸린다) */
  const [opening, setOpening] = useState<string | null>(null);
  const openDoc = (version: string, projectId: string, exists: boolean) => {
    setOpening(`${version}/${projectId}`);
    return run(async () => {
      setDoc(exists ? await fetchReviewDoc(version, projectId) : await createReviewDoc(version, projectId, adminToken));
      reload();
    }).finally(() => setOpening(null));
  };
  useEffect(() => {
    if (open) {
      void openDoc(open.version, open.projectId, false);   // 만들기는 이미 있으면 그대로 돌려준다
      onOpened?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open?.version, open?.projectId]);

  if (doc) {
    const v = doc.confirm.version, p = doc.project_id;
    return (
      <div className="max-w-[880px]">
        <button type="button" className="btn-ghost mb-3 text-[13px]" onClick={() => { setDoc(null); reload(); }}>← 검토 자료 목록</button>
        {error && <p role="alert" className="mb-3 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
        {notice && <p className="mb-3 rounded-md bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{notice}</p>}
        <div className="panel p-5">
          <ReviewDoc doc={doc} editable busy={busy}
                     onSave={(patch: EditPatch) => run(async () => {
                       try {
                         setDoc(await saveReviewDoc(v, p, patch, adminToken));
                         setNotice("저장했습니다.");
                       } catch (e) {
                         if (e instanceof ReviewConflictError) {
                           setDoc(await fetchReviewDoc(v, p));
                           throw new Error("다른 곳에서 먼저 고쳐 최신 자료를 다시 불러왔습니다. 고친 내용을 다시 넣어 주세요.");
                         }
                         throw e;
                       }
                     })}
                     onDraft={(force) => run(async () => {
                       if (force && !window.confirm("AI 초안을 다시 만듭니다(AI 비용). 직접 고친 본문은 그대로 둡니다.")) return;
                       setDoc(await draftReviewDoc(v, p, force, adminToken));
                     })}
                     onPdf={() => run(async () => {
                       await downloadReviewPdf(v, p, adminToken);
                       setDoc(await fetchReviewDoc(v, p));
                       setNotice("PDF를 내려받았습니다. 전자결재에 첨부해 올립니다.");
                     })} />
        </div>
      </div>
    );
  }

  const tabs = (
    <div className="mt-3 flex gap-1 border-b border-line text-[13.5px]" role="tablist" aria-label="검토 자료 보기">
      {([["docs", "사업별 검토 자료"], ["changes", "변경 보고서"]] as const).map(([k, label]) => (
        <button key={k} type="button" role="tab" aria-selected={view === k}
                className={`-mb-px border-b-2 px-3 py-1.5 ${view === k ? "border-pine-700 font-semibold text-ink" : "border-transparent text-slate-500"}`}
                onClick={() => setView(k)}>{label}</button>
      ))}
    </div>
  );
  if (view === "changes") {
    return (
      <section className="panel max-w-[880px] p-5" aria-labelledby="review-docs-title">
        <h2 id="review-docs-title" className="text-base font-semibold text-slate-900">인사팀 검토 자료</h2>
        {tabs}
        <div className="mt-3"><ChangeReportPanel adminToken={adminToken} onLoginRequired={onLoginRequired} /></div>
      </section>
    );
  }

  return (
    <section className="panel max-w-[880px] p-5" aria-labelledby="review-docs-title">
      <h2 id="review-docs-title" className="text-base font-semibold text-slate-900">인사팀 검토 자료</h2>
      {tabs}
      <p className="mt-3 text-[13px] text-slate-500">
        신규 사업 편성·긴급 투입·What-if를 확정하면 사람이 새로 들어간 사업마다 자료 한 편을 만듭니다. 시스템 사실은 잠겨 있고,
        부서장은 본문을 고치고 사실마다 해명·이의, 옮겨 온 사람마다 판단과 원래 사업 합의를 적습니다. 기간 보고는 "변경 보고서" 탭에서 묶습니다.
      </p>
      {error && <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
      {rows === null ? <p className="mt-4 text-sm text-slate-500">불러오는 중…</p> : rows.length === 0 ? (
        <p className="mt-4 rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-[13.5px] text-slate-500">
          아직 확정한 편성이 없습니다. 신규 사업 편성이나 긴급 투입에서 확정하면 여기에 나타납니다.
        </p>
      ) : (
        <ul className="mt-4 space-y-3">
          {rows.map((r) => (
            <li key={r.version} className="rounded-lg border border-line px-4 py-3">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <b>{confirmLabel(r)}</b>
                <span className="text-slate-500">{r.at ? new Date(r.at).toLocaleString("ko-KR") : ""} · {r.note}</span>
                {r.active && <span className="rounded-full bg-pine-100 px-2 text-[11px] text-pine-800">지금 판</span>}
                {r.reverted && <span className="rounded-full bg-red-100 px-2 text-[11px] text-red-700">{r.cancelled ? "확정 취소됨" : "되돌려짐"}</span>}
              </div>
              <ul className="mt-2 space-y-1.5">
                {r.projects.map((p) => (
                  <li key={p.project_id} className="flex flex-wrap items-center justify-between gap-2 text-[13.5px]">
                    <span>
                      {p.name} <span className="text-slate-400">({p.project_id})</span>
                      {p.exists && <span className="ml-2 text-[12px] text-slate-500">
                        {p.status === "exported" ? "내보냄" : "작성 중"} · 새로 들어온 사람 {p.joined ?? p.movers}명(옮겨 옴 {p.movers}명){p.objections ? ` · 이의 ${p.objections}건` : ""}
                      </span>}
                    </span>
                    <button type="button" className={p.exists ? "btn" : "btn-primary"} disabled={busy || (!p.exists && !r.active)}
                            title={!p.exists && !r.active ? "그 확정이 지금 판일 때만 새로 만들 수 있습니다" : undefined}
                            onClick={() => void openDoc(r.version, p.project_id, p.exists)}>
                      {opening === `${r.version}/${p.project_id}` ? <Spinner status={false} label={p.exists ? "여는 중…" : "만드는 중…"} />
                        : p.exists ? "열기" : "자료 만들기"}
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
