import type { AssignEntry, Cowork, Person, Project } from "../api/types";
import { ProjectCode } from "../ui";
import { RelationLegend } from "./RelationLegend";
import { DRAW_ORDER, TONE, lineStyle, reviewWord, toneOf, type Tone } from "./relationTone";
import type { SwapPick } from "./SwapControl";

const GRADE_SHORT: Record<string, string> = { 특급: "특", 고급: "고", 중급: "중", 초급: "초" };

interface Link { member: string; side: "out" | "in"; months: number; tone: Tone; review: number | null }

/** 관계 그림(2026-10-21 사용자 "더 직관적으로 볼 수 있게 지식그래프로"): 가운데 세로로 그 사업 팀원, 왼쪽에 빠질 사람,
 *  오른쪽에 들어올 사람. 함께 일한 팀원과 선으로 잇는다(굵기 = 개월, 색 = 서로 남긴 평가 -- PDF 팀별 협업 지도와 같은 규칙).
 *  왼쪽 선 묶음 = 빠지면 잃는 관계, 오른쪽 = 들어오면 생기는 관계. 원형 배치는 두 사람을 나란히 비교하기 어려워 쓰지 않았다. */
const GW = 460, NODE_R = 24, SIDE_X = 62, PILL_W = 78, PILL_H = 20, TOP = 30, BOTTOM = 22;

function RelationGraph({ jid, team, links, out, inn, from, name, grade }: {
  jid: string; team: string[]; links: Link[]; out: string; inn: string | null; from: string | null;
  name: (id: string) => string; grade: (id: string) => string;
}) {
  const known = new Set(links.map((l) => l.member));
  const order = [...team.filter((t) => known.has(t)), ...team.filter((t) => !known.has(t))];   // 아무와도 모르는 팀원은 아래로
  const n = order.length, row = n > 14 ? 22 : 30;
  const H = Math.max(TOP + n * row + BOTTOM, TOP + 2 * NODE_R + 44 + BOTTOM);
  const colTop = TOP + (H - TOP - BOTTOM - n * row) / 2;
  const cy = TOP + (H - TOP - BOTTOM) / 2, mid = GW / 2;
  const yOf = new Map(order.map((t, i) => [t, colTop + row / 2 + i * row]));
  const ends = (l: Link) => {
    const y = yOf.get(l.member)!;
    return l.side === "out"
      ? { x0: SIDE_X + NODE_R, x1: mid - PILL_W / 2, y }
      : { x0: GW - SIDE_X - NODE_R, x1: mid + PILL_W / 2, y };
  };
  const drawn = [...links].sort((p, q) => DRAW_ORDER.indexOf(p.tone) - DRAW_ORDER.indexOf(q.tone));
  const node = (side: "out" | "in", pid: string | null) => {
    const x = side === "out" ? SIDE_X : GW - SIDE_X;
    const head = side === "out" ? "빠질 사람" : "들어올 사람";
    return (
      <g data-node={side}>
        <text x={x} y={cy - NODE_R - 8} fontSize={11} fill="#64748b" textAnchor="middle">{head}</text>
        {pid ? (
          <>
            <circle cx={x} cy={cy} r={NODE_R} fill={side === "out" ? "#f1f5f9" : "#f0f9ff"}
                    stroke={side === "out" ? "#475569" : "#0284c7"} strokeWidth={2} />
            <text x={x} y={cy + 4} fontSize={12} fontWeight={600} fill="#0f172a" textAnchor="middle">{name(pid)}</text>
            <text x={x} y={cy + NODE_R + 14} fontSize={10} fill="#64748b" textAnchor="middle">
              {grade(pid)}{side === "in" && from ? ` · ${from}에서` : ""}
            </text>
          </>
        ) : (
          <>
            <circle cx={x} cy={cy} r={NODE_R} fill="#fff" stroke="#cbd5e1" strokeWidth={1.5} strokeDasharray="4 3" />
            <text x={x} y={cy + 4} fontSize={13} fill="#94a3b8" textAnchor="middle">?</text>
            <text x={x} y={cy + NODE_R + 14} fontSize={10} fill="#94a3b8" textAnchor="middle">고르면 보입니다</text>
          </>
        )}
      </g>
    );
  };
  return (
    <svg width="100%" viewBox={`0 0 ${GW} ${H}`} role="img" className="mt-2 block max-w-[560px]"
         aria-label={`빠질 사람·들어올 사람과 ${jid} 팀원의 관계 그림`}>
      <text x={mid} y={16} fontSize={11} fill="#64748b" textAnchor="middle">{jid} 팀원 {n}명</text>
      {drawn.map((l) => {
        const { x0, x1, y } = ends(l);
        const who = l.side === "out" ? out : inn ?? "";
        return (
          <line key={`${l.side}|${l.member}`} x1={x0} y1={cy} x2={x1} y2={y} {...lineStyle(l.tone, l.months)}
                data-side={l.side} data-member={l.member} data-tone={l.tone}>
            <title>{`${name(who)} — ${name(l.member)}: 함께 ${l.months}개월${reviewWord(l.review) ? ` · ${reviewWord(l.review)}` : ""}`}</title>
          </line>
        );
      })}
      {/* 개월 글은 선 위, 팀원 쪽 가까이(같은 쪽 선은 한 점에서 퍼지므로 줄마다 떨어진다) -- 흰 테두리로 선 위에서도 읽히게 */}
      {links.map((l) => {
        const { x0, x1, y } = ends(l);
        const t = 0.72;
        return (
          <text key={`m|${l.side}|${l.member}`} x={x0 + t * (x1 - x0)} y={cy + t * (y - cy) + 3.5} fontSize={10}
                textAnchor="middle" fill={l.tone === "bad" ? TONE.bad.stroke : "#334155"}
                stroke="#fff" strokeWidth={3} paintOrder="stroke">{l.months}개월</text>
        );
      })}
      {order.map((t) => {
        const y = yOf.get(t)!, k = known.has(t);
        return (
          <g key={t} data-member={t} data-known={k}>
            <rect x={mid - PILL_W / 2} y={y - PILL_H / 2} width={PILL_W} height={PILL_H} rx={PILL_H / 2}
                  fill="#fff" stroke={k ? "#94a3b8" : "#cbd5e1"} strokeDasharray={k ? undefined : "3 2"} />
            <text x={mid} y={y + 4} fontSize={12} fill={k ? "#0f172a" : "#94a3b8"} textAnchor="middle">
              {name(t)}<tspan fontSize={10} fill="#94a3b8"> {GRADE_SHORT[grade(t)] ?? grade(t)}</tspan>
            </text>
          </g>
        );
      })}
      {node("out", out)}
      {node("in", inn)}
    </svg>
  );
}

