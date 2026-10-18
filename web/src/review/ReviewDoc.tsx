import { useEffect, useMemo, useState } from "react";
import type {
  AgreementStatus, EditPatch, Judgment, ReviewDocView, ReviewFact, ReviewNote,
} from "./reviewApi";

/** 인사팀 검토 자료(A 문서형, 2026-10-19 사용자 선택 -- 시안 justification-layout.html).
 *  세 층을 눈으로 구분한다: 시스템 사실(잠김·회색) · 본문(AI 초안 → 부서장 수정, 노란 밑줄) · 부서장 의견·해명(초록 줄)·이의(빨간 줄).
 *  인쇄(editable=false)에서도 글자 머리("시스템 사실 · 수정 불가" 등)를 남겨 흑백으로도 구분된다. */

export const AGREEMENT_LABEL: Record<AgreementStatus, string> = {
  agreed: "합의함", requested: "요청 중", hr_assigned: "인사팀 사전 배정",
};
const ORIGIN_LABEL = { moved: "진행 사업에서 옮겨 옴", partly_free: "일부 여유", bench: "대기 인력" } as const;

/** 문장 단위로 나눈다(마침표·물음표·느낌표 뒤 공백·줄바꿈). */
export function sentences(text: string | null | undefined): string[] {
  return (text ?? "").split(/(?<=[.!?。])\s+|\n+/).map((s) => s.trim()).filter(Boolean);
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

/** 본문: AI 원본에 없는 문장(부서장이 쓰거나 고친 문장)에 "부서장 수정" 표시. */
function Body({ doc, body }: { doc: ReviewDocView; body: string | null }) {
  const ai = useMemo(() => new Set(sentences(doc.body_ai)), [doc.body_ai]);
  const text = body ?? doc.body_ai;
  if (!text) return <p className="text-[13.5px] text-slate-500">아직 본문이 없습니다. "AI 초안 만들기"를 누르거나 직접 씁니다.</p>;
  return (
    <p className="text-[14px] leading-7 text-slate-800">
      {sentences(text).map((s, i) => ai.has(s) ? <span key={i}>{s} </span> : (
        <span key={i}>
          <span className="border-b-2 border-amber-400 bg-amber-50">{s}</span>
          <span className="mx-1 rounded bg-amber-200 px-1 align-[1px] text-[10.5px] text-amber-900">부서장 수정</span>{" "}
        </span>
      ))}
    </p>
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
  const dirty = JSON.stringify(d) !== JSON.stringify(draftOf(doc));
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
      <header className="flex flex-wrap items-end justify-between gap-2 border-b-2 border-ink pb-2">
        <div>
          <h2 className="text-lg font-bold">{doc.project_name} <span className="text-sm font-normal text-slate-500">({doc.project_id})</span></h2>
          <p className="text-[12.5px] text-slate-500">
            {emergency ? "긴급 투입" : "신규 사업 편성"} · 새로 들어온 사람 {doc.moves.joined.length}명 · 팀 {doc.team.length}명
            · 확정 {doc.confirm.at ? new Date(doc.confirm.at).toLocaleString("ko-KR") : "-"}
          </p>
        </div>
        <p className="text-[12.5px] text-slate-500">상태: {doc.status === "exported" ? "내보냄" : "작성 중"}</p>
      </header>
      {doc.reverted && (
        <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-700">
          이 확정은 되돌려졌습니다. 자료는 읽기만 할 수 있고 내보낼 수 없습니다.
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
                                  onClick={() => void onDraft(Boolean(doc.body_ai))}>
                {doc.body_ai ? "AI 초안 다시 만들기" : "AI 초안 만들기"}
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
        ) : <div className="mt-2"><Body doc={doc} body={d.body} /></div>}
      </section>

      <section className="mt-6">
        <h3 className="text-[15px] font-semibold text-ink">2. 옮겨 온 인력</h3>
        {target && factBlock(target)}
        {doc.moves.joined.map((j) => {
          const facts = doc.facts.filter((f) => f.section === "move" && f.owner === j.person_id);
          const jd = d.judgments[j.person_id];
          return (
            <div key={j.person_id} className="mt-3 rounded-lg border border-line px-3 py-2.5">
              <div className="font-semibold">
                {name(j.person_id)} <span className="font-normal text-slate-500">({j.grade ?? "-"})</span>
                <span className={`ml-2 rounded-full px-2 py-0.5 text-[11px] ${j.origin === "moved" ? "bg-amber-100 text-amber-800" : "bg-pine-100 text-pine-800"}`}>
                  {j.origin === "moved" ? `${j.sources.map((s) => s.project_id).join("·")}에서 옮겨 옴` : ORIGIN_LABEL[j.origin]}
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
          <button type="button" className="btn-primary" disabled={busy || !dirty || editingBody} onClick={() => void save()}>저장</button>
          {onPdf && <button type="button" className="btn" disabled={busy || dirty} title={dirty ? "먼저 저장합니다" : undefined}
                            onClick={() => void onPdf()}>PDF 내보내기</button>}
        </div>
      )}
    </article>
  );
}
