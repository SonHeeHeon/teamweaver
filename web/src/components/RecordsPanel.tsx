import { useCallback, useEffect, useRef, useState } from "react";
import { AdminLoginRequiredError, deleteRecord, fetchRecords, importRecord, openRecord, recordFileUrl } from "../api/client";
import type { OpenRecordResult, PlacementRecord } from "../api/types";

const KIND: Record<string, string> = { operating: "운영 중", planning: "연초 계획" };

function period(r: PlacementRecord): string {
  if (!r.horizon_start) return "—";
  const [y, m] = r.horizon_start.split("-").map(Number);
  if (!y || !m || !r.horizon_months) return r.horizon_start;
  const end = new Date(y, m - 1 + r.horizon_months - 1, 1);
  return `${r.horizon_start} ~ ${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, "0")}`;
}

/** 편성 기록(2026-10-18, 사용자 지시: 시연 데이터 고르기 대신 "과거에 편성한 걸 json으로 Export/Import해서 화면에서 보는" 기능).
 *  불러오기 = 서버가 검증해 목록에 넣는다(작업 데이터는 그대로). 열기 = 그 기록의 데이터·편성안이 작업 화면이 된다. */
export function RecordsPanel({ adminToken, onLoginRequired, onOpened, onExportCurrent, activeRecordId }: {
  adminToken: string | null;
  onLoginRequired?: () => void;
  onOpened?: (res: OpenRecordResult) => void | Promise<void>;
  onExportCurrent: (() => void) | null;
  activeRecordId: string | null;
}) {
  const [records, setRecords] = useState<PlacementRecord[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState<PlacementRecord | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    fetchRecords().then(setRecords).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  function fail(e: unknown) {
    if (e instanceof AdminLoginRequiredError) onLoginRequired?.();
    setError(e instanceof Error ? e.message : String(e));
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    setBusy("import");
    setError(null);
    setNotice(null);
    try {
      const { record, check } = await importRecord(file, adminToken);
      setNotice(check.score_changed.length
        ? `“${record.title}”을 불러왔습니다. 지금 계산 규칙으로 다시 채점하니 점수가 다른 안이 있습니다: ${
            check.score_changed.map((c) => `${c.label} ${c.recorded == null ? "기록 없음" : c.recorded.toFixed(2)} → ${c.current.toFixed(2)}`).join(", ")}.`
        : `“${record.title}”을 불러왔습니다. 서버가 다시 채점해 기록과 같음을 확인했습니다.`);
      refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function open(r: PlacementRecord) {
    setConfirmOpen(null);
    setBusy(r.id);
    setError(null);
    setNotice(null);
    try {
      const res = await openRecord(r.id, adminToken);
      refresh();
      await onOpened?.(res);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function remove(id: string) {
    setConfirmDelete(null);
    setBusy(id);
    setError(null);
    try {
      await deleteRecord(id, adminToken);
      refresh();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel" aria-labelledby="records-title">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 id="records-title" className="text-base font-semibold text-slate-900">편성 기록</h2>
          <p className="mt-0.5 text-[13px] text-slate-500">
            내보낸 편성(그때의 인력·사업 데이터 + 편성안 + 계산 설정)입니다. 열면 그때 화면 그대로 다시 보고, 교체 검토·PDF까지 할 수 있습니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn" onClick={() => onExportCurrent?.()} disabled={!onExportCurrent}
                  title={onExportCurrent ? undefined : "What-if 대시보드에 편성안이 있을 때 내보낼 수 있습니다"}>
            지금 작업 내보내기
          </button>
          <button className="btn-primary" onClick={() => fileInput.current?.click()} disabled={busy !== null}>
            {busy === "import" ? "확인 중…" : "기록 파일 불러오기"}
          </button>
          <input ref={fileInput} type="file" accept=".json,application/json" className="hidden"
                 aria-label="편성 기록 파일" onChange={(e) => void onFile(e.target.files?.[0])} />
        </div>
      </header>
      {(error || notice) && (
        <p role={error ? "alert" : "status"}
           className={`mx-5 mt-4 rounded-md px-3 py-2 text-[13px] ${error ? "bg-red-50 text-red-600" : "bg-indigo-50 text-indigo-900"}`}>
          {error ?? notice}
        </p>
      )}
      {records === null ? (
        <p className="px-5 py-4 text-sm text-slate-500">불러오는 중…</p>
      ) : records.length === 0 ? (
        <div className="px-5 py-6 text-sm text-slate-600">
          <p className="font-medium text-slate-800">아직 불러온 편성 기록이 없습니다.</p>
          <p className="mt-1 text-[13px] text-slate-500">
            "기록 파일 불러오기"로 예전에 내보낸 .json 파일을 넣으세요. 시연용 기록은 저장소의 <code>demo/records/</code>에 있습니다.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-slate-100 bg-[#FAFBFA] text-left text-slate-500">
                <th className="px-5 py-2 font-medium">제목</th><th className="px-3 py-2 font-medium">종류</th>
                <th className="px-3 py-2 text-right font-medium">규모</th><th className="px-3 py-2 font-medium">계획 기간</th>
                <th className="px-3 py-2 font-medium">내보낸 날</th><th className="px-5 py-2" />
              </tr>
            </thead>
            <tbody>
              {records.map((r) => {
                const isOpen = r.id === activeRecordId || r.open;
                return (
                  <tr key={r.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-5 py-2.5">
                      <b className="font-semibold text-slate-900">{r.title}</b>
                      {isOpen && <span className="ml-2 text-xs font-semibold text-ink">지금 열림</span>}
                      {r.score_changed.length > 0 && (
                        <span className="ml-2 text-xs text-amber-700" title="지금 계산 규칙으로 다시 채점하면 점수가 다른 안이 있다">
                          점수 달라짐 {r.score_changed.map((c) => c.label).join("·")}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={`rounded-full border px-2 py-px text-xs ${r.kind === "operating"
                        ? "border-pine-200 bg-[#F1F7F4] text-slate-800" : "border-line text-slate-500"}`}>
                        {KIND[r.kind] ?? r.kind}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right">{r.people ?? "?"}명 · {r.projects ?? "?"}건</td>
                    <td className="px-3 py-2.5">{period(r)}</td>
                    <td className="px-3 py-2.5">{r.exported_at ? r.exported_at.slice(0, 10) : "—"}</td>
                    <td className="whitespace-nowrap px-5 py-2 text-right">
                      <a className="btn-ghost py-1 text-xs" href={recordFileUrl(r.id)} download>내려받기</a>
                      {confirmDelete === r.id ? (
                        <>
                          <button className="btn-ghost py-1 text-xs text-red-600" onClick={() => void remove(r.id)}>삭제 확인</button>
                          <button className="btn-ghost py-1 text-xs" onClick={() => setConfirmDelete(null)}>취소</button>
                        </>
                      ) : (
                        <button className="btn-ghost py-1 text-xs" onClick={() => setConfirmDelete(r.id)}
                                disabled={busy !== null}>삭제</button>
                      )}
                      {/* 열려 있어도 다시 열 수 있다 -- 새로고침·재기동 뒤 화면에 편성안을 다시 띄우는 길(Codex 리뷰 P2) */}
                      <button className="btn ml-1 py-1" disabled={busy !== null}
                              onClick={() => (isOpen ? void open(r) : setConfirmOpen(r))}>
                        {busy === r.id ? "여는 중…" : isOpen ? "다시 열기" : "열기"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-[rgba(21,55,47,.32)] px-4" role="dialog"
             aria-modal="true" aria-labelledby="open-title">
          <div className="panel w-full max-w-[480px] p-6">
            <h3 id="open-title" className="text-lg font-bold text-slate-900">이 기록을 열까요?</h3>
            <p className="mt-1.5 text-[13.5px] text-slate-600">
              <b>{confirmOpen.title}</b>을 열면 지금 작업 데이터와 그 결과가 이 기록으로 바뀝니다(이 서버를 쓰는 모든 사용자에게 적용).
              지금 작업을 남겨 두려면 먼저 "지금 작업 내보내기"를 하세요.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setConfirmOpen(null)}>취소</button>
              <button className="btn-primary" autoFocus onClick={() => void open(confirmOpen)}>열기</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
