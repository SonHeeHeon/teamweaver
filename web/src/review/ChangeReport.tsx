import { ReviewDoc } from "./ReviewDoc";
import type { ChangeItem, ChangePerson, ReviewDocView } from "./reviewApi";

/** 변경 보고서 PDF 한 묶음(2026-10-22 사용자 "기간으로 잡고 어떤 변경들을 했는지 각각에 대한 인사팀 보고 사항들을 정리해서 함께 pdf에 묶는것",
 *  서버 api/routes/changes.report_payload). 표지(기간·요약·목차) → 변경마다(무엇이 바뀌었나·사유·인사팀 검토 자료 전문). */
export interface ChangeReportData {
  kind: "changes";
  generated_at: string;
  period: { start: string | null; end: string | null };
  items: (ChangeItem & { review_docs: { project_id: string; name?: string; doc: ReviewDocView | null }[] })[];
  totals: {
    changes: number; by_kind: Record<string, number>; moved: number; joined: number; left: number;
    review_docs: number; review_missing: number; objections: number; undone_or_cancelled: number;
  };
}

export const STATUS_LABEL = { undone: "되돌림", cancelled: "확정 취소됨" } as const;

export const localTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("ko-KR") : "-");
const localDay = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString("ko-KR") : null);

/** "옮김 2 · 새로 1 · 빠짐 0" 같은 한 줄(0은 뺀다). */
export function countLine(c: ChangeItem["counts"]): string {
  const parts = [["옮김", c.moved], ["새로", c.joined], ["빠짐", c.left]].filter(([, n]) => Number(n) > 0).map(([k, n]) => `${k} ${n}명`);
  return parts.length ? parts.join(" · ") : "배치 변화 없음";
}

function where(ids: string[], projects: Record<string, string>): string {
  return ids.length ? ids.map((j) => (projects[j] && projects[j] !== j ? `${j} ${projects[j]}` : j)).join(", ") : "-";
}

