import { useState } from "react";
import { resetDataset, uploadDataset } from "../api/client";
import type { DatasetInfo, IngestIssue, UploadResult } from "../api/types";

interface Props {
  active: DatasetInfo | null;
  /** 서버의 활성 데이터셋이 바뀌었다(업로드 전환·되돌리기). App이 meta와 결과를 새로 고친다. */
  onSwitched: (info: DatasetInfo) => void;
  /** 서버가 관리자 토큰을 요구하면 App이 넘겨준다(없으면 null). */
  adminToken?: string | null;
}

function where(i: IngestIssue): string {
  return i.file + (i.row !== null ? ` ${i.row}행` : "") + (i.column ? ` [${i.column}]` : "");
}

function IssueList({ title, items, tone }: { title: string; items: IngestIssue[];
                                             tone: "error" | "warning" }) {
  if (items.length === 0) return null;
  const cls = tone === "error" ? "border-red-300 bg-red-50 text-red-800"
                               : "border-amber-300 bg-amber-50 text-amber-800";
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${cls}`}>
      <p className="font-medium">{title} {items.length}건</p>
      <ul className="mt-1 max-h-64 list-inside list-disc overflow-y-auto">
        {items.map((i, k) => (
          <li key={k}><span className="font-mono text-xs">{where(i)}</span> — {i.message}</li>
        ))}
      </ul>
    </div>
  );
}

export function DatasetTab({ active, onSwitched, adminToken = null }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await uploadDataset(file, adminToken);
      setResult(r);
      if (r.activated && r.dataset) onSwitched(r.dataset);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      onSwitched(await resetDataset(adminToken));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const report = result?.report ?? null;
  return (
    <section className="max-w-3xl space-y-4">
      <div>
        <h2 className="mb-1 text-lg font-semibold text-slate-900">데이터</h2>
        <p className="text-sm text-slate-500">
          인력·프로젝트 CSV 묶음(manifest.json 포함)을 zip 하나로 올린다. 검증에 오류가 하나라도
          있으면 전환하지 않는다. 전환은 이 서버를 쓰는 모든 사용자에게 적용된다. 올린 묶음은
          서버의 보호된 폴더에 하나만 보관되어 서버를 다시 켜도 유지되고, "기본 데이터로
          되돌리기"를 하면 보관본도 지운다.
        </p>
      </div>

      {active?.restore_error && (
        <p role="alert" className="rounded-md border border-amber-400 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          저장된 업로드 데이터를 복원하지 못해 기본 데이터로 시작했다: {active.restore_error}
        </p>
      )}
      <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <p className="text-xs font-medium text-slate-500">지금 계산에 쓰는 데이터</p>
        {active ? (
          <p className="mt-1 text-slate-900">
            <span className="font-medium">{active.dataset_id}</span>
            {" · "}{active.source === "fixture" ? "기본 데이터" : "업로드"}
            {active.synthetic ? " · 가상 데이터" : active.synthetic === false ? " · 실데이터" : ""}
            {" · "}{active.people}명 · 프로젝트 {active.projects}건
            <span className="ml-2 font-mono text-xs text-slate-400">{active.version.slice(0, 12)}</span>
          </p>
        ) : (
          <p className="mt-1 text-slate-500">불러오는 중…</p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm text-slate-700">
          <span className="sr-only">묶음 zip 파일</span>
          <input type="file" accept=".zip,application/zip" aria-label="묶음 zip 파일"
                 onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }}
                 className="text-sm" />
        </label>
        <button onClick={upload} disabled={!file || busy}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                           hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300">
          {busy ? "처리 중…" : "검증 후 전환"}
        </button>
        {active?.source === "upload" && (
          <button onClick={reset} disabled={busy}
                  className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm
                             font-medium text-slate-700 hover:bg-slate-50 disabled:text-slate-400">
            기본 데이터로 되돌리기
          </button>
        )}
      </div>

      {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {result && (
        <div className="space-y-3">
          <p role="status" className={`text-sm font-medium ${
            result.activated ? "text-emerald-700" : "text-red-700"}`}>
            {result.activated
              ? "검증을 통과해 이 데이터로 전환했다. 이전 계산 결과는 지웠다."
              : `전환하지 않았다 — ${result.detail ?? "검증 오류가 있다."}`}
          </p>
          {result.activated && result.persisted === false && (
            <p role="alert" className="text-sm text-amber-800">{result.persist_error}</p>
          )}
          {report && (
            <>
              <IssueList title="오류" items={report.errors} tone="error" />
              <IssueList title="경고" items={report.warnings} tone="warning" />
              {report.notes.length > 0 && (
                <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  <p className="font-medium">변환 노트</p>
                  <ul className="mt-1 list-inside list-disc">
                    {report.notes.map((n, k) => <li key={k}>{n}</li>)}
                  </ul>
                </div>
              )}
              {Object.keys(report.row_counts).length > 0 && (
                <table className="text-sm">
                  <thead className="text-left text-slate-500">
                    <tr><th className="pr-6 font-medium">파일</th><th className="font-medium">행 수</th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(report.row_counts).map(([f, n]) => (
                      <tr key={f}><td className="pr-6 font-mono text-xs">{f}</td>
                        <td className="tabular-nums">{n}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
