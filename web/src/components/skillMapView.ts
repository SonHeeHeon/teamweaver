/** 조직 기술 지도 그림의 순수 계산(2026-10-21 사용자 "조직 기술지도에도 지식 그래프" -- 제안 ②).
 *  - 공급/수요 막대: 실무 가능(그중 비어 있음) vs 사업 필요 인원·신규 제안 필요 인원. 눈금은 수요 쪽(필요·신규 제안·비어 있음)에 맞춘다 --
 *    실무 가능이 가장 많은 기술(91명)에 맞추면 "1명 부족"이 몇 px로 눌렸다(폴백 리뷰 SHOULD). 실무 가능이 눈금을 넘으면 끝을 자르고 "넘침".
 *  - 기술 관계 그림: 요구 사업 ↔ 기술(상위·하위 기술) ↔ 사람(비어 있음은 이름, 배치 중은 사업별 묶음).
 *    사람별 칸(free·on·expert·credited_from)과 계보는 claude-a `core.kg.views.skill_map` 칸이다(요청 2026-10-21) -- 없으면 그림을 그리지 않는다. */
import type { SkillMapRow, SkillPerson } from "../api/types";

/** 막대 눈금: 보이는 행의 수요 쪽(필요·신규 제안·비어 있음) 최댓값 × 1.5(최소 4) -- 같은 눈금이라 행끼리 견줄 수 있다 */
export function barScale(rows: SkillMapRow[]): number {
  const m = Math.max(0, ...rows.map((r) => Math.max(r.demand_headcount, r.proposal_headcount, r.practical_free)));
  return Math.max(4, Math.ceil(m * 1.5));
}

export interface Bar { practical: number; free: number; need: number; proposal: number; overflow: boolean }

/** 행 하나의 막대(눈금 대비 비율 0~1). 실무 가능이 눈금을 넘으면 overflow */
export function barOf(r: SkillMapRow, scale: number): Bar {
  const f = (n: number) => Math.min(1, n / scale);
  return { practical: f(r.practical), free: f(r.practical_free), need: f(r.demand_headcount), proposal: f(r.proposal_headcount),
           overflow: r.practical > scale };
}

/** 그림을 그릴 칸이 왔는가(claude-a skill_map 칸) -- 데이터 단위로 본다(실무 가능 0명인 행도 같은 모양으로, 폴백 리뷰 NIT) */
export const graphMode = (rows: SkillMapRow[]) => rows.some((r) => r.practical_people.some((p) => typeof p.free === "boolean"));

/** 인정 출처 -- 표와 같은 판정(`credited`: 인정분 덕에 실무 가능이 된 사람)일 때만(폴백 리뷰 SHOULD: 원본 출처만 보면 표 숫자와 어긋났다) */
const creditOf = (p: SkillPerson) => (p.credited ? p.credited_from?.[0] ?? null : null);

/** "Spring Boot 41개월의 49% 인정" */
export function creditText(c: { skill: string; months: number | null; ratio: number | null }): string {
  // 기술 이름만 자른다 -- 문장을 통째로 자르면 비율·"인정"이 잘렸다(폴백 리뷰 NIT)
  return `${clip(c.skill, 14)}${c.months != null ? ` ${c.months}개월` : ""}의 ${c.ratio != null ? `${Math.round(c.ratio * 100)}%` : "일부"} 인정`;
}

const clip = (t: string, n: number) => (t.length > n ? `${t.slice(0, n)}…` : t);

export interface RelationModel {
  demands: SkillMapRow["demand"]; moreDemands: number;
  free: { id: string; label: string; expert: boolean; credited: boolean; credit: string | null }[]; moreFree: number;
  busy: { project: string; count: number; expert: number; credited: number }[]; moreBusy: number;
  broader: string[]; narrower: string[];
  /** 가운데 원 글자(길면 두 줄) */
  lines: string[];
  gap: { need: number; free: number; short: number } | null;
}

