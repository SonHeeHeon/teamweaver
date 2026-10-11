import { useEffect, useMemo, useRef, useState } from "react";
import { DatasetChangedError, postJustification } from "../api/client";
import type { AssignEntry, Justification, JustFact, Meta, PlacementSettings, Step } from "../api/types";
import { KIND_LABEL, KIND_ORDER, fallbackText } from "./justification";

/** 인사팀 소명 글(claude-a 계약 docs/requests/2026-10-09-hr-justification.md).
 *  정해진 틀(템플릿)을 먼저 보이고 AI 글(GraphRAG, p50 약 8초)이 오면 바꾼다. 글 속 [F12]을 누르면 옆 사실 목록에서 강조한다.
 *  AI 원문(llm_text)은 서버가 보내지 않는다 -- 검사에서 떨어진 글은 틀린 주장이 있을 수 있다. */

/** 글을 근거 칩([F12])과 일반 글로 나눈다. */
function Prose({ text, onCite, active }: { text: string; onCite: (id: string) => void; active: string | null }) {
  const parts = text.split(/(\[F\d+\])/g);
  return (
    <p className="whitespace-pre-wrap text-sm leading-7 text-slate-800">
      {parts.map((part, i) => {
        const m = /^\[(F\d+)\]$/.exec(part);
        if (!m) return <span key={i}>{part}</span>;
        return (
          <button key={i} type="button" onClick={() => onCite(m[1])} aria-label={`근거 ${m[1]}`}
                  className={`mx-0.5 rounded px-1 text-[11px] font-medium align-baseline ${
                    active === m[1] ? "bg-indigo-600 text-white" : "bg-indigo-50 text-indigo-700 hover:bg-indigo-100"}`}>
            {m[1]}
          </button>
        );
      })}
    </p>
  );
}

interface Props {
  meta: Meta;
  datasetVersion: string;
  /** 화면에 보이는 명단(변경 적용 후). */
  entries: AssignEntry[];
  /** 변경이 있으면 원 명단과 적용 단계 -- 서버가 원 명단에서 다시 적용해 소명 대상 명단을 정한다(PDF와 같은 방식). */
  applied: { base: AssignEntry[]; steps: Step[] } | null;
  weights: Record<string, number>;
  params: PlacementSettings | null;
  /** 원 플랜 라벨·서버 서명 -- 서버가 계산한 배치에만 소명 글을 만든다. */
  planLabel: string;
  planToken: string | null;
  onDatasetChanged: () => void;
}

