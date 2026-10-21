import type { Cowork, Person, Project } from "../api/types";
import { RelationLegend } from "../components/RelationLegend";
import { DRAW_ORDER, lineStyle, toneOf, type Tone } from "../components/relationTone";

/** PDF 팀별 협업 지도(2026-10-20, 예전 StaticNetwork 대체).
 *
 *  예전 그림은 배치된 사람 전체와 그 사이 협업선을 힘 시뮬레이션으로 한 장에 그렸다. 시연 100명에서 선이
 *  약 4,000개라 실타래였고 팀도 안 보였다. 여기서는 사업마다 팀원을 원 위에 놓고(시뮬레이션 없이 결정적 --
 *  Playwright가 기다릴 것이 없다) 팀 안에서 예전에 함께 일한 쌍만 잇는다. 굵기 = 함께 일한 개월,
 *  색 = 서로 남긴 동료 평가(색·굵기 규칙은 화면 교체 검토 관계 그림과 같은 components/relationTone). 평가는 가상 데이터만 실린다(서버 _report_meta). */

export interface Tie { a: string; b: string; months: number; tone: Tone }
export interface TeamCard {
  id: string; name: string;
  members: { id: string; name: string; known: boolean }[];
  ties: Tie[]; pairs: number; avgMonths: number | null; bad: number;
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
const WIDE_W = 640;
export const WIDE_FROM = 13;                  // 13명부터는 한 줄을 다 쓴다(작은 원에서는 위아래 이름이 겹친다)

/** 사업 하나의 원 그림 카드. scale: 화면에서 크게 볼 때(인사팀 소명 "근거 한눈에", 2026-10-21) -- 같은 그림을 비율만 키운다(PDF는 1). */
export function TeamCircleCard({ card, scale = 1, fit = false }: { card: TeamCard; scale?: number;
  /** 화면: 카드 테두리가 그림 폭을 따라가게(가로 스크롤 안에서 그림 중간에 테두리가 끊기지 않게) -- PDF는 칸 폭 */
  fit?: boolean }) {
  const n = card.members.length, wide = n >= WIDE_FROM, w = wide ? WIDE_W : W;
  const R = n <= 1 ? 0 : n === 2 ? 44 : wide ? Math.min(150, 6 * n) : Math.min(60, Math.max(34, 22 + 5 * n));
  const H = n <= 1 ? 40 : n === 2 ? 34 : 2 * R + 40;                 // 위아래 이름 자리 + 꼬리 글과 띄우기
  const cx = w / 2, cy = n <= 1 ? 14 : n === 2 ? H / 2 : R + 18;
  // 두 명이면 좌우, 셋 이상이면 맨 위에서 시계 방향
  const angle = (i: number) => (n === 2 ? Math.PI * (1 - i) : -Math.PI / 2 + (2 * Math.PI * i) / n);
  const pos = new Map(card.members.map((m, i) => [m.id, { x: cx + R * Math.cos(angle(i)), y: cy + R * Math.sin(angle(i)), t: angle(i) }]));
  const ties = [...card.ties].sort((p, q) => DRAW_ORDER.indexOf(p.tone) - DRAW_ORDER.indexOf(q.tone));
  return (
    <div className={`rounded border border-slate-200 p-2${wide && !fit ? " col-span-3" : ""}${fit ? " w-max" : ""}`} style={{ breakInside: "avoid" }} data-testid={`team-${card.id}`}>
      <p className="flex justify-between gap-1 text-[11px]">
        <span className="truncate"><b>{card.id}</b> {card.name}</span>
        <span className="shrink-0 text-slate-500">{n}명</span>
      </p>
      <svg width={w * scale} height={H * scale} viewBox={`0 0 ${w} ${H}`} overflow="visible" role="img" aria-label={`${card.id} 팀 협업 관계`}>
        {ties.map((t) => {
          const a = pos.get(t.a)!, b = pos.get(t.b)!;
          return <line key={`${t.a}|${t.b}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} {...lineStyle(t.tone, t.months)} data-tone={t.tone} />;
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
      <RelationLegend hasReview={hasReview} extra={
        <span><svg width={10} height={10} className="mr-1 inline-block align-middle"><circle cx={5} cy={5} r={3.5} fill="#fff" stroke="#64748b" strokeWidth={1.5} /></svg>팀에 아는 사람 없음</span>} />
      <div className="mt-2 grid grid-cols-3 gap-2">
        {cards.map((c) => <TeamCircleCard key={c.id} card={c} />)}
      </div>
    </div>
  );
}
