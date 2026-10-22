import { useEffect, useMemo, useState } from "react";
import type {
  AgreementStatus, BodyCheck, EditPatch, Judgment, ReviewDocView, ReviewFact, ReviewNote,
} from "./reviewApi";
import { fallbackLabel } from "./reviewApi";
import { Spinner } from "../ui";

/** 인사팀 검토 자료(A 문서형, 2026-10-19 사용자 선택 -- 시안 justification-layout.html).
 *  세 층을 눈으로 구분한다: 시스템 사실(잠김·회색) · 본문(AI 초안 → 부서장 수정, 노란 밑줄) · 부서장 의견·해명(초록 줄)·이의(빨간 줄).
 *  인쇄(editable=false)에서도 글자 머리("시스템 사실 · 수정 불가" 등)를 남겨 흑백으로도 구분된다. */

/** 확정 종류 이름 -- 이력 종류(source)가 있으면 그것으로(What-if 확정도 "손 편성"), 예전 자료는 kind로. */
export function confirmLabel(c: { kind: string; source?: string | null }): string {
  if (c.source === "apply_auto") return "신규 사업 자동 편성";
  if (c.source === "apply_manual") return "손 편성(신규 사업·What-if)";
  return c.kind === "emergency" ? "긴급 투입" : "신규 사업 편성";
}

export const AGREEMENT_LABEL: Record<AgreementStatus, string> = {
  agreed: "합의함", requested: "요청 중", hr_assigned: "인사팀 사전 배정",
};
const ORIGIN_LABEL = { moved: "진행 사업에서 옮겨 옴", partly_free: "일부 여유", bench: "대기 인력",
                       follow_on: "지금 사업이 끝난 뒤 이어서 투입" } as const;

/** 문장 단위로 나눈다(마침표·물음표·느낌표 뒤 공백·줄바꿈). */
export function sentences(text: string | null | undefined): string[] {
  return (text ?? "").split(/(?<=[.!?。])\s+|\n+/).map((s) => s.trim()).filter(Boolean);
}

/** 본문 속 근거 표식 [F12]의 번호(2026-10-22 부서장 점검 f13 -- 내부 번호를 그대로 보이지 않고 아래 "인용한 사실" 번호로).
 *  본문에 나온 순서(1, 2, 3 …)로 매기고, 본문에 없는 인용 사실(부서장이 고쳐 지운 것)은 그 뒤에. 아래 목록도 이 번호 순서. */
export function citeNumbers(text: string | null | undefined, cited: { id: string }[] | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const m of (text ?? "").matchAll(/\[(F\d+)\]/g)) if (!out.has(m[1])) out.set(m[1], out.size + 1);
  for (const c of cited ?? []) if (!out.has(c.id)) out.set(c.id, out.size + 1);
  return out;
}

/** 한 문장: 근거 표식은 위첨자 번호로 */
function Prose({ text, cites }: { text: string; cites: Map<string, number> }) {
  return (
    <>
      {text.split(/(\[F\d+\])/g).map((part, i) => {
        const m = /^\[(F\d+)\]$/.exec(part);
        return m ? <sup key={i} className="ml-px text-[9.5px] text-slate-500" title="아래 '인용한 사실' 번호">{cites.get(m[1]) ?? "*"}</sup>
          : <span key={i}>{part}</span>;
      })}
    </>
  );
}

interface Draft {
  body: string | null;
  notes: ReviewNote[];
  judgments: Record<string, Judgment>;
  memo: string;
}

function draftOf(doc: ReviewDocView): Draft {
  return { body: doc.body, notes: doc.notes, judgments: doc.judgments, memo: doc.situation_memo };
}

function Locked({ fact, children }: { fact: ReviewFact; children?: React.ReactNode }) {
  return (
    <div className="mt-1.5 rounded-md border border-line bg-slate-50 px-3 py-2">
      <div className="text-[11px] text-slate-500">🔒 시스템 사실 · 수정 불가</div>
      <p className={`text-[13.5px] leading-6 ${fact.adverse ? "text-red-700" : "text-slate-800"}`}>
        {fact.adverse && <span className="mr-1 font-semibold">[불리]</span>}{fact.text}
      </p>
      {children}
    </div>
  );
}