export function PeopleTable({ item }: { item: ChangeItem }) {
  const rows: [string, ChangePerson][] = [
    ...item.people.moved.map((p) => ["옮김", p] as [string, ChangePerson]),
    ...item.people.joined.map((p) => ["새로", p] as [string, ChangePerson]),
    ...item.people.left.map((p) => ["빠짐", p] as [string, ChangePerson]),
  ];
  if (!rows.length) return <p className="text-[12.5px] text-slate-500">사람의 배치는 바뀌지 않았습니다.</p>;
  return (
    <table className="w-full border-collapse text-[12px]">
      <thead><tr className="border-b border-slate-300 text-left text-slate-500">
        <th className="py-1 pr-2 font-normal">구분</th><th className="py-1 pr-2 font-normal">사람</th>
        <th className="py-1 pr-2 font-normal">전</th><th className="py-1 font-normal">후</th></tr></thead>
      <tbody>
        {rows.map(([k, p]) => (
          <tr key={`${k}-${p.person_id}`} className="border-b border-slate-100 align-top">
            <td className="py-1 pr-2">{k}</td>
            <td className="py-1 pr-2">{p.name ?? p.person_id}{p.grade ? ` (${p.grade})` : ""}</td>
            <td className="py-1 pr-2">{where(p.from, item.projects)}</td>
            <td className="py-1">{where(p.to, item.projects)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Cover({ data }: { data: ChangeReportData }) {
  const t = data.totals;
  const from = localDay(data.period.start), to = data.period.end ? localDay(new Date(new Date(data.period.end).getTime() - 1).toISOString()) : null;
  return (
    <section>
      <p className="text-xs text-slate-500">인력 배치 변경 보고서 · {localTime(data.generated_at)} 출력</p>
      <h1 className="mt-1 text-2xl font-bold">인력 배치 변경 보고서</h1>
      <p className="mt-1 text-[13px] text-slate-600">
        기간 {from || to ? `${from ?? "처음"} ~ ${to ?? "지금"}` : "고른 변경"} · 변경 {t.changes}건
      </p>
      <table className="mt-4 w-full border-collapse text-[12.5px]">
        <tbody>
          <tr className="border-b border-slate-200"><th className="w-40 py-1.5 text-left font-normal text-slate-500">종류별</th>
            <td>{Object.entries(t.by_kind).map(([k, n]) => `${k} ${n}건`).join(" · ")}</td></tr>
          <tr className="border-b border-slate-200"><th className="py-1.5 text-left font-normal text-slate-500">사람 변화(유효한 변경)</th>
            <td>옮김 {t.moved}명 · 새로 {t.joined}명 · 빠짐 {t.left}명{t.undone_or_cancelled ? ` (되돌리거나 취소한 변경 ${t.undone_or_cancelled}건 제외)` : ""}</td></tr>
          <tr className="border-b border-slate-200"><th className="py-1.5 text-left font-normal text-slate-500">인사팀 검토 자료</th>
            <td>{t.review_docs}편{t.review_missing ? ` (만들지 않음 ${t.review_missing}편)` : ""} · 이의 {t.objections}건</td></tr>
        </tbody>
      </table>
      <h2 className="mt-5 text-[14px] font-semibold">목차</h2>
      <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-[12.5px]">
        {data.items.map((it) => (
          <li key={it.id}>
            {localTime(it.at)} · <b>{it.label}</b> · {it.note}{it.status ? ` · ${STATUS_LABEL[it.status]}` : ""}
          </li>
        ))}
      </ol>
      <p className="mt-4 text-[11.5px] text-slate-500">
        계산 숫자는 같은 평가 기준의 계산상 비교이며 실제 성과를 보장하지 않는다(NOT_CALIBRATED). 이 묶음은 열람·보고용이며 결재 첨부는 검토 자료별 PDF를 쓴다.
      </p>
    </section>
  );
}

export function ChangeReport({ data }: { data: ChangeReportData }) {
  return (
    <div className="change-report">
      <Cover data={data} />
      {data.items.map((it, n) => (
        <section key={it.id} className="mt-8" style={{ breakBefore: "page" }} aria-label={`변경 ${n + 1}`}>
          <header className="border-b-2 border-slate-800 pb-1.5">
            <h2 className="text-lg font-bold">{n + 1}. {it.label}</h2>
            <p className="text-[12.5px] text-slate-600">
              {localTime(it.at)} · {it.note}{it.by ? ` · 실행 ${it.by}` : ""}{it.dataset_id ? ` · 데이터 ${it.dataset_id}` : ""}
            </p>
          </header>
          {it.status && (
            <p className="mt-2 rounded bg-red-50 px-3 py-1.5 text-[12.5px] text-red-700">
              이 변경은 뒤에 {it.status === "undone" ? "되돌렸습니다" : "확정 취소했습니다"} — 지금 배치에는 남아 있지 않습니다.
            </p>
          )}
          {it.backfilled && <p className="mt-2 text-[11.5px] text-slate-500">이 기능 전 이력에서 옮긴 기록이라 사람 이름이 없습니다.</p>}
          <h3 className="mt-3 text-[13.5px] font-semibold">무엇이 바뀌었나 <span className="font-normal text-slate-500">({countLine(it.counts)})</span></h3>
          <div className="mt-1"><PeopleTable item={it} /></div>
          {it.reasons.length > 0 && (
            <>
              <h3 className="mt-3 text-[13.5px] font-semibold">사유·결정</h3>
              <ul className="mt-1 list-disc pl-5 text-[12.5px]">{it.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </>
          )}
          {it.review_kind && (
            <>
              <h3 className="mt-3 text-[13.5px] font-semibold">인사팀 보고 사항 — 사업별 검토 자료 {it.review_docs.length}편</h3>
              {it.review_docs.length === 0 && <p className="text-[12.5px] text-slate-500">사람이 새로 들어간 사업이 없습니다.</p>}
              {it.review_docs.map((d) => d.doc ? (
                <div key={d.project_id} className="mt-4" style={{ breakBefore: "page" }}>
                  <ReviewDoc doc={d.doc} editable={false} />
                </div>
              ) : (
                <p key={d.project_id} className="mt-2 rounded border border-dashed border-amber-400 px-3 py-2 text-[12.5px] text-amber-800">
                  {d.name ?? d.project_id}({d.project_id}): 검토 자료를 만들지 않았습니다 — 그 확정이 지금 판일 때 "검토 자료" 화면에서 만들 수 있습니다.
                </p>
              ))}
            </>
          )}
        </section>
      ))}
    </div>
  );
}
