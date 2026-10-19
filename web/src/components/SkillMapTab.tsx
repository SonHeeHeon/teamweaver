import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { DatasetChangedError, fetchSkillMap } from "../api/client";
import type { SkillMap, SkillMapRow } from "../api/types";

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
        회사가 가진 기술 인력(공급)과 사업이 요구하는 인원(수요)을 비교합니다. "공급/수요"가 1배에 가까울수록 그 기술 사람이 모자랍니다.
        열 이름에 마우스를 올리면 뜻이 나옵니다.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[56rem] text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="py-1 font-medium">기술</th><th className="font-medium">분류</th>
              <th className="font-medium" title="경력 12개월 이상">실무 가능</th>
              <th className="font-medium" title="하위 기술 경력에서 일부 인정받아 실무 가능이 된 사람">그중 인정</th>
              <th className="font-medium" title="경력 36개월 이상">숙련</th>
              <th className="font-medium" title="지금 진행 사업에 들어가 있지 않은 실무 가능 인원">비어 있음</th>
              <th className="font-medium">요구 사업</th><th className="font-medium">필요 인원</th>
              <th className="font-medium" title="신규 제안 필요 인원 − 비어 있는 실무 가능 인원(투입률·가용률은 안 봄)">신규 제안 부족</th>
              <th className="font-medium" title="실무 가능 인원 ÷ 필요 인원">공급/수요</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={11} className="py-2 text-xs text-slate-500">조건에 맞는 기술이 없다.</td></tr>}
            {rows.map((r) => (
              <Fragment key={r.skill}>
                <tr className={`border-t border-slate-100 ${r.scarce ? "bg-red-50" : ""}`}>
                  <td className="py-1 font-medium text-slate-900">
                    {r.skill}{r.scarce && <span className="ml-1 rounded bg-red-600 px-1 text-[10px] text-white">희소</span>}
                  </td>
                  <td className="text-xs text-slate-500">{r.category ?? "-"}</td>
                  <td className="tabular-nums">{r.practical}</td>
                  <td className="tabular-nums text-amber-800">{r.practical_credited || "-"}</td>
                  <td className="tabular-nums">{r.expert}</td>
                  <td className="tabular-nums">{r.practical_free}</td>
                  <td className="tabular-nums">{r.demand_projects}</td>
                  <td className="tabular-nums">{r.demand_headcount}</td>
                  <td className={`tabular-nums ${r.proposal_gap > 0 ? "font-semibold text-red-700" : ""}`}>{r.proposal_gap || "-"}</td>
                  <td className="tabular-nums">{r.coverage == null ? "-" : `${r.coverage.toFixed(2)}배`}</td>
                  <td>
                    <button onClick={() => setOpen(open === r.skill ? null : r.skill)} aria-label={`${r.skill} 자세히`}
                            className="text-xs text-indigo-700 underline">{open === r.skill ? "접기" : "자세히"}</button>
                  </td>
                </tr>
                {open === r.skill && <tr><td colSpan={11}><Detail row={r} /></td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500">{data.note} 점선 테두리 = 하위 기술 경력에서 일부 인정받아 실무 가능이 된 사람(본인이 적은 경력과 구분).</p>
    </section>
  );
}
