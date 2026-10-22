import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminLoginRequiredError } from "../api/client";
import { Spinner } from "../ui";
import { PeopleTable, STATUS_LABEL, countLine, localTime } from "./ChangeReport";
import { downloadChangeReport, fetchChanges, type ChangeItem } from "./reviewApi";

/** 변경 보고서 탭(2026-10-22 사용자 "액션 하나하나에 대한 것도 보고서로 남길 수 있게하고 기간으로 잡고 어떤 변경들을 했는지 각각에 대한
 *  인사팀 보고 사항들을 정리해서 함께 pdf에 묶는것"). 기간을 고르면 그 안의 변경(확정·취소·되돌리기·등록·고정·동시 수행 등)이 나오고,
 *  한 건씩 또는 고른 것을 한 PDF로 묶는다. 확정에는 그 사업별 인사팀 검토 자료 전문이 함께 들어간다. */
type Preset = "today" | "week" | "month" | "all" | "custom";
const PRESET_LABEL: Record<Preset, string> = { today: "오늘", week: "최근 7일", month: "이번 달", all: "전체", custom: "직접" };

const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** 화면의 날짜(그 날 0시 ~ 끝 날 다음 날 0시, 이 컴퓨터 시간대) → 서버에 보낼 ISO 시각. */
export function periodOf(preset: Preset, from: string, to: string, now = new Date()): { start: string | null; end: string | null } {
  const day = (s: string) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const next = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (preset === "all") return { start: null, end: null };
  if (preset === "today") return { start: today.toISOString(), end: next(today).toISOString() };
  if (preset === "week") return { start: new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6).toISOString(), end: next(today).toISOString() };
  if (preset === "month") return { start: new Date(today.getFullYear(), today.getMonth(), 1).toISOString(), end: next(today).toISOString() };
  return { start: from ? day(from).toISOString() : null, end: to ? next(day(to)).toISOString() : null };
}

