/** 등록 양식 ① "자료에서 사업 정보 채우기(AI)"(2026-10-23 사용자 "자료에서 메타 정보 따와서 원래 우리가 신규 사업 등록시에 입력했던 프로젝트 이름, 기간, 예산 등을
 *  대신 작성해줄수있나? 물론 사용자가 보고 고칠수 있도록은 만들어 둬야겠지", claude-b -- 서버 `api/rag/doc_brief.read_project_meta`).
 *  결과는 **제안**이다: 칸마다 근거 말(서버가 원문에서 확인한 글)을 보이고, 체크한 칸만 양식에 넣는다. 넣은 뒤에는 보통 칸에서 고친다.
 *  - 기간: 자료의 **길이(개월)**만 쓴다 -- 시작 달은 양식에 적힌 달(사용자 결정 "기간 길이만 쓰기": 시연은 10월 공고·12월 시작 가정, 실제 자료는 4월 공고).
 *  - 월 예산: 총 사업비 ÷ 개월 ÷ 1만 원 = 가상 비용점(사용자 "1억에 6개월 프로젝트면 1억÷6개월 하면 월 비용 나오지않나?" -- 1점 = 1만 원 가정을 화면에 적는다).
 *  - 기본 체크는 AI가 읽고 서버가 근거를 확인한 칸만: 이미 적은 칸, 규칙으로만 찾은 칸, AI가 날짜로 센 기간(과 그 기간으로 나눈 월 예산)은 체크하지 않는다
 *    (폴백 리뷰 S1·S6 -- 서버가 확인하지 못한 값을 사실처럼 넣지 않게). */
import { useEffect, useRef, useState } from "react";
import { Spinner } from "../ui";
import {
  endMonth, fallbackText, monthlyPoints, readable, readProjectMeta, wonText,
  type AiDestination, type MetaField, type MetaResult, type PendingDoc,
} from "./projectDocsApi";
import { SECTORS, type ProjectForm as Form } from "./workspaceApi";

const DESTINATION: Record<AiDestination, string> = {
  external: "자료 글자가 회사 밖 AI(OpenAI 등)로 갑니다 — 비밀유지 자료(민간 RFP 등)는 읽지 마세요. 사람 정보는 보내지 않습니다.",
  onprem: "자료 글자가 사내 AI로 갑니다. 사람 정보는 보내지 않습니다.",
  none: "AI를 쓰지 않고(키 없음·실데이터 외부 차단) 자료의 정해진 말(사업명·사업예산·계약기간 등)로 읽습니다.",
};
/** 정보가 많은 자료부터 고른다(공고문에 사업명·기간·예산·마감이 다 있다) */
const KIND_ORDER = ["입찰공고문", "제안요청서(RFP)", "사전규격", "과업지시서", "사업 개요", "기타"];

export type Key = "name" | "sector" | "end_month" | "monthly_budget";
export interface Proposal {
  key: Key; label: string; shown: string; patch: Partial<Form>; field: MetaField<unknown>; note?: string; already: boolean;
  /** 기본 체크할지 */
  suggest: boolean;
}

