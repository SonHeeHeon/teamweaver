/** 신규 사업 편성 후보 표·"넣기로 고른 사람" 줄의 글 규칙(2026-10-20 사용자 요청 2·4·5번, claude-b).
 *  - 정원 변화: 한 숫자(모든 사업 빈자리 합)가 아니라 사업별로 "이 사업 1자리 채움" · "J013에 1자리 빔".
 *  - 협업: 두 사람 사이 점수라 "누구와"를 함께 쓴다("이보람과 +0.17"). 익숙한 쌍 감점은 "(익숙한 쌍)". */
import type { ApplyHint, CandidateRow, PairTerm, SeatEffect } from "./workspaceApi";
import { signedPts } from "../components/scoreSplit";

export interface SeatLine { text: string; tone: "good" | "warn"; /** 사업 이름(마우스를 올리면) */ title?: string }

/** 사업별 정원 변화 → 글 줄. 대상 사업이 먼저 온다. 변화가 없으면 빈 목록. */
export function seatLines(effects: SeatEffect[] | undefined, targetId: string): SeatLine[] {
  const rows = [...(effects ?? [])].sort((a, b) => Number(b.project_id === targetId) - Number(a.project_id === targetId));
  return rows.map((e) => {
    const where = e.project_id === targetId ? "이 사업" : `${e.project_id}에`;
    return e.change > 0
      ? { text: `${where} ${e.change}자리 채움`, tone: "good", title: e.name }
      : { text: `${where} ${-e.change}자리 빔`, tone: "warn", title: e.name };
  });
}

/** 받침이 있으면 "과", 없으면(한글이 아닌 끝 글자 포함) "와". */
export function withGwa(name: string): string {
  const c = name.charCodeAt(name.length - 1) - 0xac00;
  return name + (c >= 0 && c <= 11171 && c % 28 !== 0 ? "과" : "와");
}

/** 한 쌍에 걸린 항들(협업 보상·익숙한 쌍 감점)을 한 글로: "+0.19", "−0.20(익숙한 쌍)", "+0.19·익숙한 쌍 −0.20". */
function termsText(ts: PairTerm[]): string {
  const syn = ts.find((t) => t.kind === "synergy");
  const fam = ts.find((t) => t.kind === "familiar");
  if (syn && fam) return `${signedPts(syn.value)}·익숙한 쌍 ${signedPts(fam.value)}`;
  const t = syn ?? fam;
  return t ? `${signedPts(t.value)}${t.kind === "familiar" ? "(익숙한 쌍)" : ""}` : "";
}

/** 후보 행의 협업 쌍 글(없으면 null) -- 사람마다 한 번("이보람과 +0.19·익숙한 쌍 −0.20"). */
export function partnersText(r: CandidateRow): string | null {
  const by = new Map<string, { name: string; terms: PairTerm[] }>();
  for (const p of r.partners ?? []) {
    const e = by.get(p.person_id) ?? { name: p.name, terms: [] };
    e.terms.push(p);
    by.set(p.person_id, e);
  }
  return by.size ? [...by.values()].map((e) => `${withGwa(e.name)} ${termsText(e.terms)}`).join(" · ") : null;
}

/** 팀 안의 쌍 목록을 쌍마다 한 번으로: "이보람·윤시현 +0.17 · 박서준·김은우 −0.50(익숙한 쌍)". */
export function duosText(pairs: (PairTerm & { a: string; a_name: string; b: string; b_name: string })[]): string {
  const by = new Map<string, { a: string; b: string; terms: PairTerm[] }>();
  for (const p of pairs) {
    const k = `${p.a}|${p.b}`;
    const e = by.get(k) ?? { a: p.a_name, b: p.b_name, terms: [] };
    e.terms.push(p);
    by.set(k, e);
  }
  return [...by.values()].map((e) => `${e.a}·${e.b} ${termsText(e.terms)}`).join(" · ");
}

export const VIOLATION_LABEL: Record<string, string> = {
  grade_over: "등급 정원 초과", budget: "예산 초과", availability: "투입 불가 달", concurrent_projects: "동시 수행 상한",
  alloc_range: "투입률 범위",
};

/** "넣으면 위반"의 이유(서버 new_violations = [코드, 위치]) -- 점검표 f4 "이유가 안 보인다". 모르는 코드는 코드 그대로. */
export function violationReasons(v: unknown[]): string {
  const codes = v.map((x) => (Array.isArray(x) ? String(x[0]) : String(x)));
  return [...new Set(codes.map((c) => VIOLATION_LABEL[c] ?? c))].join(", ");
}

/** 후보의 새 위반이 예산 초과뿐인가(2026-10-22 P3-2 -- 손 편성 확정은 사유와 함께 증액한다) */
export function budgetOnly(v: unknown[]): boolean {
  return v.length > 0 && v.every((x) => (Array.isArray(x) ? String(x[0]) : String(x)) === "budget");
}

/** 후보 행의 기술·협업 몫(예전 서버 응답이면 합친 값 하나만 기술 칸에). */
export function skillCollab(r: CandidateRow): { skill: number | undefined; collab: number | undefined } {
  if (typeof r.skill_delta === "number" && typeof r.collab_delta === "number") return { skill: r.skill_delta, collab: r.collab_delta };
  return { skill: r.delta_excl_unfilled ?? r.delta_total, collab: undefined };
}

/** 긴급 투입 확정 때 함께 늘어날 것(서버 apply_hint) 한 문장: "… 사업 정원 중급 +2 · 월 예산 +1,404 · 요구 기술 Kubernetes이(가) 늘어납니다(증원)." */
export function hintText(h: ApplyHint, lead: string): string {
  const hc = Object.values(h.headcount_add).flatMap((x) => Object.entries(x)).map(([g, c]) => `${g} +${c}`);
  const budget = Object.values(h.budget_add);
  const skills = Object.values(h.skills_add).flat().map((x) => x.name);
  const parts = [...(hc.length ? [`정원 ${hc.join(" · ")}`] : []),
                 ...(budget.length ? [`월 예산 +${budget.map((b) => b.toLocaleString()).join(", ")}`] : []),
                 ...(skills.length ? [`요구 기술 ${skills.join(", ")}`] : [])];
  // 등급 대체(2026-10-21): 빈자리를 다른 등급으로 메운다 -- 증원과 따로 적는다
  const swaps = Object.values(h.grade_swaps ?? {}).flat().map((s) => `${s.seat} 자리 ${s.count}개 → ${s.grade}`);
  const swapText = swaps.length ? ` 등급 대체: ${swaps.join(", ")}(사유 필요).` : "";
  return parts.length ? `${lead} 사업 ${parts.join(" · ")}이(가) 늘어납니다(증원).${swapText}`
    : swaps.length ? `${lead}${swapText}` : `${lead} 지금 정원·예산 안에서 들어갑니다.`;
}