/** 교체 검토 오른쪽(2026-10-20 사용자 "작은 목록으로" → 10-21 "지식그래프로"): 빠질 사람·들어올 사람과 그 사업 팀원 사이의 관계만.
 *  예전 전 직원 협업 관계망(회색 원 100개)은 무엇을 보여 주는지 알기 어려워 뺐다. 위는 관계 그림, 숫자 목록은 "목록으로 보기"에. */
export function TeamRelations({ people, projects, coworks, entries, pick }: {
  people: Person[]; projects: Project[]; coworks: Cowork[]; entries: AssignEntry[]; pick: SwapPick | null;
}) {
  const byId = new Map(people.map((p) => [p.id, p]));
  const name = (id: string) => byId.get(id)?.name ?? id;
  const grade = (id: string) => byId.get(id)?.grade ?? "";
  const jName = new Map(projects.map((j) => [j.id, j.name]));
  const rel = new Map<string, Cowork>();
  for (const c of coworks) rel.set(`${c.a_id}|${c.b_id}`, c), rel.set(`${c.b_id}|${c.a_id}`, c);
  const jid = pick?.projectId ?? null;
  const team = jid ? [...new Set(entries.filter((e) => e.project_id === jid && e.person_id !== pick?.outPersonId).map((e) => e.person_id))] : [];
  const rows = (pid: string | null | undefined) => pid ? team.filter((t) => t !== pid).map((t) => ({ t, c: rel.get(`${pid}|${t}`) })) : [];
  const links: Link[] = (["out", "in"] as const).flatMap((side) => {
    const pid = side === "out" ? pick?.outPersonId : pick?.inPersonId;
    return rows(pid).filter((r) => r.c && r.c.co_months > 0)
      .map((r) => ({ member: r.t, side, months: r.c!.co_months, tone: toneOf(r.c!.review), review: r.c!.review ?? null }));
  });
  const summary = (side: "out" | "in", pid: string | null | undefined) => {
    if (!pid) return null;
    const ls = links.filter((l) => l.side === side);
    const bad = ls.filter((l) => l.tone === "bad").length;
    const avg = ls.length ? Math.round(ls.reduce((s, l) => s + l.months, 0) / ls.length) : null;
    return (
      <li data-summary={side}>
        <span className="font-medium text-slate-800">{side === "out" ? "빠질 사람" : "들어올 사람"} {name(pid)}</span>
        : 팀원 {team.length}명 중 {ls.length}명과 함께 일함{avg !== null && ` · 평균 ${avg}개월`}
        {bad > 0 && <span className="text-red-700"> · 평가 나쁨 {bad}명</span>}
      </li>
    );
  };
  const block = (title: string, pid: string | null | undefined) => {
    if (!pid) return null;
    const list = rows(pid);
    const known = list.filter((r) => r.c);
    return (
      <div>
        <p className="text-[13px] font-medium text-slate-800">{title}: {name(pid)}</p>
        {list.length === 0 ? <p className="text-[12px] text-slate-500">이 사업에 다른 팀원이 없습니다.</p> : (
          <ul className="mt-1 space-y-0.5 text-[12.5px]">
            {known.map(({ t, c }) => (
              <li key={t} className="flex justify-between gap-2">
                <span>{name(t)}</span>
                <span className="text-slate-600">함께 {c!.co_months}개월{reviewWord(c!.review) ? ` · ${reviewWord(c!.review)}` : ""}</span>
              </li>
            ))}
            {list.length > known.length && (
              <li className="text-slate-500">함께 일한 적 없음: {list.filter((r) => !r.c).map((r) => name(r.t)).join(", ")}</li>
            )}
          </ul>
        )}
      </div>
    );
  };
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="팀원과의 관계">
      <h3 className="text-sm font-semibold text-slate-900">팀원과의 관계</h3>
      <p className="mt-0.5 text-[12.5px] text-slate-500">
        교체할 사람들이 {jid ? <><ProjectCode id={jid} name={jName.get(jid)} /> 팀원들과</> : "그 사업 팀원들과"} 함께 일한 기간과 서로 남긴 동료 평가입니다.
        왼쪽 선은 빠지면 잃는 관계, 오른쪽 선은 들어오면 생기는 관계입니다. 오래 함께했고 평가가 좋으면 협업 점수가 올라갑니다.
      </p>
      {!pick?.outPersonId || !jid ? (
        <p className="mt-3 text-[12.5px] text-slate-500">왼쪽에서 교체 대상을 고르면 보입니다.</p>
      ) : team.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-slate-500">이 사업에 다른 팀원이 없습니다.</p>
      ) : (
        <>
          <RelationGraph jid={jid} team={team} links={links} out={pick.outPersonId} inn={pick.inPersonId ?? null}
                         from={pick.from} name={name} grade={grade} />
          <RelationLegend hasReview={links.some((l) => l.tone !== "none")} extra={
            <span><svg width={18} height={10} className="mr-1 inline-block align-middle"><rect x={1} y={1} width={16} height={8} rx={4} fill="#fff" stroke="#cbd5e1" strokeDasharray="3 2" /></svg>두 사람 모두 함께 일한 적 없는 팀원</span>} />
          <ul className="mt-2 space-y-0.5 text-[12.5px] text-slate-600">
            {summary("out", pick.outPersonId)}
            {pick.inPersonId ? summary("in", pick.inPersonId) : <li className="text-slate-500">들어올 사람을 고르면 오른쪽에 함께 보입니다.</li>}
          </ul>
          <details className="mt-2">
            <summary className="cursor-pointer text-[12.5px] text-slate-600">목록으로 보기</summary>
            <div className="mt-2 space-y-3">
              {block("빠질 사람", pick.outPersonId)}
              {block(pick.from ? `들어올 사람(${pick.from}에서)` : "들어올 사람", pick.inPersonId)}
            </div>
          </details>
        </>
      )}
    </section>
  );
}
