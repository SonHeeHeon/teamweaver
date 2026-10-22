/** 긴급 투입 "상황을 말로"(2026-10-22 사용자 "4번은 말로 하게되면 추천안(인력)을 받아 선택하도록", claude-a).
 *  부서장이 쓴 상황 글을 AI가 요구(기술·인원·등급)로 읽는다 -- 원문 근거에 밑줄, 글에 쓴 기술은 켜고 미루어 본 기술은 꺼 둔다.
 *  "이대로 추천 받기"를 누르면 그 값으로 기존 계산(최선 조합·후보 순위)이 추천안을 낸다. AI는 사람을 보지 않고 고르지도 않는다. */
import { useEffect, useState, type ReactNode } from "react";
import { Spinner } from "../ui";
import { postEmergencyParse, type Grade, type ParsedRequest } from "./workspaceApi";

export interface ReadRequest { skills: string[]; n: number | null; grade: Grade | null }

const WHY: Record<string, string> = {
  no_client: "AI 키가 없어",
  external_blocked: "실데이터라 회사 밖 AI로 보내지 않아",
};

/** 원문에 근거 밑줄(글에 쓴 기술 = 실선, 미루어 본 기술 = 점선) */
function Marked({ r }: { r: ParsedRequest }) {
  const spans = [...r.skills].sort((a, b) => a.start - b.start);
  const out: ReactNode[] = [];
  let at = 0;
  for (const s of spans) {
    if (s.start < at) continue;
    if (s.start > at) out.push(r.text.slice(at, s.start));
    out.push(<mark key={`${s.id}-${s.start}`} title={`${s.name}${s.kind === "implied" ? " (미루어 봄)" : ""}`}
                   className={`bg-transparent px-0 text-pine-900 underline underline-offset-4 ${s.kind === "implied" ? "decoration-dotted decoration-amber-600" : "decoration-pine-600"}`}>
      {r.text.slice(s.start, s.end)}</mark>);
    at = s.end;
  }
  out.push(r.text.slice(at));
  return <p className="rounded border border-slate-200 bg-white px-2 py-1.5 text-[13px] leading-6 text-slate-800">{out}</p>;
}

export function RequestReader({ datasetVersion, text, disabled, onApply }: {
  datasetVersion: string;
  text: string;
  disabled?: boolean;
  onApply: (req: ReadRequest) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ParsedRequest | null>(null);
  const [on, setOn] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  // 글을 고치면 예전 읽기는 맞지 않는다 -- 지운다
  useEffect(() => { if (res && res.text !== text.trim().slice(0, 300)) setRes(null); }, [text]); // eslint-disable-line react-hooks/exhaustive-deps

  async function read() {
    setBusy(true);
    setError(null);
    try {
      const r = await postEmergencyParse(datasetVersion, text.trim());
      setRes(r);
      setOn(Object.fromEntries(r.skills.map((s) => [s.id, s.kind === "explicit"])));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const chosen = res ? res.skills.filter((s) => on[s.id]) : [];
  return (
    <div className="space-y-2">
      <button type="button" className="btn px-2.5 py-1 text-xs" disabled={disabled || busy || !text.trim()} onClick={() => void read()}
              title="이 글을 AI에 보내 필요한 기술·인원·등급을 읽습니다(사람 정보는 보내지 않습니다)">
        {busy ? <Spinner status={false} label="읽는 중…" /> : "AI로 요구 읽기"}
      </button>
      {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
      {res && (
        <section aria-label="AI가 읽은 요구" className="space-y-1.5 rounded-md border border-pine-200 bg-pine-50/60 px-3 py-2 text-xs">
          <p className="font-medium text-pine-900">
            {res.method === "ai" ? "AI가 읽은 요구" : `사전으로 읽은 요구 — ${WHY[res.fallback_reason ?? ""] ?? "AI를 쓰지 못해"} 이름이 그대로 적힌 기술만`}
            {res.usage && <span className="ml-1 font-normal text-slate-500">({res.usage.latency_s.toFixed(1)}초)</span>}
          </p>
          <Marked r={res} />
          {res.skills.length > 0 ? (
            <div className="flex flex-wrap gap-1.5" aria-label="읽은 기술">
              {res.skills.map((s) => (
                <label key={s.id} className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${on[s.id] ? "border-pine-300 bg-white" : "border-slate-200 bg-slate-50 text-slate-500"}`}
                       title={`근거: "${s.quote}"`}>
                  <input type="checkbox" checked={!!on[s.id]} disabled={disabled} onChange={(e) => setOn((o) => ({ ...o, [s.id]: e.target.checked }))} />
                  {s.name}
                  <span className="text-[11px] text-slate-500">{s.kind === "implied" ? "미루어 봄" : "글에 씀"}</span>
                </label>
              ))}
            </div>
          ) : <p className="text-slate-600">기술을 정하지 못했습니다{res.question ? ` — ${res.question}` : " — 기술 이름을 넣어 다시 써 주세요."}</p>}
          <p className="text-slate-700">
            인원 <b>{res.headcount ?? "—"}</b> · 등급 <b>{res.grade ?? "—"}</b>
            {res.grade_at_least && res.grade && <span className="text-slate-500"> ("{res.grade} 이상" — 지금은 한 등급으로 찾습니다. 위 등급까지 보려면 등급을 비우세요)</span>}
          </p>
          {res.unmapped_terms.length > 0 && <p className="text-slate-600">사전에 없는 말: {res.unmapped_terms.join(", ")} (요구로 쓰지 않음)</p>}
          {res.warnings.map((w) => <p key={w} className="text-amber-800">{w}</p>)}
          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            <button type="button" className="btn-primary px-2.5 py-1 text-xs" disabled={disabled || chosen.length === 0}
                    onClick={() => onApply({ skills: chosen.map((s) => s.name), n: res.headcount, grade: res.grade })}>
              이대로 추천 받기
            </button>
            <span className="text-slate-500">추천은 지금까지 쓰던 계산이 하고(AI 아님), 고르는 것은 부서장입니다.</span>
          </div>
        </section>
      )}
    </div>
  );
}
