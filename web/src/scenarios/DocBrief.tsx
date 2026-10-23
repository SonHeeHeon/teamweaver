/** 부서장용 자료 요약 창(2026-10-23 사용자 "그 자료들 등록했을 때 그 등록한 프로젝트명을 누르거나 뭐 어디서든 각각의 자료를 llm으로 잘 압축 요약한
 *  (이 서비스를 쓰는 부서장입장에서 필요로 하는 정보를 잘 요약해서 보여주기) 걸 각각 보여줄수있는 화면? 팝업?", 결정 "사업명 클릭 → 창", claude-b).
 *  자료마다 탭. 요약은 처음 한 번 사람이 눌러 만든다(자동으로 만들면 비밀유지 자료가 묻지 않고 회사 밖 AI로 간다) -- 서버가 저장해 다시 열면 바로 보인다.
 *  항목마다 근거 말(서버가 원문에서 확인)을 펼쳐 볼 수 있고, 근거·숫자 검사를 못 넘은 항목은 서버가 뺐다(`api/rag/doc_brief`). */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Dialog, Spinner } from "../ui";
import {
  fallbackText, fetchDocSummary, makeDocSummary, type AiDestination, type DocSummary, type ProjectDoc, type SummaryResponse,
} from "./projectDocsApi";

const DESTINATION: Record<AiDestination, string> = {
  external: "자료 글자가 회사 밖 AI(OpenAI 등)로 갑니다 — 비밀유지 자료(민간 RFP 등)는 요약하지 마세요.",
  onprem: "자료 글자가 사내 AI로 갑니다.",
  none: "AI를 쓰지 않고(키 없음·실데이터 외부 차단) 칸별 열쇠말이 든 줄을 모읍니다.",
};

interface TabState { loading: boolean; making: boolean; data: SummaryResponse | null; error: string | null; stopped?: boolean }
const EMPTY: TabState = { loading: false, making: false, data: null, error: null };

