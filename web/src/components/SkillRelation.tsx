import type { SkillMapRow } from "../api/types";
import { relationModel } from "./skillMapView";

/** 기술 관계 그림(2026-10-21 사용자 "조직 기술지도에도 지식 그래프" -- 제안 ②): 왼쪽 이 기술을 요구하는 사업,
 *  가운데 기술(위 상위 기술 · 아래 하위 기술), 오른쪽 실무 가능한 사람 -- 지금 비어 있는 사람은 이름(진하게), 배치 중인 사람은 사업별로 센다.
 *  숙련(36개월+)은 굵은 테두리, 하위 기술 경력에서 인정받은 사람은 점선. 신규 제안 칸이 있으면 "필요 − 비어 있음" 결론. */

const W = 1000, LX = 10, LW = 330, RX = 640, RW = 350, CX = 495, CR = 54;
const DEM_H = 40, DEM_GAP = 8, PILL_H = 24, PILL_GAP = 6;
const ellipsis = (t: string, n: number) => (t.length > n ? `${t.slice(0, n)}…` : t);

export function SkillRelation({ row, projectName }: { row: SkillMapRow; projectName?: (id: string) => string | undefined }) {
  const m = relationModel(row);
  const top = 30 + (m.broader.length ? 44 : 0);
  const rightItems = m.free.length + (m.moreFree ? 1 : 0) + m.busy.length + (m.moreBusy ? 1 : 0);
  const leftH = m.demands.length * (DEM_H + DEM_GAP) + (m.moreDemands ? 22 : 0);
  const rightH = rightItems * (PILL_H + PILL_GAP);
  const bodyH = Math.max(leftH, rightH, 2 * CR + 20);
  const H = top + bodyH + (m.narrower.length ? 56 : 12);
  const cy = top + bodyH / 2;
  const leftTop = top + (bodyH - leftH) / 2, rightTop = top + (bodyH - rightH) / 2;
  let ry = rightTop;
  const next = () => { const y = ry; ry += PILL_H + PILL_GAP; return y; };
  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${row.skill} 기술 관계`} className="max-w-[1000px]">
        <text x={LX} y={18} fontSize={12} fill="#64748b">이 기술을 요구하는 사업</text>
        <text x={RX} y={18} fontSize={12} fill="#64748b">실무 가능한 사람(12개월+)</text>
        {m.broader.length > 0 && (
          <g data-node="broader">
            <rect x={CX - 120} y={30} width={240} height={28} rx={14} fill="#fff" stroke="#94a3b8" strokeDasharray="4 3" />
            <text x={CX} y={48} fontSize={12} fill="#334155" textAnchor="middle">상위 기술: {ellipsis(m.broader.join(", "), 24)}</text>
            <line x1={CX} y1={58} x2={CX} y2={cy - CR} stroke="#94a3b8" strokeDasharray="4 3" />
          </g>
        )}
        {m.demands.map((d, i) => {
          const y = leftTop + i * (DEM_H + DEM_GAP);
          const name = projectName?.(d.project_id) ?? d.project;
          return (
            <g key={d.project_id} data-node="demand">
              <rect x={LX} y={y} width={LW} height={DEM_H} rx={8} fill={d.proposal ? "#fff7e6" : "#eef2ef"} stroke={d.proposal ? "#f59e0b" : "#dfe5e0"} />
              <text x={LX + 12} y={y + 16} fontSize={12} fontWeight={700} fill="#0f2620">{d.project_id}{d.proposal ? " · 신규 제안" : ""}</text>
              <text x={LX + 12} y={y + 32} fontSize={11} fill="#475569">
                {name.length > 17 ? `${name.slice(0, 17)}…` : name} · {d.headcount}명 · 조건 맞는 {d.qualified_people}명
              </text>
              <line x1={LX + LW} y1={y + DEM_H / 2} x2={CX - CR} y2={cy} stroke={d.proposal ? "#f59e0b" : "#b8cfc4"} strokeWidth={2} />
            </g>
          );
        })}
        {m.moreDemands > 0 && <text x={LX + 12} y={leftTop + m.demands.length * (DEM_H + DEM_GAP) + 14} fontSize={11.5} fill="#64748b">
          … 이 기술을 요구하는 사업 {m.moreDemands}개 더</text>}
        <circle cx={CX} cy={cy} r={CR} fill="#1f5f4a" />
        <text x={CX} y={cy - (m.lines.length > 1 ? 12 : 3)} fontSize={12} fontWeight={700} fill="#fff" textAnchor="middle">
          {m.lines.map((t, i) => <tspan key={i} x={CX} dy={i ? 15 : 0}>{t}</tspan>)}
        </text>
        <text x={CX} y={cy + (m.lines.length > 1 ? 24 : 15)} fontSize={11} fill="#cfe3da" textAnchor="middle">실무 {row.practical} · 숙련 {row.expert}</text>
        {m.demands.length === 0 && <text x={LX + 12} y={cy + 4} fontSize={12} fill="#94a3b8">요구하는 사업 없음</text>}
        {rightItems === 0 && <text x={RX + 12} y={cy + 4} fontSize={12} fill="#94a3b8">실무 가능한 사람 없음</text>}
        {m.narrower.length > 0 && (
          <g data-node="narrower">
            <line x1={CX} y1={cy + CR} x2={CX} y2={H - 44} stroke="#94a3b8" strokeDasharray="4 3" />
            <rect x={CX - 150} y={H - 44} width={300} height={28} rx={14} fill="#fff" stroke="#94a3b8" strokeDasharray="4 3" />
            <text x={CX} y={H - 26} fontSize={12} fill="#334155" textAnchor="middle">
              하위 기술: {ellipsis(m.narrower.slice(0, 2).join(", "), 28)}{m.narrower.length > 2 ? ` 외 ${m.narrower.length - 2}개` : ""}</text>
          </g>
        )}
        {m.free.map((p) => {
          const y = next();
          return (
            <g key={p.id} data-node="free">
              <line x1={CX + CR} y1={cy} x2={RX} y2={y + PILL_H / 2} stroke="#1f5f4a" strokeWidth={2.2} />
              <rect x={RX} y={y} width={RW} height={PILL_H} rx={12} fill="#1f5f4a"
                    stroke={p.credited ? "#d97706" : "#0f2620"} strokeWidth={p.expert ? 3 : 1.2} strokeDasharray={p.credited ? "4 3" : undefined} />
              <text x={RX + 12} y={y + 16} fontSize={11.5} fontWeight={600} fill="#fff">
                {p.label} · 비어 있음{p.expert ? " · 숙련" : ""}{p.credit ? ` · ← ${p.credit}` : p.credited ? " · 인정" : ""}</text>
            </g>
          );
        })}
        {m.moreFree > 0 && <text x={RX + 12} y={next() + 16} fontSize={11.5} fill="#1f5f4a">… 비어 있는 사람 {m.moreFree}명 더</text>}
        {m.busy.map((g) => {
          const y = next();
          return (
            <g key={g.project} data-node="busy">
              <line x1={CX + CR} y1={cy} x2={RX} y2={y + PILL_H / 2} stroke="#cbd5e1" strokeWidth={1.5} />
              <rect x={RX} y={y} width={RW} height={PILL_H} rx={12} fill="#f1f5f2" stroke="#dfe5e0" />
              <text x={RX + 12} y={y + 16} fontSize={11.5} fill="#475569">
                {g.project}에 있음 · {g.count}명(숙련 {g.expert}{g.credited ? ` · 인정 ${g.credited}` : ""})</text>
            </g>
          );
        })}
        {m.moreBusy > 0 && <text x={RX + 12} y={next() + 16} fontSize={11.5} fill="#64748b">… 다른 사업에 {m.moreBusy}명 더</text>}
      </svg>
      {m.gap && (
        <p className="mt-1 rounded-md border border-emerald-100 bg-emerald-50/60 px-3 py-2 text-xs text-slate-700">
          신규 제안이 요구하는 {m.gap.need}명 중 지금 비어 있는 실무 가능자는 {m.gap.free}명 →{" "}
          {m.gap.short > 0
            ? <b className="text-red-700">{m.gap.short}명 부족 — 진행 사업에서 데려오거나(빼 오기) 자격 조건을 다시 봐야 합니다.</b>
            : <b className="text-emerald-700">충분</b>}
          <span className="ml-1 text-slate-500">(투입률·가용률·예산은 보지 않은 어림 -- 실제 배치는 계산이 정합니다)</span>
        </p>
      )}
    </div>
  );
}