function NoteView({ n }: { n: ReviewNote }) {
  const obj = n.type === "objection";
  return (
    <div className={`mt-1 border-l-[3px] pl-2 text-[12.5px] ${obj ? "border-red-600 text-red-800" : "border-pine-500 text-pine-800"}`}>
      <b>{obj ? "이의(사실과 다름)" : "부서장 해명"}</b>{n.orphan ? " · 근거 사실이 바뀜" : ""}: {n.text}
      {n.author && <span className="ml-1 text-slate-400">— {n.author}</span>}
    </div>
  );
}

function NoteEditor({ onAdd }: { onAdd: (type: "reply" | "objection", text: string) => void }) {
  const [type, setType] = useState<"reply" | "objection" | null>(null);
  const [text, setText] = useState("");
  if (!type) {
    return (
      <div className="mt-1.5 flex gap-2 text-[12px]">
        <button type="button" className="text-pine-700 underline" onClick={() => setType("reply")}>해명 달기</button>
        <button type="button" className="text-red-700 underline" onClick={() => setType("objection")}>이의 제기</button>
      </div>
    );
  }
  const label = type === "reply" ? "해명" : "이의 사유(필수)";
  return (
    <div className="mt-1.5">
      <label className="text-[12px] text-slate-600">{label}
        <textarea aria-label={label} className="field mt-1 min-h-[56px] text-[13px]" value={text}
                  onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="mt-1 flex gap-2">
        <button type="button" className="btn text-[12px]" disabled={!text.trim()}
                onClick={() => { onAdd(type, text.trim()); setText(""); setType(null); }}>
          {type === "reply" ? "해명 추가" : "이의 추가"}
        </button>
        <button type="button" className="btn-ghost text-[12px]" onClick={() => { setText(""); setType(null); }}>취소</button>
      </div>
    </div>
  );
}

function FactBlock({ fact, notes, editable, onAdd, onRemove }: {
  fact: ReviewFact; notes: ReviewNote[]; editable: boolean;
  onAdd: (type: "reply" | "objection", text: string) => void; onRemove: (n: ReviewNote) => void;
}) {
  return (
    <Locked fact={fact}>
      {notes.map((n, i) => (
        <div key={i} className="flex items-start justify-between gap-2">
          <NoteView n={n} />
          {editable && <button type="button" className="mt-1 text-[11px] text-slate-400 underline" onClick={() => onRemove(n)}>지우기</button>}
        </div>
      ))}
      {editable && <NoteEditor onAdd={onAdd} />}
    </Locked>
  );
}

/** AI 초안 요약의 사실 검사 한 줄 + 인용 사실 목록(펼치기 -- 인쇄·PDF에서는 펼친 채). 검사는 AI 초안에 대한 것이다(부서장이 고친 문장은 대상 아님). */
function BodyCheckLine({ check, edited, print, cites }: { check: BodyCheck; edited: boolean; print: boolean; cites: Map<string, number> }) {
  return (
    <details className="mt-2 text-[12px] text-slate-600" open={print}>
      <summary className="cursor-pointer select-none">
        {/* 요약을 못 만들어 사실 목록 본문을 쓴 경우는 "미통과"가 아니라 상태를 말한다(리뷰 NIT -- 본문은 서버가 채운 사실 문구뿐) */}
        <span className={check.ok ? "text-pine-800" : "text-amber-800"}>
          {check.ok ? "✓ AI 초안 사실 검사 통과" : check.method === null ? "요약 없음 -- 사실 목록 본문" : "AI 초안 사실 검사 미통과"}</span>
        {" · "}{fallbackLabel(check.method, check.fallback_reason)} · 인용한 사실 {check.cited.length}개(이 사업 사실 {check.facts_total}개 중)
        {edited && <span className="ml-1 text-slate-500">· 부서장이 고친 문장은 검사 대상이 아님</span>}
      </summary>
      <p className="mt-1 text-slate-500">AI는 사실 번호만 고르고, 숫자·충족 여부 같은 문구는 시스템이 채웁니다. 만들 때 문장마다 검사해 걸리면 규칙 요약으로 바꾸고,
        저장 전에 저장할 글이 검사한 원문 그대로인지 한 번 더 확인합니다: {check.rules}.</p>
      <ul className="mt-1 list-disc pl-5">
        {[...check.cited].sort((a, b) => (cites.get(a.id) ?? 99) - (cites.get(b.id) ?? 99)).map((c) => <li key={c.id}><span className="num text-slate-400">[{cites.get(c.id) ?? "*"}]</span> {c.text}{c.adverse && <span className="ml-1 text-amber-700">(불리한 사실)</span>}</li>)}
      </ul>
    </details>
  );
}