export function JustificationPanel({ meta, datasetVersion, entries, applied, weights, params, planLabel, planToken,
                                    onDatasetChanged }: Props) {
  const staffed = useMemo(() => meta.projects.filter((p) => entries.some((e) => e.project_id === p.id)),
                          [meta.projects, entries]);
  const [project, setProject] = useState<string>(staffed[0]?.id ?? "");
  const [result, setResult] = useState<Justification | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const gen = useRef(0);
  const factRefs = useRef(new Map<string, HTMLLIElement>());
  const chosen = staffed.some((p) => p.id === project) ? project : (staffed[0]?.id ?? "");
  const reqKey = JSON.stringify([datasetVersion, chosen, entries, applied, weights, params, planLabel, planToken]);

  async function load(ai: boolean) {
    if (!chosen || !planToken) return;
    const g = ++gen.current;
    const body = { dataset_version: datasetVersion, project_id: chosen, entries, base_entries: applied?.base ?? null,
                   applied_swaps: applied?.steps ?? [], weights, milp_params: params, ai,
                   plan_label: planLabel, plan_token: planToken };
    setError(null);
    if (ai) setAiBusy(true);
    try {
      const first = ai ? null : await postJustification(body);
      if (g !== gen.current) return;                          // 그사이 사업·명단이 바뀌었다 -- 버린다
      if (first) setResult(first);
      if (ai || first?.ai_available) {
        setAiBusy(true);
        const next = await postJustification({ ...body, ai: true });
        if (g !== gen.current) return;
        setResult(next);
      }
    } catch (e) {
      if (g !== gen.current) return;
      if (e instanceof DatasetChangedError) onDatasetChanged();
      else setError(String(e instanceof Error ? e.message : e));
    } finally {
      if (g === gen.current) setAiBusy(false);
    }
  }

  // 사업·명단·기준이 바뀌면 다시 만든다(템플릿 먼저, AI 글이 가능하면 이어서)
  useEffect(() => {
    setResult(null);
    setActive(null);
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reqKey]);

  function cite(id: string) {
    setActive(id);
    factRefs.current.get(id)?.scrollIntoView?.({ block: "nearest" });
  }

  if (staffed.length === 0) return null;
  if (!planToken) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500" aria-label="인사팀 소명">
        인사팀 소명 글은 서버가 계산한 배치(서명 있는 플랜)에만 만든다 — 이 플랜에는 서명이 없다.
      </section>
    );
  }
  const add = result?.addendum ?? null;
  const main = result && add && result.text.endsWith(add) ? result.text.slice(0, -add.length).trimEnd() : result?.text ?? "";
  const byKind = new Map<string, JustFact[]>();
  for (const f of result?.facts ?? []) byKind.set(f.kind, [...(byKind.get(f.kind) ?? []), f]);
  const kinds = [...KIND_ORDER.filter((k) => byKind.has(k)), ...[...byKind.keys()].filter((k) => !KIND_ORDER.includes(k))];
  const adverseCount = (result?.facts ?? []).filter((f) => f.adverse).length;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="인사팀 소명">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-slate-900">인사팀 소명 — 왜 이 팀인가</h3>
        <select aria-label="소명 사업" value={chosen} onChange={(e) => setProject(e.target.value)}
                className="rounded-md border border-slate-300 px-2 py-1 text-sm">
          {staffed.map((p) => <option key={p.id} value={p.id}>{p.name}({p.id})</option>)}
        </select>
        {result && (result.method === "graphrag"
          ? <span className="rounded bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700"
                  title="AI는 사실을 고르고 순서·묶음만 정했다. 숫자·충족/미달·사람-사실 짝은 서버가 데이터 그대로 채웠다.">
              AI 구성 · 사실은 데이터 그대로</span>
          : <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">정해진 틀</span>)}
        {aiBusy && <span className="text-xs text-slate-500">AI 글 쓰는 중…(약 8초)</span>}
        <button type="button" onClick={() => void load(true)} disabled={aiBusy || !result?.ai_available}
                title={result && !result.ai_available ? fallbackText(result.ai_unavailable_reason) ?? "" : "AI로 다시 쓴다"}
                className="ml-auto rounded-md border border-slate-300 px-3 py-1 text-xs text-slate-700 hover:bg-slate-50
                           disabled:text-slate-400">
          다시 쓰기
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
          {!result && (
            <button type="button" onClick={() => void load(false)} className="ml-2 text-xs underline">다시 불러오기</button>
          )}
        </p>
      )}
      {result?.method === "template" && fallbackText(result.fallback_reason) && (
        <p className="mt-2 text-xs text-amber-700">{fallbackText(result.fallback_reason)}</p>
      )}
      {!result && !error && <p className="mt-2 text-sm text-slate-500">불러오는 중…</p>}
      {result && (
        <div className="mt-3 grid gap-4 lg:grid-cols-[3fr_2fr]">
          <div>
            <Prose text={main} onCite={cite} active={active} />
            {add && (
              <details open={result.appended_adverse.length <= 5}
                       className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                <summary className="cursor-pointer text-xs font-medium text-amber-800">
                  서버가 덧붙임 — AI 글이 빠뜨린 불리한 사실 {result.appended_adverse.length}개(AI가 쓴 문장이 아니다)
                </summary>
                <div className="mt-1"><Prose text={add} onCite={cite} active={active} /></div>
              </details>
            )}
            <p className="mt-2 text-[11px] text-slate-400">
              계산상 근거(같은 평가 기준) — 실제 사업 성과로 보정되지 않았다(NOT_CALIBRATED).
            </p>
          </div>
          <div className="max-h-[32rem] overflow-y-auto">
            <p className="text-xs font-medium text-slate-500">
              근거 사실 {result.facts.length}개 · 불리한 사실 {adverseCount}개
            </p>
            {kinds.map((k) => (
              <div key={k} className="mt-2">
                <p className="text-xs font-semibold text-slate-700">{KIND_LABEL[k] ?? k}</p>
                <ul className="mt-1 space-y-1">
                  {byKind.get(k)!.map((f) => (
                    <li key={f.id} ref={(el) => { if (el) factRefs.current.set(f.id, el); }}
                        data-testid={`fact-${f.id}`}
                        className={`rounded px-2 py-1 text-xs ${active === f.id ? "ring-2 ring-indigo-500" : ""} ${
                          f.adverse ? "bg-red-50 text-red-800" : "bg-slate-50 text-slate-700"}`}>
                      <span className="mr-1 font-medium">{f.id}</span>
                      {f.adverse && <span className="mr-1 rounded bg-red-600 px-1 text-[10px] text-white">불리</span>}
                      {result.appended_adverse.includes(f.id) && (
                        <span className="mr-1 rounded bg-amber-500 px-1 text-[10px] text-white">덧붙임</span>
                      )}
                      {f.text}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
