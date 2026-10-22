/** 등록 양식 ④ "자료에서 필요 기술 읽기"(2026-10-23 사용자 "그 자료를 넣으면 llm … 을 통해서 분석해서 알아서 필요 기술에 대해 정의해서 등록해주고
 *  사용자가 보고 거기서 추가로 더 등록할 기술 있으면 더 등록하고 혹은 수정해야할게 있으면 수정 및 삭제", claude-b -- 읽기 함수는 claude-a `api/rag/rfp_read`).
 *  AI 결과는 **제안**이다: 경력이나 인원이 적힌 필수만 기본 체크하고, 우대·이미 적은 기술·**경력도 인원도 없는 필수**(과업 범위 문장에서 추정한 것 --
 *  화면 확인 2026-10-23: J020 RFP의 "이전 뒤 성능·보안 점검"에서 성능 테스트·정보보안이 필수로 나왔다)는 체크하지 않는다. 이미 적은 값은 덮어쓰기 전에
 *  비교해 보인다. 넣은 뒤에는 기존 칸에서 고치고 뺀다.
 *  근거 말(quote)은 서버가 원문에 있는지 확인한 글이다. */
import { useEffect, useRef, useState } from "react";
import { Spinner } from "../ui";
import {
  experience, fallbackText, readRequirements, readable, type AiDestination, type PendingDoc, type ReadResult, type ReadSkill,
} from "./projectDocsApi";
import type { SkillNeed } from "./workspaceApi";

/** 경력·인원이 자료에 없을 때 넣는 값 -- 양식에서 고친다 */
const DEFAULT_MONTHS = 24;
const DEFAULT_HEADCOUNT = 1;

/** 읽기 전에 알리는 보내는 곳(폴백 리뷰 S2 -- 가상 데이터에서도 실제 비밀유지 RFP를 고르면 회사 밖 AI로 간다) */
const DESTINATION: Record<AiDestination, string> = {
  external: "자료 글자가 회사 밖 AI(OpenAI 등)로 갑니다 — 비밀유지 자료(민간 RFP 등)는 읽지 마세요. 사람 정보는 보내지 않습니다.",
  onprem: "자료 글자가 사내 AI로 갑니다. 사람 정보는 보내지 않습니다.",
  none: "AI를 쓰지 않고(키 없음·실데이터 외부 차단) 사전의 기술 이름으로 읽습니다.",
};