export function ChangeReportPanel({ adminToken = null, onLoginRequired }: {
  adminToken?: string | null;
  onLoginRequired?: (reason: string) => void;
}) {
  const [preset, setPreset] = useState<Preset>("week");
  const today = ymd(new Date());
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const period = useMemo(() => periodOf(preset, from, to), [preset, from, to]);
  const [items, setItems] = useState<ChangeItem[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** 만드는 중인 PDF(한 건이면 그 id, 묶음이면 "all") */
  const [making, setMaking] = useState<string | null>(null);

  const fail = useCallback((e: unknown) => {
    if (e instanceof AdminLoginRequiredError) { onLoginRequired?.(e.message); return; }
    setError(e instanceof Error ? e.message : String(e));
  }, [onLoginRequired]);
  useEffect(() => {
    setItems(null); setError(null);
    fetchChanges(period.start, period.end).then((xs) => {
      setItems(xs);
      setPicked(new Set(xs.map((x) => x.id)));          // 처음에는 기간 안 전부
    }).catch(fail);
  }, [period.start, period.end, fail]);

  const pdf = async (ids: string[], key: string) => {
    setMaking(key); setError(null); setNotice(null);
    try {
      await downloadChangeReport(ids, period.start, period.end, adminToken);
      setNotice(`변경 ${ids.length}건을 PDF로 내려받았습니다.`);
    } catch (e) { fail(e); } finally { setMaking(null); }
  };
  const order = (items ?? []).filter((x) => picked.has(x.id)).map((x) => x.id);
  const toggle = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <div>
      <p className="text-[13px] text-slate-500">
        확정·확정 취소·되돌리기·신규 사업 등록·고정·프로젝트 동시 수행 허용 같은 변경이 모두 지우지 않는 기록으로 남습니다.
        기간을 고르고 한 건씩, 또는 고른 변경을 한 PDF로 묶습니다. 확정에는 사업별 인사팀 검토 자료 전문이 함께 들어갑니다.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px]" role="group" aria-label="기간">
        {(Object.keys(PRESET_LABEL) as Preset[]).map((p) => (
          <button key={p} type="button" aria-pressed={preset === p}
                  className={preset === p ? "btn-primary px-2.5 py-1 text-xs" : "btn px-2.5 py-1 text-xs"} onClick={() => setPreset(p)}>
            {PRESET_LABEL[p]}
          </button>
        ))}
        {preset === "custom" && (
          <>
            <input type="date" aria-label="시작 날짜" className="field w-auto py-0.5" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
            <span>~</span>
            <input type="date" aria-label="끝 날짜" className="field w-auto py-0.5" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          </>
        )}
      </div>
      {error && <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-[13px] text-red-600">{error}</p>}
      {notice && <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{notice}</p>}
      {items === null ? <p className="mt-4 text-sm text-slate-500">불러오는 중…</p> : items.length === 0 ? (
        <p className="mt-4 rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-[13.5px] text-slate-500">
          이 기간에 변경이 없습니다.
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-slate-600">
            <span>변경 {items.length}건 · 고름 {order.length}건</span>
            <span className="flex gap-2">
              <button type="button" className="underline" onClick={() => setPicked(new Set(items.map((x) => x.id)))}>모두 고르기</button>
              <button type="button" className="underline" onClick={() => setPicked(new Set())}>모두 빼기</button>
            </span>
          </div>
          <ul className="mt-2 space-y-2">
            {items.map((it) => (
              <li key={it.id} className={`rounded-lg border px-3 py-2.5 ${it.status ? "border-slate-200 bg-slate-50" : "border-line"}`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <label className="flex min-w-0 flex-1 items-start gap-2 text-[13px]">
                    <input type="checkbox" className="mt-1" checked={picked.has(it.id)} onChange={() => toggle(it.id)}
                           aria-label={`${it.label} ${localTime(it.at)} 고르기`} />
                    <span className="min-w-0">
                      <b>{it.label}</b>
                      <span className="ml-1 text-slate-500">{localTime(it.at)}{it.by ? ` · ${it.by}` : ""}</span>
                      {it.status && <span className="ml-2 rounded-full bg-red-100 px-2 text-[11px] text-red-700">{STATUS_LABEL[it.status]}</span>}
                      <span className="block text-slate-700">{it.note}</span>
                      <span className="block text-[12px] text-slate-500">{countLine(it.counts)}
                        {it.review.length > 0 && ` · 검토 자료 ${it.review.filter((r) => r.exists).length}/${it.review.length}편`
                          + (it.review.some((r) => r.objections) ? ` · 이의 ${it.review.reduce((s, r) => s + r.objections, 0)}건` : "")}
                      </span>
                    </span>
                  </label>
                  <button type="button" className="btn px-2.5 py-1 text-xs" disabled={making !== null}
                          onClick={() => void pdf([it.id], it.id)}>
                    {making === it.id ? <Spinner status={false} label="만드는 중…" /> : "이 건 PDF"}
                  </button>
                </div>
                {(it.reasons.length > 0 || it.counts.moved + it.counts.joined + it.counts.left > 0) && (
                  <details className="mt-1.5 pl-6 text-[12.5px]">
                    <summary className="cursor-pointer select-none text-slate-600">자세히(사람·사유)</summary>
                    <div className="mt-1.5"><PeopleTable item={it} /></div>
                    {it.reasons.length > 0 && <ul className="mt-1.5 list-disc pl-5">{it.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
                    {it.review.length > 0 && (
                      <p className="mt-1.5 text-slate-600">검토 자료: {it.review.map((r) =>
                        `${r.project_id} ${r.exists ? (r.status === "exported" ? "내보냄" : "작성 중") : "만들지 않음"}`).join(" · ")}</p>
                    )}
                  </details>
                )}
              </li>
            ))}
          </ul>
          <div className="sticky bottom-0 mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-line bg-paper py-3">
            <span className="text-[12px] text-slate-500">묶음은 보고·열람용입니다(검토 자료의 "내보냄" 표시는 그대로).</span>
            <button type="button" className="btn-primary" disabled={making !== null || order.length === 0}
                    onClick={() => void pdf(order, "all")}>
              {making === "all" ? <Spinner status={false} label="PDF 만드는 중…" /> : `고른 ${order.length}건 한 PDF로`}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