export function DocBriefDialog({ projectName, docs, initialId, onClose }: {
  projectName: string; docs: ProjectDoc[]; initialId?: string | null; onClose: () => void;
}) {
  const [picked, setActive] = useState<string | null>(initialId ?? docs[0]?.id ?? null);
  // 보던 자료가 목록에서 빠지면 첫 자료로(모든 탭이 tabIndex -1이 되지 않게 -- 2차 NIT 5)
  const active = docs.some((d) => d.id === picked) ? picked : docs[0]?.id ?? null;
  const [tabs, setTabs] = useState<Record<string, TabState>>({});
  // 탭마다 따로 끊는다(폴백 리뷰 S8: 하나를 같이 쓰면 B를 만들 때 A가 끊겨 "요약하는 중"에 영영 멈췄다). 창을 닫을 때만 모두 끊는다.
  const ctls = useRef<Record<string, AbortController>>({});
  const asked = useRef<Set<string>>(new Set());
  useEffect(() => () => { for (const c of Object.values(ctls.current)) c.abort(); }, []);
  const doc = docs.find((d) => d.id === active) ?? null;
  const tab = (active && tabs[active]) || EMPTY;
  const put = (id: string, patch: Partial<TabState>) => setTabs((t) => ({ ...t, [id]: { ...(t[id] ?? EMPTY), ...patch } }));

  // 저장된 요약 불러오기 -- 탭을 바꿔도 끊지 않는다(끊으면 "불러오는 중"에 남았다), 탭마다 한 번(다시 불러오기는 reload)
  function load(d: ProjectDoc) {
    const id = d.id;
    put(id, { loading: true, error: null, stopped: false });
    fetchDocSummary(d.project_id, id)
      .then((data) => put(id, { loading: false, data }))
      .catch((e) => put(id, { loading: false, error: (e as Error).message }));
  }
  useEffect(() => {
    if (!doc || asked.current.has(doc.id)) return;
    asked.current.add(doc.id);
    load(doc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.id]);

  async function make() {
    if (!doc) return;
    const id = doc.id;
    ctls.current[id]?.abort();
    const c = new AbortController();
    ctls.current[id] = c;
    put(id, { making: true, error: null });
    try {
      const data = await makeDocSummary(doc.project_id, id, c.signal);
      if (!c.signal.aborted) put(id, { making: false, data });
    } catch (e) {
      if (!c.signal.aborted) put(id, { making: false, error: (e as Error).message });
    } finally {
      if (ctls.current[id] === c) delete ctls.current[id];
    }
  }
  function stop() {
    if (!active) return;
    ctls.current[active]?.abort();
    delete ctls.current[active];
    put(active, { making: false, stopped: true });     // 서버는 끝까지 만들어 저장할 수 있다 -- "다시 불러오기"로 확인(2차 NIT 5)
  }
  function onTabKey(e: KeyboardEvent, i: number) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = docs[(i + (e.key === "ArrowRight" ? 1 : docs.length - 1)) % docs.length];
    setActive(next.id);
    document.getElementById(`brief-tab-${next.id}`)?.focus();
  }

  return (
    <Dialog title={`자료 요약 — ${projectName}`} wide labelId="brief-title"
            actions={<button className="btn-ghost" onClick={onClose}>닫기</button>}>
      <p>부서장에게 필요한 것(무슨 일인지 · 기간·예산 · 인력 요구 · 평가 방식 · 유의 사항)만 자료마다 짧게 정리합니다. 항목의 "근거"는 자료 원문 그대로입니다.</p>
      {docs.length === 0 ? <p className="mt-3 text-slate-500">올린 자료가 없습니다.</p> : (
        <>
          <div role="tablist" aria-label="자료" className="mt-3 flex flex-wrap gap-1">
            {docs.map((d, i) => (
              <button key={d.id} id={`brief-tab-${d.id}`} role="tab" aria-selected={d.id === active} aria-controls="brief-panel"
                      tabIndex={d.id === active ? 0 : -1} title={d.filename} onKeyDown={(e) => onTabKey(e, i)}
                      className={`rounded-full px-2.5 py-1 text-xs ${d.id === active ? "bg-ink text-white" : "bg-slate-100 text-slate-700"}`}
                      onClick={() => setActive(d.id)}>
                {d.kind} · {d.filename.length > 18 ? `${d.filename.slice(0, 17)}…` : d.filename}
                {tabs[d.id]?.making && <span className="ml-1" aria-label="요약하는 중">…</span>}
              </button>
            ))}
          </div>
          <div id="brief-panel" role="tabpanel" aria-labelledby={doc ? `brief-tab-${doc.id}` : undefined}
               className="mt-3 max-h-[60vh] overflow-y-auto pr-1">
            {tab.loading && <Spinner label="불러오는 중…" />}
            {tab.error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-700">{tab.error}</p>}
            {doc && !tab.loading && !tab.making && (tab.error || tab.stopped) && (
              <p className="mb-2 text-[12px] text-slate-600">
                {tab.stopped ? "멈췄습니다. 서버가 이미 요약을 만들어 저장했을 수 있습니다. " : ""}
                <button type="button" className="btn-ghost px-1.5 py-0.5 text-xs" onClick={() => load(doc)}>다시 불러오기</button>
              </p>
            )}
            {doc && tab.data && !tab.data.readable && (
              <p className="text-amber-800">이 형식(ZIP 묶음)은 요약할 수 없습니다 — 묶음을 풀어 PDF·HWP·HWPX·DOCX 파일을 올립니다.</p>
            )}
            {doc && tab.data?.readable && (tab.making ? (
              <span className="inline-flex items-center gap-2">
                <Spinner label="요약하는 중… (AI 10~20초)" />
                <button type="button" className="btn-ghost px-1.5 py-0.5 text-xs" onClick={stop}>멈추기</button>
              </span>
            ) : tab.data.summary ? (
              <SummaryView s={tab.data.summary} createdAt={tab.data.created_at} onRemake={() => void make()} />
            ) : (
              <div className="space-y-2">
                <p className="text-slate-600">아직 요약이 없습니다. AI가 자료를 읽고 근거를 붙여 정리합니다(한 번 만들면 저장돼 다시 열 때 바로 보입니다).</p>
                <p className={`text-[12px] ${(tab.data.ai_destination ?? "external") === "external" ? "text-amber-800" : "text-slate-500"}`}
                   data-destination={tab.data.ai_destination ?? "unknown"}>{DESTINATION[tab.data.ai_destination ?? "external"]}</p>
                <button type="button" className="btn-primary px-3 py-1 text-xs" onClick={() => void make()}>
                  {tab.data.ai_destination === "none" ? "요약 만들기(AI 없이)" : "AI로 요약 만들기"}</button>
              </div>
            ))}
          </div>
        </>
      )}
    </Dialog>
  );
}

export function SummaryView({ s, createdAt, onRemake }: { s: DocSummary; createdAt: string | null; onRemake?: () => void }) {
  const filled = s.sections.filter((x) => x.items.length > 0);
  return (
    <div className="space-y-3 text-[13px] text-slate-700" data-summary="">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-pine-100 px-2 py-px text-xs text-pine-800">{s.doc_type}</span>
        {s.relevance === "low" && <span className="rounded-full bg-slate-100 px-2 py-px text-xs text-slate-600">부서장이 볼 내용이 적은 자료</span>}
      </div>
      {s.one_line && <p className="font-semibold text-slate-900">{s.one_line}</p>}
      {s.relevance === "low" && <p className="text-slate-500">표준 계약 조건·서약처럼 어느 사업에나 같은 내용이 대부분입니다.</p>}
      {s.warnings.map((w) => <p key={w} className="text-amber-800">{w}</p>)}
      {filled.length === 0 ? <p className="text-slate-500">근거를 확인한 요약 항목이 없습니다.</p> : filled.map((sec) => (
        <section key={sec.key} aria-label={sec.title}>
          <h4 className="text-[12.5px] font-bold text-slate-900">{sec.title}</h4>
          <ul className="mt-1 space-y-1">
            {sec.items.map((it, i) => (
              <li key={`${sec.key}-${i}`} className="rounded border border-slate-100 bg-white px-2 py-1">
                <span>{it.text}</span>
                {it.source === "rule" && s.method === "ai" && (
                  <span className="ml-1.5 rounded-full bg-amber-50 px-1.5 text-[10.5px] text-amber-800"
                        title="AI가 이 부분을 읽지 못해 열쇠말이 든 원문 줄을 그대로 넣었습니다 — 다시 만들면 AI로 읽습니다">열쇠말 줄</span>
                )}
                <details className="mt-0.5 text-[11.5px] text-slate-500">
                  <summary className="cursor-pointer select-none">근거</summary>
                  <q>{it.quote}</q>
                </details>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="border-t border-slate-100 pt-2 text-[11.5px] text-slate-500">
        {s.method === "ai"
          ? `AI 요약(${s.model ?? "모델"}${s.usage?.latency_s ? ` · ${s.usage.latency_s.toFixed(1)}초` : ""})`
          : `${fallbackText(s.fallback_reason)} AI 없이 열쇠말이 든 줄을 모았습니다`}
        {(s.chunks?.length ?? 0) > 1 && ` · 긴 자료라 ${s.chunks!.length}조각으로 나눠 읽었습니다(${s.chars.toLocaleString()}자)`}
        {(s.chunks ?? []).some((c) => c.method === "rule") && s.method === "ai" &&
          ` · ${(s.chunks ?? []).filter((c) => c.method === "rule").length}조각은 AI 실패로 열쇠말 줄`}
        {s.dropped.length > 0 && ` · 근거·숫자를 확인하지 못한 항목 ${s.dropped.length}개는 뺐습니다`}
        {createdAt && ` · 저장 ${localTime(createdAt)}`}
        {onRemake && <button type="button" className="btn-ghost ml-2 px-1.5 py-0.5 text-[11.5px]" onClick={onRemake}>다시 만들기</button>}
      </p>
    </div>
  );
}

/** 서버 시각(UTC ISO) → 이 컴퓨터 시각 "YYYY-MM-DD HH:mm" -- 그대로 자르면 UTC가 보였다(화면 확인: 19:33에 만든 요약이 "10:33") */
export function localTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