export function SkillReader({ docs, destination, current, canonicalOf, onApply }: {
  docs: PendingDoc[];
  /** 자료 글자를 보낼 곳(서버 `ai_destination`) -- 모르면 일반 안내 */
  destination?: AiDestination | null;
  /** 지금 양식에 적힌 기술 */
  current: SkillNeed[];
  /** 적은 이름 → 사전 대표 이름(별칭으로 적었어도 같은 기술로 본다) */
  canonicalOf: (name: string) => string;
  /** 고른 제안을 양식에 합친다(같은 기술이면 값을 바꾼다) */
  onApply: (picked: SkillNeed[]) => void;
}) {
  const files = docs.filter((d) => readable(d.file));
  // 번호가 아니라 이름으로 고른다 -- ①에서 자료를 바꿔도 다른 파일을 읽지 않게(폴백 리뷰 NIT)
  const [whichName, setWhichName] = useState<string | null>(null);
  const target = files.find((d) => d.file.name === whichName) ?? files[0];
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReadResult | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const ctl = useRef<AbortController | null>(null);
  useEffect(() => () => ctl.current?.abort(), []);      // 단계를 떠나면 기다리던 읽기를 끊는다
  const have = new Map(current.map((s) => [canonicalOf(s.name).toLowerCase(), s]));
  const existing = (s: ReadSkill) => have.get(s.name.toLowerCase()) ?? null;
  const guessed = (s: ReadSkill) => s.kind === "required" && s.months === null && s.headcount === null;

  if (docs.length === 0) {
    return <p className="text-xs text-slate-500">① 기본에서 발주 자료(PDF·HWPX·DOCX 등)를 고르면 그 자료에서 필요 기술을 읽어 채울 수 있습니다.</p>;
  }
  if (files.length === 0) {
    return <p className="text-xs text-amber-800">고른 자료는 글자를 읽을 수 없는 형식입니다(옛 한글 HWP·ZIP). PDF·HWPX로 저장해 올리면 필요 기술을 읽을 수 있습니다.</p>;
  }

  async function run() {
    const file = target.file;
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await readRequirements(file, c.signal);
      if (c.signal.aborted) return;
      setResult(r);
      setChecked(new Set(r.skills.filter((s) => s.kind === "required" && !existing(s) && !guessed(s)).map((s) => s.concept_id)));
    } catch (e) {
      if (c.signal.aborted) return;
      setError((e as Error).message);
    } finally {
      if (ctl.current === c) setBusy(false);
    }
  }
  function stop() { ctl.current?.abort(); setBusy(false); }
  function apply() {
    if (!result) return;
    onApply(result.skills.filter((s) => checked.has(s.concept_id))
      .map((s) => ({ name: existing(s)?.name ?? s.name, min_months: s.months ?? DEFAULT_MONTHS, headcount: s.headcount ?? DEFAULT_HEADCOUNT })));
    setChecked(new Set());
  }
  const toggle = (id: string) => setChecked((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <section className="rounded-md border border-pine-200 bg-[#F6FAF8] px-3 py-2.5" aria-label="자료에서 필요 기술 읽기">
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-[13px]">자료에서 필요 기술 읽기(AI)</b>
        {files.length > 1 && (
          <select className="field w-auto py-0.5 text-xs" aria-label="읽을 자료" value={target.file.name}
                  onChange={(e) => setWhichName(e.target.value)}>
            {files.map((d, i) => <option key={`${d.file.name}-${i}`} value={d.file.name}>{d.kind} · {d.file.name}</option>)}
          </select>
        )}
        {busy ? (
          <>
            <Spinner label="읽는 중… (OpenAI 수 초, 사내 AI는 더 걸림)" />
            <button type="button" className="btn-ghost px-1.5 py-0.5 text-xs" onClick={stop}>멈추기</button>
          </>
        ) : (
          <button type="button" className="btn px-2.5 py-1 text-xs" onClick={() => void run()}>
            {result ? "다시 읽기" : files.length === 1 ? `${files[0].file.name} 읽기` : "읽기"}
          </button>
        )}
      </div>
      {/* 모르면(요구 기술 목록을 못 읽음) 회사 밖으로 보고 경고한다 -- 경고가 사라지는 쪽보다 안전하다(2차 폴백 리뷰 NIT) */}
      <p className={`mt-1 text-[12px] ${(destination ?? "external") === "external" ? "text-amber-800" : "text-slate-500"}`}
         data-destination={destination ?? "unknown"}>
        {DESTINATION[destination ?? "external"]} 결과는 제안이고, 고른 것만 아래 필요 기술에 들어갑니다.</p>
      {error && <p role="alert" className="mt-1.5 text-[12.5px] text-red-700">{error}</p>}
      {result && (
        <div className="mt-2 space-y-2 text-[12.5px]">
          <p className="text-slate-600">
            {result.method === "ai"
              ? `AI가 읽었습니다(${result.model ?? "모델"}${result.usage?.latency_s ? ` · ${result.usage.latency_s.toFixed(1)}초` : ""}${
                  result.ai_destination === "onprem" ? " · 사내 AI" : result.ai_destination === "external" ? " · 회사 밖 AI" : ""}).`
              : `${fallbackText(result.fallback_reason)} AI 없이 사전의 기술 이름으로 찾았습니다(문장 말로 필수·우대를 가름).`}
          </p>
          {result.warnings.map((w) => <p key={w} className="text-amber-800">{w}</p>)}
          {result.skills.length === 0 ? <p className="text-slate-500">자료에서 사전에 있는 기술을 찾지 못했습니다. 아래에서 직접 넣습니다.</p> : (
            <ul className="space-y-1" aria-label="읽은 필요 기술">
              {result.skills.map((s) => {
                const old = existing(s);
                return (
                  <li key={s.concept_id} className="rounded border border-slate-200 bg-white px-2 py-1">
                    <label className="flex flex-wrap items-center gap-1.5">
                      <input type="checkbox" checked={checked.has(s.concept_id)} onChange={() => toggle(s.concept_id)}
                             aria-label={`${s.name} 넣기`} />
                      <b>{s.name}</b>
                      <span className={`rounded-full px-1.5 text-[11px] ${s.kind === "required" ? "bg-pine-100 text-pine-800" : "bg-amber-50 text-amber-800"}`}>
                        {s.kind === "required" ? "필수" : "우대"}</span>
                      {guessed(s) && <span className="text-amber-800" title="자료에 경력·인원 언급 없이 과업 설명에서 읽은 기술 -- 정말 필요한지 확인하고 고릅니다">
                        추정(경력·인원 언급 없음)</span>}
                      <span className="num text-slate-600">
                        {s.months !== null ? `경력 ${experience(s.months)} 이상` : `경력 미기재(넣을 값 ${experience(DEFAULT_MONTHS)})`} ·{" "}
                        {s.headcount !== null ? `${s.headcount}명` : `인원 미기재(넣을 값 ${DEFAULT_HEADCOUNT}명)`}</span>
                      {old && <span className="text-amber-800">이미 적음: {experience(old.min_months)}·{old.headcount}명 — 체크하면 바꿉니다</span>}
                    </label>
                    <p className="mt-0.5 truncate text-[11.5px] text-slate-500" title={s.quote}>근거: “{s.quote}”</p>
                  </li>
                );
              })}
            </ul>
          )}
          {result.mentioned_not_required.length > 0 && (
            <p className="text-slate-500">자료에 나오지만 요구로 보지 않음: {result.mentioned_not_required.map((m) => m.name).join(", ")}</p>
          )}
          {result.unmapped_terms.length > 0 && (
            <p className="text-slate-500">사전에 없는 말: {result.unmapped_terms.join(", ")} — 필요하면 아래 "기술 검색"으로 그대로 넣습니다.</p>
          )}
          {result.skills.length > 0 && (
            <button type="button" className="btn-primary px-2.5 py-1 text-xs" disabled={checked.size === 0} onClick={apply}>
              고른 {checked.size}개 필요 기술에 넣기</button>
          )}
        </div>
      )}
    </section>
  );
}
