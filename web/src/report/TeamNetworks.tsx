import type { Cowork, Person, Project } from "../api/types";
import { reviewWord } from "../components/TeamRelations";

/** PDF 팀별 협업 지도(2026-10-20, 예전 StaticNetwork 대체).
 *
 *  예전 그림은 배치된 사람 전체와 그 사이 협업선을 힘 시뮬레이션으로 한 장에 그렸다. 시연 100명에서 선이
 *  약 4,000개라 실타래였고 팀도 안 보였다. 여기서는 사업마다 팀원을 원 위에 놓고(시뮬레이션 없이 결정적 --
 *  Playwright가 기다릴 것이 없다) 팀 안에서 예전에 함께 일한 쌍만 잇는다. 굵기 = 함께 일한 개월,
 *  색 = 서로 남긴 동료 평가(경계는 화면 관계 목록 reviewWord와 같다). 평가는 가상 데이터만 실린다(서버 _report_meta). */

const TONE: Record<string, { stroke: string; dash?: string; label: string }> = {
  good: { stroke: "#059669", label: "평가 좋음" },
  mid: { stroke: "#94a3b8", label: "평가 보통" },
  bad: { stroke: "#dc2626", label: "평가 나쁨" },
  none: { stroke: "#cbd5e1", dash: "3 2", label: "평가 없음" },
};
const DRAW_ORDER = ["none", "mid", "good", "bad"];   // 나쁨이 맨 위에 보이게
const LEGEND_ORDER = ["good", "mid", "bad", "none"];

export interface Tie { a: string; b: string; months: number; tone: keyof typeof TONE }
export interface TeamCard {
  id: string; name: string;
  members: { id: string; name: string; known: boolean }[];
  ties: Tie[]; pairs: number; avgMonths: number | null; bad: number;
}

function toneOf(r: number | null | undefined): Tie["tone"] {
  const w = reviewWord(r);
  return w === "평가 좋음" ? "good" : w === "평가 나쁨" ? "bad" : w ? "mid" : "none";
}

/** 사업마다 팀원·팀 안 협업선·요약. 사업 코드 순(배치 명단 표와 같은 순서). */
export function teamCards(entries: { person_id: string; project_id: string }[], people: Person[],
                          projects: Project[], coworks: Cowork[]): TeamCard[] {
  const name = new Map(people.map((p) => [p.id, p.name]));
  const jName = new Map(projects.map((j) => [j.id, j.name]));
  const rel = new Map<string, Cowork>();
  for (const c of coworks) rel.set(`${c.a_id}|${c.b_id}`, c), rel.set(`${c.b_id}|${c.a_id}`, c);
  const teams = new Map<string, string[]>();
  for (const e of entries) {
    const t = teams.get(e.project_id) ?? [];
    if (!t.includes(e.person_id)) t.push(e.person_id);
    teams.set(e.project_id, t);
  }
  return [...teams.keys()].sort().map((jid) => {
    const ids = teams.get(jid)!;
    const ties: Tie[] = [];
    for (let i = 0; i < ids.length; i++)
      for (let k = i + 1; k < ids.length; k++) {
        const c = rel.get(`${ids[i]}|${ids[k]}`);
        if (c && c.co_months > 0) ties.push({ a: ids[i], b: ids[k], months: c.co_months, tone: toneOf(c.review) });
      }
    const known = new Set(ties.flatMap((t) => [t.a, t.b]));
    return {
      id: jid, name: jName.get(jid) ?? jid,
      members: ids.map((id) => ({ id, name: name.get(id) ?? id, known: known.has(id) })),
      ties, pairs: (ids.length * (ids.length - 1)) / 2,
      avgMonths: ties.length ? ties.reduce((s, t) => s + t.months, 0) / ties.length : null,
      bad: ties.filter((t) => t.tone === "bad").length,
    };
  });
}

const W = 200;                                       // PDF 본문 폭 3열 카드 안쪽
const WIDE_W = 640, WIDE_FROM = 13;                  // 13명부터는 한 줄을 다 쓴다(작은 원에서는 위아래 이름이 겹친다)
const strokeOf = (months: number) => 1 + Math.min(months, 30) / 10;   // 6개월 1.6 · 12개월 2.2 · 24개월 3.4