/** 본문: 문장마다 한 줄(2026-10-22 부서장 점검 f12 -- 한 문단으로 이어져 읽기 어려웠다), 근거 표식은 위첨자 번호.
 *  AI 원본에 없는 문장(부서장이 쓰거나 고친 문장)에 "부서장 수정" 표시. */
function Body({ doc, body, cites }: { doc: ReviewDocView; body: string | null; cites: Map<string, number> }) {
  const ai = useMemo(() => new Set(sentences(doc.body_ai)), [doc.body_ai]);
  const text = body ?? doc.body_ai;
  if (!text) return <p className="text-[13.5px] text-slate-500">아직 본문이 없습니다. "AI 초안 만들기"를 누르거나 직접 씁니다.</p>;
  return (
    <div className="space-y-1 text-[14px] leading-7 text-slate-800">
      {sentences(text).map((s, i) => ai.has(s) ? <p key={i}><Prose text={s} cites={cites} /></p> : (
        <p key={i}>
          <span className="border-b-2 border-amber-400 bg-amber-50"><Prose text={s} cites={cites} /></span>
          <span className="mx-1 rounded bg-amber-200 px-1 align-[1px] text-[10.5px] text-amber-900">부서장 수정</span>
        </p>
      ))}
    </div>
  );
}

export function ReviewDoc({ doc, editable, onSave, onDraft, onPdf, busy = false }: {
  doc: ReviewDocView;
  editable: boolean;
  onSave?: (patch: EditPatch) => Promise<void>;
  onDraft?: (force: boolean) => Promise<void>;
  onPdf?: () => Promise<void>;
  busy?: boolean;
}) {
  const [d, setD] = useState<Draft>(() => draftOf(doc));
  const [editingBody, setEditingBody] = useState(false);
  const [bodyText, setBodyText] = useState("");
  useEffect(() => { setD(draftOf(doc)); setEditingBody(false); }, [doc]);
  const can = editable && !doc.reverted;
  /** 누른 버튼(AI 초안·PDF·저장)에 "…중"을 보인다 -- AI 초안은 수십 초 걸릴 수 있다(2026-10-21 사용자 요청 2 "로딩 액션") */
  const [doing, setDoing] = useState<null | "draft" | "pdf" | "save">(null);
  const act = async (what: "draft" | "pdf" | "save", fn: () => Promise<void>) => {
    setDoing(what);
    try { await fn(); } finally { setDoing(null); }
  };
  // 본문 편집창이 열려 있으면(아직 "본문 반영" 전) 그것도 저장 안 한 변경이다 -- PDF·AI가 응답으로 편집을 덮지 않게(Codex 리뷰)
  const dirty = JSON.stringify(d) !== JSON.stringify(draftOf(doc)) || editingBody;
  const name = (id: string) => doc.names[id] ?? id;
  const emergency = doc.confirm.kind === "emergency";
  const notesFor = (key: string) => d.notes.filter((n) => n.key === key);
  const add = (key: string) => (type: "reply" | "objection", text: string) =>
    setD((x) => ({ ...x, notes: [...x.notes, { key, type, text }] }));
  const remove = (n: ReviewNote) => setD((x) => ({ ...x, notes: x.notes.filter((m) => m !== n) }));
  const factBlock = (f: ReviewFact) => (
    <FactBlock key={f.key} fact={f} notes={notesFor(f.key)} editable={can} onAdd={add(f.key)} onRemove={remove} />
  );
  const target = doc.facts.find((f) => f.kind === "MOVE_TARGET");
  // 주인 없는 "사업 전체" 결정(등급 대체·예산 증액) -- 예전에는 저장만 되고 화면·PDF 어디에도 보이지 않았다(2026-10-22 고침)
  const decisions = doc.facts.filter((f) => f.section === "move" && !f.owner && f.kind !== "MOVE_TARGET");
  const cites = citeNumbers(d.body ?? doc.body_ai, doc.body_ai_check?.cited);
  const movers = doc.moves.joined.filter((j) => j.origin === "moved").length;
  const adverse = doc.facts.filter((f) => f.section === "adverse");
  const appendix = doc.facts.filter((f) => f.section === "appendix");
  const keys = new Set(doc.facts.map((f) => f.key));
  const orphans = d.notes.filter((n) => !keys.has(n.key));
  const pending = Object.entries(d.judgments).filter(([, j]) => j.agreement.status === "requested").map(([p]) => name(p));
  const setJudgment = (p: string, patch: Partial<Judgment>) => setD((x) => {
    const cur: Judgment = x.judgments[p] ?? { text: "", agreement: { status: null, memo: "" } };
    return { ...x, judgments: { ...x.judgments, [p]: { ...cur, ...patch } } };
  });
  const save = async () => {
    if (!onSave) return;
    await onSave({
      // null(AI 초안으로 되돌림)은 빈 글로 보낸다 -- 서버는 None을 "그대로"로 읽고 빈 글을 "고친 본문 없음"으로 읽는다(폴백 리뷰 P2)
      revision: doc.revision, body: d.body ?? "", situation_memo: d.memo,
      notes: d.notes.map(({ key, type, text }) => ({ key, type, text })),
      judgments: Object.fromEntries(Object.entries(d.judgments).map(([p, j]) => [p, { text: j.text, agreement: j.agreement }])),
    });
  };

  return (
    <article className="review-doc bg-paper text-slate-900" aria-label="인사팀 검토 자료">
      {/* 저장·AI·PDF 요청 중에는 입력을 잠근다 -- 응답이 오면 자료를 다시 그려 그사이 쓴 글이 사라졌다(Codex Stop 훅) */}
      <fieldset disabled={busy} className="m-0 min-w-0 border-0 p-0">
      <header className="flex flex-wrap items-end justify-between gap-2 border-b-2 border-ink pb-2">
        <div>
          <h2 className="text-lg font-bold">{doc.project_name} <span className="text-sm font-normal text-slate-500">({doc.project_id})</span></h2>
          <p className="text-[12.5px] text-slate-500">
            {confirmLabel(doc.confirm)} · 새로 들어온 사람 {doc.moves.joined.length}명(옮겨 옴 {movers}명) · 팀 {doc.team.length}명
            · 확정 {doc.confirm.at ? new Date(doc.confirm.at).toLocaleString("ko-KR") : "-"}
          </p>
        </div>
        <p className="text-[12.5px] text-slate-500">상태: {doc.status === "exported" ? "내보냄" : "작성 중"}</p>
      </header>
      {doc.reverted && (
        <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-700">
          {doc.cancelled ? "이 확정은 취소됐습니다(변경 이력의 확정 취소)." : "이 확정은 되돌려졌습니다."} 자료는 읽기만 할 수 있고 내보낼 수 없습니다.
        </p>
      )}
      {doc.history === "out_of_history" && (
        <p className="mt-2 rounded-md bg-slate-100 px-3 py-2 text-[13px] text-slate-600">
          이 확정은 작업 이력 밖에 있습니다(다른 데이터로 바꿨거나 오래된 확정). 고치고 내보낼 수 있지만 AI 초안은 새로 만들 수 없습니다.
        </p>
      )}
      {pending.length > 0 && (
        <p className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
          원래 사업과의 합의가 아직 요청 중입니다: {pending.join(", ")}
        </p>
      )}
      <p className="mt-2 text-[12px] text-slate-500">{doc.disclaimer}</p>

      {emergency && (
        <section className="mt-4">
          <h3 className="text-[14px] font-semibold text-ink">상황 메모 <span className="text-[11px] font-normal text-slate-500">(부서장 입력 · AI에 보내지 않음)</span></h3>
          {can ? (
            <textarea aria-label="상황 메모" className="field mt-1 min-h-[56px]" value={d.memo}
                      onChange={(e) => setD((x) => ({ ...x, memo: e.target.value }))} />
          ) : <p className="mt-1 rounded-md border border-dashed border-amber-400 px-3 py-2 text-[13.5px]">{d.memo || "(없음)"}</p>}
        </section>
      )}

      <section className="mt-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[15px] font-semibold text-ink">1. {emergency ? "투입 사유" : "편성 사유"}</h3>
          {can && (
            <div className="flex gap-2 text-[12.5px]">
              {onDraft && <button type="button" className="btn" disabled={busy || !doc.active || dirty}
                                  title={!doc.active ? "이후 다른 변경이 있어 새 초안을 만들 수 없습니다"
                                    : dirty ? "저장하지 않은 변경이 있습니다 -- 먼저 저장합니다" : undefined}
                                  onClick={() => void act("draft", () => onDraft(Boolean(doc.body_ai)))}>
                {doing === "draft" ? <Spinner status={false} label="AI 초안 쓰는 중…(보통 10초 안팎)" />
                  : doc.body_ai ? "AI 초안 다시 만들기" : "AI 초안 만들기"}
              </button>}
              {!editingBody && <button type="button" className="btn" onClick={() => { setBodyText(d.body ?? doc.body_ai ?? ""); setEditingBody(true); }}>본문 고치기</button>}
            </div>
          )}
        </div>
        {editingBody ? (
          <div className="mt-2">
            <textarea aria-label="본문" className="field min-h-[140px] text-[13.5px]" value={bodyText}
                      onChange={(e) => setBodyText(e.target.value)} />
            <div className="mt-1 flex gap-2">
              <button type="button" className="btn" onClick={() => { setD((x) => ({ ...x, body: bodyText })); setEditingBody(false); }}>본문 반영</button>
              <button type="button" className="btn-ghost" onClick={() => setEditingBody(false)}>취소</button>
              {d.body !== null && <button type="button" className="btn-ghost" onClick={() => { setD((x) => ({ ...x, body: null })); setEditingBody(false); }}>AI 초안으로 되돌리기</button>}
            </div>
          </div>
        ) : <div className="mt-2"><Body doc={doc} body={d.body} cites={cites} /></div>}
        {doc.body_ai_check && <BodyCheckLine check={doc.body_ai_check} print={!editable} cites={cites}
                                             edited={d.body !== null && d.body !== doc.body_ai} />}
      </section>

      <section className="mt-6">
        <h3 className="text-[15px] font-semibold text-ink">2. 새로 들어온 인력
          <span className="ml-1 text-[12px] font-normal text-slate-500">— 진행 사업에서 옮겨 온 사람 {movers}명 · 대기·여유 인력 {doc.moves.joined.length - movers}명</span></h3>
        {target && factBlock(target)}
        {decisions.length > 0 && (
          <div className="mt-3">
            <div className="text-[12.5px] font-semibold text-slate-700">이 확정의 정원·예산 결정</div>
            {decisions.map(factBlock)}
          </div>
        )}
        {doc.moves.joined.map((j) => {
          const facts = doc.facts.filter((f) => f.section === "move" && f.owner === j.person_id);
          const jd = d.judgments[j.person_id];
          return (
            <div key={j.person_id} className="mt-3 rounded-lg border border-line px-3 py-2.5">
              <div className="font-semibold">
                {name(j.person_id)} <span className="font-normal text-slate-500">({j.grade ?? "-"})</span>
                <span className={`ml-2 rounded-full px-2 py-0.5 text-[11px] ${j.origin === "moved" ? "bg-amber-100 text-amber-800" : "bg-pine-100 text-pine-800"}`}>
                  {j.origin === "moved" ? `${j.sources.map((s) => s.project_id).join("·")}에서 옮겨 옴`
                    : j.concurrency ? `프로젝트 동시 수행(${j.concurrency.reason})` : ORIGIN_LABEL[j.origin]}
                </span>
              </div>
              {facts.map(factBlock)}
              {j.origin === "moved" && (
                <div className="mt-2 rounded-r-md border-l-[3px] border-pine-500 bg-pine-50 px-3 py-2">
                  <div className="text-[11px] text-pine-700">✍ 부서장 의견</div>
                  {can ? (
                    <>
                      <textarea aria-label={`${name(j.person_id)} 판단`} className="field mt-1 min-h-[56px] text-[13px]"
                                placeholder="예: 원래 사업은 막바지라 이 기술 작업이 거의 끝났다"
                                value={jd?.text ?? ""} onChange={(e) => setJudgment(j.person_id, { text: e.target.value })} />
                      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[12.5px]">
                        <label>원래 사업 합의{" "}
                          <select aria-label={`${name(j.person_id)} 합의 상태`} className="field inline-block w-auto py-0.5"
                                  value={jd?.agreement.status ?? ""}
                                  onChange={(e) => setJudgment(j.person_id, { agreement: { status: (e.target.value || null) as AgreementStatus | null,
                                                                                            memo: jd?.agreement.memo ?? "" } })}>
                            <option value="">미입력</option>
                            {(Object.keys(AGREEMENT_LABEL) as AgreementStatus[]).map((k) => <option key={k} value={k}>{AGREEMENT_LABEL[k]}</option>)}
                          </select>
                        </label>
                        <input aria-label={`${name(j.person_id)} 합의 메모`} className="field w-auto flex-1 py-0.5"
                               placeholder="누구와·어떻게·언제" value={jd?.agreement.memo ?? ""}
                               onChange={(e) => setJudgment(j.person_id, { agreement: { status: jd?.agreement.status ?? null, memo: e.target.value } })} />
                      </div>
                    </>
                  ) : (
                    <>
                      <p className="text-[13.5px]">{jd?.text || "(의견 없음)"}</p>
                      <p className="text-[12px] text-slate-500">
                        원래 사업 합의: {jd?.agreement.status ? AGREEMENT_LABEL[jd.agreement.status] : "미입력"}
                        {jd?.agreement.memo ? ` — ${jd.agreement.memo}` : ""}
                      </p>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </section>

      <section className="mt-6">
        <h3 className="text-[15px] font-semibold text-ink">3. 확인할 사실</h3>
        {adverse.length === 0 ? <p className="text-[13px] text-slate-500">불리한 사실이 없습니다.</p> : adverse.map(factBlock)}
        {orphans.length > 0 && (
          <div className="mt-3">
            <div className="text-[12px] text-slate-500">근거 사실이 바뀐 해명·이의</div>
            {orphans.map((n, i) => <NoteView key={i} n={n} />)}
          </div>
        )}
      </section>

      <details className="mt-6" open={!editable}>
        <summary className="cursor-pointer text-[14px] font-semibold text-ink">부록 · {emergency ? "새로 들어온 사람과 기존 팀원" : "팀원"} 사실 {appendix.length}건</summary>
        <ul className="mt-2 space-y-0.5 text-[12.5px] text-slate-600">
          {appendix.map((f) => <li key={f.key}>🔒 {f.text}{notesFor(f.key).map((n, i) => <NoteView key={i} n={n} />)}</li>)}
        </ul>
      </details>

      {can && (
        <div className="sticky bottom-0 mt-6 flex flex-wrap items-center justify-end gap-2 border-t border-line bg-paper py-3">
          {dirty && <span className="text-[12.5px] text-amber-700">저장하지 않은 변경이 있습니다</span>}
          <button type="button" className="btn-primary" disabled={busy || editingBody || JSON.stringify(d) === JSON.stringify(draftOf(doc))}
                  onClick={() => void act("save", save)}>{doing === "save" ? <Spinner status={false} label="저장 중…" /> : "저장"}</button>
          {onPdf && <button type="button" className="btn" disabled={busy || dirty} title={dirty ? "먼저 저장합니다" : undefined}
                            onClick={() => void act("pdf", onPdf)}>
            {doing === "pdf" ? <Spinner status={false} label="PDF 만드는 중…" /> : "PDF 내보내기"}</button>}
        </div>
      )}
      </fieldset>
    </article>
  );
}
