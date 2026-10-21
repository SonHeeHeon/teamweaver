import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { DatasetChangedError, fetchSkillMap } from "../api/client";
import type { SkillMap, SkillMapRow } from "../api/types";
import { SkillRelation } from "./SkillRelation";
import { barOf, barScale, graphMode } from "./skillMapView";

/** 조직 기술 지도(지식 그래프 첫 번째 보기, claude-a core.kg.views.skill_map).
 *  "이 기술을 실무 수준으로 가진 사람이 몇 명이고 지금 비어 있는 사람은? 사업들은 몇 명을 요구하나?" */

type Filter = "all" | "demanded" | "gap" | "scarce";

const LEVEL_MONTHS = [12, 36, 60, 96];
const levelOf = (m: number | null) => (m == null ? 1 : 1 + LEVEL_MONTHS.filter((x) => m >= x).length);

function Detail({ row }: { row: SkillMapRow }) {
  return (
    <div className="grid gap-4 bg-slate-50 px-4 py-3 text-xs lg:grid-cols-2">
      <div>
        <p className="font-semibold text-slate-700">요구하는 사업</p>
        {row.demand.length === 0 ? <p className="text-slate-500">없음</p> : (
          <ul className="mt-1 space-y-0.5">
            {row.demand.map((d) => (
              <li key={d.project_id}>
                {d.proposal && <span className="mr-1 rounded bg-indigo-100 px-1 text-indigo-800">신규 제안</span>}
                {d.project}({d.project_id}) · {d.headcount}명 · 레벨 {levelOf(d.min_months)} 이상
                <span className="text-slate-500"> — 조건 맞는 사람 {d.qualified_people}명</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <p className="font-semibold text-slate-700">실무 가능한 사람 {row.practical}명</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {row.practical_people.map((p) => (
            <span key={p.id} title={p.credited ? "하위 기술 경력에서 일부 인정받아 실무 가능이 됐다" : undefined}
                  className={`rounded px-1.5 py-0.5 ${p.credited ? "border border-dashed border-amber-400 bg-amber-50 text-amber-900"
                                                                 : "bg-white text-slate-700 ring-1 ring-slate-200"}`}>
              {p.name ? `${p.name}(${p.id})` : p.id}{p.credited ? " · 인정" : ""}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export function SkillMapTab({ datasetVersion, onDatasetChanged }: { datasetVersion: string; onDatasetChanged: () => void }) {
  const [data, setData] = useState<SkillMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("demanded");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const onChanged = useRef(onDatasetChanged);
  useEffect(() => { onChanged.current = onDatasetChanged; });

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    fetchSkillMap(datasetVersion).then((d) => alive && setData(d)).catch((e) => {
      if (!alive) return;
      if (e instanceof DatasetChangedError) onChanged.current();          // 다른 사용자가 데이터를 바꿨다 -- 화면 전체를 새 데이터로
      else setError(e instanceof Error ? e.message : String(e));
    });
    return () => { alive = false; };
  }, [datasetVersion]);

  const rows = useMemo(() => (data?.rows ?? []).filter((r) =>
    (filter === "all" || (filter === "demanded" && r.demand_headcount > 0) || (filter === "gap" && r.proposal_gap > 0)
      || (filter === "scarce" && r.scarce))
    && (!query.trim() || r.skill.toLowerCase().includes(query.trim().toLowerCase()))), [data, filter, query]);

  const scale = barScale(rows);
  const graph = graphMode(data?.rows ?? []);
  if (error) return <p role="alert" className="text-sm text-slate-600">{error}</p>;
  if (!data) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  const s = data.summary;
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">조직 기술 지도</h2>
        <p className="text-sm text-slate-500">
          기술 {s.skills}개 · 사업이 요구하는 기술 {s.demanded}개 · 실무 가능 2명 이하인 희소 기술 {s.scarce}개 ·
          신규 제안에 지금 비어 있는 사람이 모자란 기술 {s.proposal_gap_skills}개
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {([["demanded", "요구 있는 기술"], ["gap", "신규 제안 부족"], ["scarce", "희소(2명 이하)"], ["all", "전체"]] as const).map(([k, label]) => (
          <label key={k} className="flex items-center gap-1">
            <input type="radio" name="skillmap-filter" checked={filter === k} onChange={() => setFilter(k)} />{label}
          </label>
        ))}
        <input aria-label="기술 검색" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="기술 이름"
               className="rounded-md border border-slate-300 px-2 py-1" />
      </div>
      <p className="mt-2 text-xs text-slate-500">
        회사가 가진 기술 인력(공급, 막대)과 사업이 요구하는 인원(수요)을 견줍니다. 연한 막대가 빨간 선에 못 미치면 그 기술 사람이 모자라고,
        진한 부분(지금 비어 있는 사람)이 주황 표시(▼ 신규 제안 필요)에 못 미치면 신규 제안을 쉬는 인력만으로 못 채웁니다(신규 제안 부족).
        눈금은 수요 쪽에 맞췄습니다 -- 실무 가능이 눈금을 넘으면 막대 끝을 자르고 ▸로 표시합니다.
      </p>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600">
        <span><span className="mr-1 inline-block h-2.5 w-3 rounded-sm bg-[#a9cbbb] align-middle" />실무 가능(12개월+)</span>
        <span><span className="mr-1 inline-block h-2.5 w-3 rounded-sm bg-pine-700 align-middle" />그중 지금 비어 있음</span>
        <span><span className="mr-1 inline-block h-3 w-0.5 bg-red-700 align-middle" />사업 필요 인원</span>
        <span><span className="mr-1 text-amber-600">▼</span>그중 신규 제안 필요</span>
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[48rem] text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="py-1 font-medium">기술</th>
              <th className="font-medium">공급(막대) vs 수요(선)</th>
              <th className="font-medium" title="실무 가능(숙련 · 하위 기술 인정) · 그중 지금 비어 있음 · 사업 필요 인원(요구 사업 수) · 공급/수요 배수">인원</th>
              <th className="font-medium" title="신규 제안 필요 인원 − 비어 있는 실무 가능 인원(투입률·가용률은 안 봄)">신규 제안 부족</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={5} className="py-2 text-xs text-slate-500">조건에 맞는 기술이 없다.</td></tr>}
            {rows.map((r) => {
              const b = barOf(r, scale);
              const title = `실무 가능 ${r.practical}명(그중 하위 기술 인정 ${r.practical_credited}명) · 숙련 ${r.expert}명 · 지금 비어 있음 ${r.practical_free}명`
                + ` · 요구 사업 ${r.demand_projects}개 · 필요 ${r.demand_headcount}명(신규 제안 ${r.proposal_headcount}명)`
                + (r.coverage == null ? "" : ` · 공급/수요 ${r.coverage.toFixed(2)}배`);
              return (
                <Fragment key={r.skill}>
                  <tr className={`border-t border-slate-100 ${r.scarce ? "bg-red-50" : ""}`}>
                    <td className="py-1.5 pr-2">
                      <span className="font-medium text-slate-900">{r.skill}</span>
                      {r.scarce && <span className="ml-1 rounded bg-red-600 px-1 text-[10px] text-white">희소</span>}
                      <span className="block text-[11px] text-slate-500">{r.category ?? "-"}</span>
                    </td>
                    <td title={title} className="pr-5">
                      <span role="img" className="relative mt-2 block h-3.5 w-[300px] rounded-sm bg-slate-100"
                            aria-label={`${r.skill} 공급 ${r.practical}명(비어 있음 ${r.practical_free}명), 필요 ${r.demand_headcount}명(신규 제안 ${r.proposal_headcount}명)`}>
                        <span className={`absolute left-0 top-0 h-3.5 bg-[#a9cbbb] ${b.overflow ? "rounded-l-sm" : "rounded-sm"}`} style={{ width: `${b.practical * 100}%` }} />
                        <span className="absolute left-0 top-0 h-3.5 rounded-l-sm bg-pine-700" style={{ width: `${b.free * 100}%` }} />
                        {b.overflow && <span className="absolute -right-3 top-[-2px] text-[11px] text-slate-500" data-mark="overflow">▸</span>}
                        {r.demand_headcount > 0 && <span className="absolute -top-1 h-[22px] w-[3px] -translate-x-1/2 bg-red-700" style={{ left: `${b.need * 100}%` }} data-mark="need" />}
                        {r.proposal_headcount > 0 && <span className="absolute -top-[13px] -translate-x-1/2 text-[10px] leading-none text-amber-600" style={{ left: `${b.proposal * 100}%` }} data-mark="proposal">▼</span>}
                      </span>
                    </td>
                    <td className="whitespace-nowrap text-xs text-slate-600" title={title}>
                      실무 {r.practical}<span className="text-slate-400">(숙련 {r.expert}{r.practical_credited ? ` · 인정 ${r.practical_credited}` : ""})</span>
                      {" · "}비어 있음 {r.practical_free} · 필요 {r.demand_headcount}<span className="text-slate-400">({r.demand_projects}개 사업)</span>
                      {r.coverage != null && <span className="text-slate-400"> · {r.coverage.toFixed(1)}배</span>}
                    </td>
                    <td className={`tabular-nums ${r.proposal_gap > 0 ? "font-semibold text-red-700" : "text-slate-400"}`}>
                      {r.proposal_gap ? `${r.proposal_gap}명 부족` : "-"}</td>
                    <td>
                      <button onClick={() => setOpen(open === r.skill ? null : r.skill)} aria-label={`${r.skill} 자세히`}
                              className="text-xs text-indigo-700 underline">{open === r.skill ? "접기" : "자세히"}</button>
                    </td>
                  </tr>
                  {open === r.skill && (
                    <tr><td colSpan={5}>
                      {/* 사람별 칸(claude-a skill_map, 2026-10-21 요청)이 오면 기술 관계 그림, 없으면 예전 목록 */}
                      {graph ? (
                        <div className="bg-slate-50 px-3 py-3">
                          <SkillRelation row={r} />
                          {/* 그림은 비어 있는 사람만 이름 -- 전원 이름·인정 표시는 목록으로(화면 낭독기도 읽는다, 폴백 리뷰 SHOULD) */}
                          <details className="mt-2"><summary className="cursor-pointer text-xs text-slate-600">목록으로 보기</summary><Detail row={r} /></details>
                        </div>
                      ) : <Detail row={r} />}
                    </td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500">{data.note} 점선 테두리 = 하위 기술 경력에서 일부 인정받아 실무 가능이 된 사람(본인이 적은 경력과 구분).</p>
    </section>
  );
}
