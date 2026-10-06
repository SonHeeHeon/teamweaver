import { useState } from "react";
import { AdminLoginRequiredError, rejudgeDataset, resetDataset, uploadDataset } from "../api/client";
import type { DatasetInfo, IngestIssue, UploadResult } from "../api/types";

/** 평가 사유(글)를 보내는 곳 -- 사내로 확인되지 않은 주소를 "사내"라고 단정하지 않는다. */
function placeLabel(location: string | null | undefined, host: string | null | undefined): string {
  if (location === "openai") return "외부(OpenAI)";
  if (location === "onprem") return `사내 LLM ${host ?? ""}`.trim();
  return `사내인지 확인되지 않은 주소 ${host ?? ""}`.trim();
}

const SOURCE_LABEL: Record<string, string> = {
  fixture: "기본 데이터", upload: "업로드", "demo-bundle": "시연 데이터(실제 형식)",
};

/** 계산에 쓰지 않는 선택 파일(claude-a 요청) -- 목록에서 무엇인지 알 수 있게. */
const OPTIONAL_FILES: Record<string, string> = {
  "project_outcomes.csv": "과거 성과(선택 · 계산에 쓰지 않음)",
  "replacements.csv": "교체 이력(선택 · 계산에 쓰지 않음)",
};

interface Props {
  active: DatasetInfo | null;
  /** 서버의 활성 데이터셋이 바뀌었다(업로드 전환·되돌리기). App이 meta와 결과를 새로 고친다. */
  onSwitched: (info: DatasetInfo) => void;
  /** 서버가 관리자 토큰을 요구하면 App이 넘겨준다(없으면 null). */
  adminToken?: string | null;
  /** 관리자 동작이 401을 받았다(K14) -- App이 로그인 화면으로 보낸다. */
  onLoginRequired?: () => void;
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

export function DatasetTab({ active, onSwitched, adminToken = null, onLoginRequired }: Props) {
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
      if (e instanceof AdminLoginRequiredError) onLoginRequired?.();
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
      if (e instanceof AdminLoginRequiredError) onLoginRequired?.();
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function rejudge() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const next = await rejudgeDataset(adminToken);
      // 다시 실패해 데이터가 그대로면(같은 버전) 계산 결과를 비우지 않고 이유만 알린다.
      if (active && next.version === active.version) setError(next.judge_error ?? "다시 판정했지만 결과가 같다.");
      else onSwitched(next);
    } catch (e) {
      if (e instanceof AdminLoginRequiredError) onLoginRequired?.();
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

      {active?.review_judge === "items" && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md border border-amber-400 bg-amber-50
                                     px-3 py-2 text-sm text-amber-800">
          <span className="flex-1">{active.judge_error ?? "LLM 판정에 실패해 평가 사유 대신 항목 점수를 썼다."}</span>
          <button onClick={rejudge} disabled={busy}
                  title="성공하면 데이터 버전이 바뀌어 이 데이터에 저장된 교체 기록은 지워진다"
                  className="rounded-md border border-amber-500 bg-white px-3 py-1 text-xs font-medium
                             hover:bg-amber-100 disabled:opacity-50">
            {busy ? "판정 중…" : "판정 다시 시도"}
          </button>
          <span className="basis-full text-xs">성공하면 판정이 바뀌어 이 데이터에 저장된 교체 기록은 지워진다.</span>
        </div>
      )}
      {active?.review_judge === "blocked" && (
        <p role="alert" className="rounded-md border border-amber-400 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {active.judge_error}
        </p>
      )}
      {active?.restore_error && (
        <p role="alert" className="rounded-md border border-amber-400 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {active.restore_error}
        </p>
      )}
      <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <p className="text-xs font-medium text-slate-500">지금 계산에 쓰는 데이터</p>
        {active ? (
          <p className="mt-1 text-slate-900">
            <span className="font-medium">{active.dataset_id}</span>
            {" · "}{SOURCE_LABEL[active.source] ?? active.source}
            {active.synthetic ? " · 가상 데이터" : active.synthetic === false ? " · 실데이터" : ""}
            {" · "}{active.people}명 · 프로젝트 {active.projects}건
            <span className="ml-2 font-mono text-xs text-slate-400">{active.version.slice(0, 12)}</span>
          </p>
        ) : null}
        {active ? (
          <p className="mt-1 text-xs text-slate-500">
            평가 사유(글) 판정:{" "}
            {active.review_judge === "fixture" ? "가상 데이터 생성 때 LLM이 매긴 값"
              : active.review_judge === "items" ? "LLM 실패 — 항목 점수로 대신함"
              : active.review_judge === "blocked" ? "외부 전송이 허용되지 않아 판정하지 않음 — 항목 점수로 대신함"
              : `LLM ${active.judge_model ?? ""} · ${placeLabel(active.judge_location, active.judge_host)}`}
          </p>
        ) : (
          <p className="mt-1 text-slate-500">불러오는 중…</p>
        )}
      </div>

      {active?.judge_endpoint && (
        <p className={`rounded-md border px-3 py-2 text-sm ${active.judge_endpoint.external
          ? "border-amber-400 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}>
          올린 묶음의 평가 사유(좋은점·나쁜점 글)는 LLM이 읽어 협업 점수에 반영한다. 보내는 곳:{" "}
          <b>{placeLabel(active.judge_endpoint.location, active.judge_endpoint.host)}</b>
          {" · "}모델 {active.judge_endpoint.model}. 처음 판정은 100명(평가 약 1,400건) 기준 2~4분 걸리고, 이후엔 저장된 판정을 쓴다.
          {active.judge_endpoint.external && (active.judge_endpoint.external_allowed
            ? <b> 이 서버는 실데이터의 평가 원문도 이곳으로 보내도록 허용돼 있다(TEAMWEAVER_REVIEW_ALLOW_EXTERNAL=1).</b>
            : " 실데이터(가상이 아닌 묶음)는 서버가 외부 전송을 허용(TEAMWEAVER_REVIEW_ALLOW_EXTERNAL=1)하지 않으면 보내지 않고 항목 점수를 쓴다. 사내 LLM은 서버의 TEAMWEAVER_REVIEW_BASE_URL로 지정한다.")}
        </p>
      )}
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
                      <tr key={f}><td className="pr-6 font-mono text-xs">{f}
                        {OPTIONAL_FILES[f] && <span className="ml-2 font-sans text-slate-500">{OPTIONAL_FILES[f]}</span>}</td>
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