const MAX_DEMANDS = 5, MAX_FREE = 6, MAX_BUSY = 6;

/** 관계 그림 재료. 요구 사업은 신규 제안을 먼저, 비어 있는 사람은 숙련·경력 순, 배치 중인 사람은 지금 사업별로 센다. */
export function relationModel(r: SkillMapRow): RelationModel {
  const demandsAll = [...r.demand].sort((a, b) => Number(b.proposal) - Number(a.proposal) || b.headcount - a.headcount
                                                  || a.project_id.localeCompare(b.project_id));
  const freeAll = r.practical_people.filter((p) => p.free)
    .sort((a, b) => Number(!!b.expert) - Number(!!a.expert) || (b.months ?? 0) - (a.months ?? 0) || a.id.localeCompare(b.id));
  const groups = new Map<string, SkillPerson[]>();
  for (const p of r.practical_people.filter((x) => x.free !== true)) {
    // 칸이 없는 사람(free 없음)은 사업을 모른다고 따로 묶는다 -- 배치 중으로 세지 않는다(폴백 리뷰 NIT)
    const j = p.free === false ? p.on?.[0] ?? "(사업 모름)" : "(정보 없음)";
    groups.set(j, [...(groups.get(j) ?? []), p]);
  }
  const busyAll = [...groups.entries()].map(([project, ps]) => ({
    project, count: ps.length, expert: ps.filter((p) => p.expert).length, credited: ps.filter((p) => p.credited).length,
  })).sort((a, b) => b.count - a.count || a.project.localeCompare(b.project));
  const label = (p: SkillPerson) => `${clip(p.name ?? p.id, 10)}${p.months != null ? ` · ${p.months}개월` : ""}`;
  return {
    demands: demandsAll.slice(0, MAX_DEMANDS), moreDemands: Math.max(0, demandsAll.length - MAX_DEMANDS),
    free: freeAll.slice(0, MAX_FREE).map((p) => {
      const c = creditOf(p);
      return { id: p.id, label: label(p), expert: !!p.expert,
               credited: p.credited, credit: c ? creditText(c) : null };
    }),
    moreFree: Math.max(0, freeAll.length - MAX_FREE),
    busy: busyAll.slice(0, MAX_BUSY), moreBusy: busyAll.slice(MAX_BUSY).reduce((s, g) => s + g.count, 0),
    broader: r.broader ?? [],
    narrower: (r.narrower ?? []).map((n) => (typeof n === "string" ? n : `${n.skill}${n.practical != null ? `(실무 ${n.practical})` : ""}`)),
    lines: centerLines(r.skill),
    gap: r.proposal_headcount > 0 ? { need: r.proposal_headcount, free: r.practical_free, short: r.proposal_gap } : null,
  };
}

/** 가운데 원(지름 108)에 들어가게: 9자 넘으면 가운데에 가까운 띄어쓰기에서 두 줄, 띄어쓰기가 없으면 반으로. 줄마다 12자에서 자른다 */
export function centerLines(skill: string): string[] {
  if (skill.length <= 9) return [skill];
  const mid = skill.length / 2;
  const spaces = [...skill].map((ch, i) => (ch === " " ? i : -1)).filter((i) => i > 0);
  // 띄어쓰기 → 없으면 '·'·'(' 앞 → 없으면 반(한국어 이름 "접근통제·인증(IAM)"이 단어 중간에서 갈리지 않게)
  const marks = [...skill].map((ch, i) => ("·(".includes(ch) ? i : -1)).filter((i) => i > 0);
  const near = (xs: number[]) => xs.reduce((a, b) => (Math.abs(b - mid) < Math.abs(a - mid) ? b : a));
  const cut = spaces.length ? near(spaces) : marks.length ? near(marks) : Math.ceil(mid);
  return [skill.slice(0, cut).trim(), skill.slice(cut).trim()].map((t) => clip(t, 12));
}