export function MetaReader({ docs, destination, form, defaults, onApply }: {
  docs: PendingDoc[];
  destination?: AiDestination | null;
  /** 지금 양식 값(기간 제안은 지금 시작 달 기준) */
  form: Form;
  /** 빈 양식의 기본값 -- 이것과 다르면 사람이 적은 칸으로 보고 기본 체크하지 않는다 */
  defaults: Form;
  onApply: (patch: Partial<Form>) => void;
}) {
  const files = [...docs].filter((d) => readable(d.file))
    .sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
  const [whichName, setWhichName] = useState<string | null>(null);
  const target = files.find((d) => d.file.name === whichName) ?? files[0];
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MetaResult | null>(null);
  const [checked, setChecked] = useState<Set<Key>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const ctl = useRef<AbortController | null>(null);
  useEffect(() => () => ctl.current?.abort(), []);
  // 읽을 자료가 바뀌면 옛 결과를 지운다 -- 다른 자료의 값을 넣지 않게(폴백 리뷰 NIT 5)
  const targetKey = target ? `${target.file.name}:${target.file.size}:${target.kind}` : "";
  useEffect(() => {
    ctl.current?.abort();
    setBusy(false);
    setResult(null);
    setChecked(new Set());
  }, [targetKey]);

  if (files.length === 0) return null;

  const f = result?.fields;
  const months = f?.duration_months?.value ?? null;
  const proposals = result ? proposalsOf(result, form, defaults) : [];
  const info: [string, string, MetaField<unknown>][] = [];
  if (f?.client) info.push(["발주기관", f.client.value, f.client]);
  if (f?.notice_no) info.push(["공고 번호", f.notice_no.value, f.notice_no]);
  if (f?.deadline) info.push(["제출 마감", f.deadline.value, f.deadline]);
  if (f?.period) info.push(["계약 기간(원문)", f.period.value, f.period]);
  if (f?.budget && !months) info.push(["총 사업비", `${wonText(f.budget.value)} — 기간 개월을 몰라 월 예산은 직접 적습니다`, f.budget]);

  async function run() {
    const file = target.file;
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await readProjectMeta(file, c.signal);
      if (c.signal.aborted) return;
      setResult(r);
      setChecked(new Set(proposalsOf(r, form, defaults).filter((p) => p.suggest).map((p) => p.key)));
    } catch (e) {
      if (c.signal.aborted) return;
      setError((e as Error).message);
    } finally {
      if (ctl.current === c) setBusy(false);
    }
  }
  function stop() { ctl.current?.abort(); setBusy(false); }
  const toggle = (k: Key) => setChecked((prev) => { const n = new Set(prev); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  function apply() {
    onApply(Object.assign({}, ...proposals.filter((p) => checked.has(p.key)).map((p) => p.patch)));
    setChecked(new Set());
  }

  return (
    <section className="rounded-md border border-pine-200 bg-[#F6FAF8] px-3 py-2.5" aria-label="자료에서 사업 정보 채우기">
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-[13px]">자료에서 사업 정보 채우기(AI)</b>
        {files.length > 1 && (
          <select className="field w-auto py-0.5 text-xs" aria-label="정보를 읽을 자료" value={target.file.name}
                  onChange={(e) => setWhichName(e.target.value)}>
            {files.map((d, i) => <option key={`${d.file.name}-${i}`} value={d.file.name}>{d.kind} · {d.file.name}</option>)}
          </select>
        )}
        {busy ? (
          <>
            <Spinner label={(destination ?? "external") === "external" ? "읽는 중… (OpenAI 수 초)" : "읽는 중…"} />
            <button type="button" className="btn-ghost px-1.5 py-0.5 text-xs" onClick={stop}>멈추기</button>
          </>
        ) : (
          <button type="button" className="btn px-2.5 py-1 text-xs" onClick={() => void run()}>
            {result ? "다시 읽기" : files.length === 1 ? `${files[0].file.name}에서 읽기` : "읽기"}
          </button>
        )}
      </div>
      <p className={`mt-1 text-[12px] ${(destination ?? "external") === "external" ? "text-amber-800" : "text-slate-500"}`}
         data-destination={destination ?? "unknown"}>
        {DESTINATION[destination ?? "external"]} 사업명·기간·월 예산을 제안하고, 고른 것만 위 칸에 들어갑니다.</p>
      {error && <p role="alert" className="mt-1.5 text-[12.5px] text-red-700">{error}</p>}
      {result && (
        <div className="mt-2 space-y-2 text-[12.5px]">
          <p className="text-slate-600">
            {result.method === "ai"
              ? `AI가 읽었습니다(${result.model ?? "모델"}${result.usage?.latency_s ? ` · ${result.usage.latency_s.toFixed(1)}초` : ""}).`
              : `${fallbackText(result.fallback_reason)} AI 없이 자료의 정해진 말로 찾았습니다.`}
          </p>
          {result.warnings.map((w) => <p key={w} className="text-amber-800">{w}</p>)}
          {proposals.length === 0 ? <p className="text-slate-500">양식에 넣을 사업명·기간·예산을 자료에서 찾지 못했습니다. 위 칸에 직접 적습니다.</p> : (
            <ul className="space-y-1" aria-label="자료에서 읽은 사업 정보">
              {proposals.map((p) => (
                <li key={p.key} className="rounded border border-slate-200 bg-white px-2 py-1">
                  <label className="flex flex-wrap items-center gap-1.5">
                    <input type="checkbox" checked={checked.has(p.key)} onChange={() => toggle(p.key)} aria-label={`${p.label} 넣기`} />
                    <span className="text-slate-500">{p.label}</span><b>{p.shown}</b>
                    <Source field={p.field} />
                    {p.already && <span className="text-amber-800">이미 적음 — 체크하면 바꿉니다</span>}
                    {!p.already && !p.suggest && <span className="text-amber-800">확인 필요 — 맞으면 체크합니다</span>}
                  </label>
                  {p.note && <p className="mt-0.5 text-[11.5px] text-slate-600">{p.note}</p>}
                  <p className="mt-0.5 truncate text-[11.5px] text-slate-500" title={p.field.quote}>근거: “{p.field.quote}”</p>
                </li>
              ))}
            </ul>
          )}
          {!months && f?.period && <p className="text-amber-800">자료에서 기간 개월 수를 읽지 못했습니다 — 종료 달은 직접 고릅니다.</p>}
          {info.length > 0 && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-slate-600" aria-label="참고 정보">
              {info.map(([k, v, field]) => (
                <div key={k} className="contents">
                  <dt className="text-slate-500">{k}</dt>
                  <dd title={`근거: ${field.quote}`}>{v} <Source field={field} /></dd>
                </div>
              ))}
            </dl>
          )}
          {proposals.length > 0 && (
            <button type="button" className="btn-primary px-2.5 py-1 text-xs" disabled={checked.size === 0} onClick={apply}>
              고른 {checked.size}개 칸에 넣기</button>
          )}
        </div>
      )}
    </section>
  );
}

