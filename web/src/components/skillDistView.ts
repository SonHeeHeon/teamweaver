/** 조직 기술 분포(2026-10-22 사용자 "우리 조직 인력들의 기술 분포 … 이런 기술에 대해 경력이 높구나, 이 기술에 강하겠구나")의 순수 계산.
 *  - 넓이 × 깊이: 넓이 = 실무 가능(12개월+) 인원, 깊이 = 실무 가능자 평균 경력. 두 축의 가운데 값(중앙값)으로 네 칸 --
 *    넓고 깊음(강점) · 좁고 깊음(소수 전문가) · 넓고 얕음(많이 해 봤지만 얕음) · 좁고 얕음.
 *  - 경력 띠: 보유자 경력 구간(1년 미만·1~3·3~5·5~8·8년+) 막대, 5년 이상 인원이 많은 순.
 *  서버(api/routes/skill_map)가 센 값만 쓴다 -- 사업 성과와 무관한 경력 개월 기준(NOT_CALIBRATED). */
import type { SkillMapRow } from "../api/types";

export const BANDS = ["1년 미만", "1~3년", "3~5년", "5~8년", "8년+"] as const;

export type Quadrant = "strong" | "expert" | "broad" | "weak";
export const QUADRANT_LABEL: Record<Quadrant, string> = {
  strong: "넓고 깊음 = 강점", expert: "소수 전문가(좁고 깊음)", broad: "많이 해 봤지만 얕음", weak: "좁고 얕음",
};

/** 분포를 그릴 수 있는 기술(실무 가능 1명 이상, 서버가 분포를 보냈음) */
export function distRows(rows: SkillMapRow[]): (SkillMapRow & { avg_months: number; levels: number[]; senior: number })[] {
  return rows.filter((r): r is SkillMapRow & { avg_months: number; levels: number[]; senior: number } =>
    r.practical > 0 && typeof r.avg_months === "number" && Array.isArray(r.levels) && typeof r.senior === "number");
}

export function median(xs: number[]): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b), m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** 가운데 값과 같으면 넓은·깊은 쪽으로 친다 */
export function quadrantOf(practical: number, avgMonths: number, xm: number, ym: number): Quadrant {
  const wide = practical >= xm, deep = avgMonths >= ym;
  return wide && deep ? "strong" : deep ? "expert" : wide ? "broad" : "weak";
}

/** 실무 가능 중 하위 기술 인정분 덕에 실무 가능이 된 사람이 절반 이상 -- 그림·요약에 "(인정)"·점선으로 구분(표와 같은 규칙, 사용자 결정 10-11) */
export const creditedMostly = (r: SkillMapRow) => r.practical > 0 && r.practical_credited / r.practical >= 0.5;   // 절반 이상

export interface Summary { xm: number; ym: number; count: number; groups: Record<Quadrant, string[]> }

/** 한 줄 결론용: 칸마다 기술 이름(5년+ 인원 많은 순, 같으면 실무 가능 많은 순). 인정분이 절반 넘는 기술은 "(인정)".
 *  칸은 **이 조직 기술끼리의 가운데 값** 기준(상대 비교) -- 조직 전체가 얕아도 몇 개는 "강점"이 된다(화면에 적는다). */
export function summarize(rows: SkillMapRow[]): Summary {
  const rs = distRows(rows);
  const xm = median(rs.map((r) => r.practical)), ym = median(rs.map((r) => r.avg_months));
  const groups: Record<Quadrant, string[]> = { strong: [], expert: [], broad: [], weak: [] };
  for (const r of [...rs].sort((a, b) => b.senior - a.senior || b.practical - a.practical || a.skill.localeCompare(b.skill)))
    groups[quadrantOf(r.practical, r.avg_months, xm, ym)].push(creditedMostly(r) ? `${r.skill}(인정)` : r.skill);
  return { xm, ym, count: rs.length, groups };
}

/** 눈금 간격: 1·2·5 × 10^k 중 눈금이 target개 안팎이 되는 것(20명 고정이면 3,000명에서 눈금 89개가 겹쳤다) */
export function niceStep(span: number, target = 8): number {
  if (!(span > 0)) return 1;
  const raw = span / target, p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? 10 * p;
}