function Card({ card }: { card: TeamCard }) {
  const n = card.members.length, wide = n >= WIDE_FROM, w = wide ? WIDE_W : W;
  const R = n <= 1 ? 0 : n === 2 ? 44 : wide ? Math.min(150, 6 * n) : Math.min(60, Math.max(34, 22 + 5 * n));
  const H = n <= 1 ? 40 : n === 2 ? 34 : 2 * R + 40;                 // 위아래 이름 자리 + 꼬리 글과 띄우기
  const cx = w / 2, cy = n <= 1 ? 14 : n === 2 ? H / 2 : R + 18;
  // 두 명이면 좌우, 셋 이상이면 맨 위에서 시계 방향
  const angle = (i: number) => (n === 2 ? Math.PI * (1 - i) : -Math.PI / 2 + (2 * Math.PI * i) / n);
  const pos = new Map(card.members.map((m, i) => [m.id, { x: cx + R * Math.cos(angle(i)), y: cy + R * Math.sin(angle(i)), t: angle(i) }]));
  const ties = [...card.ties].sort((p, q) => DRAW_ORDER.indexOf(p.tone) - DRAW_ORDER.indexOf(q.tone));
  return (
    <div className={`rounded border border-slate-200 p-2${wide ? " col-span-3" : ""}`} style={{ breakInside: "avoid" }} data-testid={`team-${card.id}`}>
      <p className="flex justify-between gap-1 text-[11px]">
        <span className="truncate"><b>{card.id}</b> {card.name}</span>
        <span className="shrink-0 text-slate-500">{n}명</span>
      </p>
      <svg width={w} height={H} viewBox={`0 0 ${w} ${H}`} overflow="visible" role="img" aria-label={`${card.id} 팀 협업 관계`}>
        {ties.map((t) => {
          const a = pos.get(t.a)!, b = pos.get(t.b)!, tone = TONE[t.tone], sw = strokeOf(t.months);
          // 점선은 굵기에 맞춰 마디를 늘리고 끝을 자른다(둥근 끝이면 굵은 점선이 실선처럼 메워진다 -- PDF 실측)
          return <line key={`${t.a}|${t.b}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={tone.stroke}
                       strokeWidth={sw} strokeDasharray={tone.dash ? `${Math.max(3, sw * 1.6)} ${Math.max(2, sw * 1.3)}` : undefined}
                       strokeLinecap={tone.dash ? "butt" : "round"} data-tone={t.tone} />;
        })}
        {card.members.map((m) => {
          const p = pos.get(m.id)!;
          const c = Math.cos(p.t), s = Math.sin(p.t);
          const anchor = n <= 1 ? "middle" : c > 0.3 ? "start" : c < -0.3 ? "end" : "middle";
          const lx = n <= 1 ? p.x : p.x + 7 * c, ly = n <= 1 ? p.y + 16 : p.y + 7 * s + (s > 0.3 ? 8 : s < -0.3 ? -1 : 3);
          return (
            <g key={m.id}>
              <circle cx={p.x} cy={p.y} r={4.5} fill={m.known || n <= 1 ? "#334155" : "#ffffff"}
                      stroke={m.known || n <= 1 ? "none" : "#64748b"} strokeWidth={1.5} data-known={m.known} />
              <text x={lx} y={ly} fontSize={9} fill="#1e293b" textAnchor={anchor}>{m.name}</text>
            </g>
          );
        })}
      </svg>
      <p className="text-[10px] text-slate-600">
        {n <= 1 ? "혼자 배치" : <>아는 사이 {card.ties.length}/{card.pairs}쌍
          {card.avgMonths !== null && ` · 평균 ${Math.round(card.avgMonths)}개월`}
          {card.members.some((m) => !m.known) && ` · 아는 사람 없음 ${card.members.filter((m) => !m.known).length}명`}
          {card.bad > 0 && <span className="text-red-700"> · 평가 나쁨 {card.bad}쌍</span>}</>}
      </p>
    </div>
  );
}

function LegendLine({ stroke, width = 2, dash }: { stroke: string; width?: number; dash?: string }) {
  return <svg width={22} height={8} className="mr-1 inline-block align-middle"><line x1={1} y1={4} x2={21} y2={4} stroke={stroke} strokeWidth={width} strokeDasharray={dash} /></svg>;
}

export function TeamNetworks({ entries, people, projects, coworks }: {
  entries: { person_id: string; project_id: string }[]; people: Person[]; projects: Project[]; coworks: Cowork[];
}) {
  const cards = teamCards(entries, people, projects, coworks);
  if (cards.length === 0) return <p className="text-sm text-slate-500">배치된 사람이 없다.</p>;
  const pairs = cards.reduce((s, c) => s + c.pairs, 0), tied = cards.reduce((s, c) => s + c.ties.length, 0);
  const badCards = cards.filter((c) => c.bad > 0);
  const hasReview = cards.some((c) => c.ties.some((t) => t.tone !== "none"));
  return (
    <div>
      <p className="text-xs text-slate-600">
        사업마다 배치된 사람들이 예전에 함께 일한 관계다. 선이 굵을수록 오래 함께했고, 색은 두 사람이 서로 남긴 동료 평가다.
        협업 점수는 이 관계로 매긴다. 다른 사업 사람과의 관계는 그리지 않았다.
      </p>
      <p className="mt-1 text-xs text-slate-800" data-testid="team-network-summary">
        팀 안 {pairs}쌍 중 {tied}쌍이 예전에 함께 일한 사이다
        {hasReview && (badCards.length > 0
          ? <span className="text-red-700"> · 평가 나쁜 쌍이 있는 사업: {badCards.map((c) => `${c.id}(${c.bad}쌍)`).join(", ")}</span>
          : " · 평가 나쁜 쌍은 없다")}
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-slate-600">
        <span><LegendLine stroke="#64748b" width={strokeOf(6)} />6개월</span>
        <span><LegendLine stroke="#64748b" width={strokeOf(12)} />12개월</span>
        <span><LegendLine stroke="#64748b" width={strokeOf(24)} />24개월</span>
        {hasReview
          ? LEGEND_ORDER.map((k) => <span key={k}><LegendLine stroke={TONE[k].stroke} dash={TONE[k].dash && "4 3"} />{TONE[k].label}</span>)
          : <span>선 색 없음: 팀 안 쌍에 실린 동료 평가가 없다(실제 인사 자료는 평가 내용을 싣지 않는다).</span>}
        <span><svg width={10} height={10} className="mr-1 inline-block align-middle"><circle cx={5} cy={5} r={3.5} fill="#fff" stroke="#64748b" strokeWidth={1.5} /></svg>팀에 아는 사람 없음</span>
      </p>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {cards.map((c) => <Card key={c.id} card={c} />)}
      </div>
    </div>
  );
}