function Source({ field }: { field: MetaField<unknown> }) {
  const label = field.source !== "ai" ? "규칙" : field.computed ? "AI 계산" : "AI";
  const title = field.source !== "ai" ? "AI 없이 정해진 말로 찾음(확인 필요)"
    : field.computed ? "근거 문장에 개월 수가 없어 AI가 날짜로 센 값 — 서버가 확인하지 못했다" : "AI가 읽고 서버가 근거 문장을 원문에서 확인";
  return (
    <span className={`rounded-full px-1.5 text-[10.5px] ${label === "AI" ? "bg-pine-100 text-pine-800" : "bg-amber-50 text-amber-800"}`} title={title}>
      {label}</span>
  );
}

export function proposalsOf(result: MetaResult, form: Form, defaults: Form): Proposal[] {
  const f = result.fields;
  const months = f.duration_months?.value ?? null;
  const computed = !!f.duration_months?.computed;
  const start = /^\d{4}-\d{2}$/.test(form.start_month) ? form.start_month : null;
  const sure = (field: MetaField<unknown>) => field.source === "ai";
  const out: Proposal[] = [];
  const push = (p: Omit<Proposal, "suggest">, ok: boolean) => out.push({ ...p, suggest: ok && !p.already });
  if (f.name) push({ key: "name", label: "사업명", shown: f.name.value, patch: { name: f.name.value }, field: f.name,
                     note: f.name.unlabeled ? "자료의 이 문장에 사업명 표지(입찰건명·용역명 등)가 없어 확인이 필요합니다" : undefined,
                     already: !!form.name.trim() && form.name.trim() !== f.name.value }, sure(f.name) && !f.name.unlabeled);
  if (f.sector && (SECTORS as readonly string[]).includes(f.sector.value)) {
    push({ key: "sector", label: "고객 구분", shown: f.sector.value, patch: { sector: f.sector.value as Form["sector"] }, field: f.sector,
           already: form.sector !== defaults.sector && form.sector !== f.sector.value }, sure(f.sector));
  }
  if (f.duration_months && months && start) {
    const end = endMonth(start, months);
    push({ key: "end_month", label: "기간", shown: `${months}개월 → ${start} ~ ${end}`, patch: { end_month: end }, field: f.duration_months,
           note: (computed ? "자료에 개월 수가 없어 AI가 날짜로 셌습니다(서버가 확인하지 못함). " : "") +
                 "시작 달은 지금 양식 값 기준 — 시작 달을 먼저 바꾸면 종료 달도 그에 맞춰 제안합니다",
           already: form.end_month !== form.start_month && form.end_month !== end }, sure(f.duration_months) && !computed);
  }
  if (f.budget && months) {
    const pts = monthlyPoints(f.budget.value, months);
    const vat = f.budget.vat_included === true ? "(부가세 포함)" : f.budget.vat_included === false ? "(부가세 별도)" : "";
    push({ key: "monthly_budget", label: "월 예산", shown: `${pts.toLocaleString()}점`, patch: { monthly_budget: pts }, field: f.budget,
           note: `총 ${wonText(f.budget.value)}${vat} ÷ ${months}개월${computed ? "(AI가 센 기간)" : ""} = 월 ${pts.toLocaleString()}만 원 → ${pts.toLocaleString()}점 ` +
                 "(1점 = 1만 원 가정 — 가상 데이터 단가표 특급 1,600·고급 1,300·중급 1,000·초급 750점/월)",
           already: form.monthly_budget !== defaults.monthly_budget && form.monthly_budget !== pts },
         sure(f.budget) && !!f.duration_months && sure(f.duration_months) && !computed);
  }
  return out;
}