/** 원 반지름: 5년+ 인원을 이 조직 최댓값으로 나눠 4~22px(규모가 커져도 원이 판을 덮지 않게) */
export const radiusOf = (senior: number, maxSenior: number) => 4 + 18 * Math.sqrt(maxSenior > 0 ? senior / maxSenior : 0);

/** 경력 띠 순서: 5년+ 많은 순 → 3년+ → 실무 가능 */
export function bandOrder(rows: SkillMapRow[]) {
  return [...distRows(rows)].sort((a, b) => b.senior - a.senior || b.expert - a.expert || b.practical - a.practical
                                              || a.skill.localeCompare(b.skill));
}

export interface Box { x: number; y: number; w: number; h: number }
const hit = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/** 이름표 자리: 중요한 기술(앞쪽)부터 원 오른쪽 → 왼쪽 → 위 → 아래 → 대각선 순으로 놓아 보고, 이미 놓인 이름표나 **자기 원**과 겹치면 다음 자리,
 *  모두 겹치면 이름표를 생략한다(마우스를 올리면 나온다). 다른 원 위에는 놓을 수 있다 -- 원이 몰린 곳(실무 30~40명·4.5년)의 강점 기술 이름이
 *  통째로 빠졌다. charW: 글자 하나 폭 어림(한국어는 넓게). */
export function placeLabels(points: { key: string; x: number; y: number; r: number; text: string }[],
                            bounds: { w: number; h: number; x0?: number; y0?: number; blocked?: Box[] },
                            charW = (ch: string) => (/[가-힣]/.test(ch) ? 11 : 6.4), lineH = 13): Map<string, { x: number; y: number; anchor: "start" | "end" | "middle" }> {
  const out = new Map<string, { x: number; y: number; anchor: "start" | "end" | "middle" }>();
  const placed: Box[] = [...(bounds.blocked ?? [])];          // 사분면 글자 같은 고정 글자 자리
  const x0 = bounds.x0 ?? 0, y0 = bounds.y0 ?? 0;             // 축 눈금 자리에는 놓지 않는다
  const circles: Box[] = points.map((p) => ({ x: p.x - p.r, y: p.y - p.r, w: 2 * p.r, h: 2 * p.r }));
  for (const p of points) {
    const w = [...p.text].reduce((s, ch) => s + charW(ch), 0);
    const tries: { box: Box; at: { x: number; y: number; anchor: "start" | "end" | "middle" } }[] = [
      { box: { x: p.x + p.r + 3, y: p.y - lineH / 2, w, h: lineH }, at: { x: p.x + p.r + 3, y: p.y + 4, anchor: "start" } },
      { box: { x: p.x - p.r - 3 - w, y: p.y - lineH / 2, w, h: lineH }, at: { x: p.x - p.r - 3, y: p.y + 4, anchor: "end" } },
      { box: { x: p.x - w / 2, y: p.y - p.r - 3 - lineH, w, h: lineH }, at: { x: p.x, y: p.y - p.r - 6, anchor: "middle" } },
      { box: { x: p.x - w / 2, y: p.y + p.r + 3, w, h: lineH }, at: { x: p.x, y: p.y + p.r + 14, anchor: "middle" } },
      { box: { x: p.x + p.r * 0.7, y: p.y - p.r - lineH, w, h: lineH }, at: { x: p.x + p.r * 0.7, y: p.y - p.r - 3, anchor: "start" } },
      { box: { x: p.x + p.r * 0.7, y: p.y + p.r, w, h: lineH }, at: { x: p.x + p.r * 0.7, y: p.y + p.r + 11, anchor: "start" } },
      { box: { x: p.x - p.r * 0.7 - w, y: p.y - p.r - lineH, w, h: lineH }, at: { x: p.x - p.r * 0.7, y: p.y - p.r - 3, anchor: "end" } },
      { box: { x: p.x - p.r * 0.7 - w, y: p.y + p.r, w, h: lineH }, at: { x: p.x - p.r * 0.7, y: p.y + p.r + 11, anchor: "end" } },
    ];
    const own = circles[points.indexOf(p)];
    const ok = tries.find((t) => t.box.x >= x0 && t.box.y >= y0 && t.box.x + t.box.w <= bounds.w && t.box.y + t.box.h <= bounds.h
      && !placed.some((b) => hit(b, t.box)) && !hit(own, t.box));
    if (ok) { placed.push(ok.box); out.set(p.key, ok.at); }
  }
  return out;
}
