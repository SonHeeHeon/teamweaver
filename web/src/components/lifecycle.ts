/** 사업 수명 주기 표시(2026-10-19, 사용자 "24년에 시작해서 아직 진행중인 플젝 … 추가 과제가 있어 6달만 회사 인력은 2명만 추가(외주는 여러명)").
 *  서버 api/lifecycle이 만든 기록(단계·실제 시작·추가 과제 상위·외주)과 계획 밖까지 이어지는 원래 기간을 보인다 -- 계산에는 쓰지 않는다. */
import type { Lifecycle, Project } from "../api/types";
import { monthLabel } from "./allocFormat";

/** "2024-06" → "24.6"(형식이 다르면 그대로). */
export function shortMonth(ym: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})$/.exec(ym ?? "");
  return m ? `${m[1].slice(2)}.${Number(m[2])}` : (ym ?? "");
}

/** 꼬리표 글: 단계(이름 머리말 "[운영]"과 같으면 생략 -- 같은 말을 두 번 쓰지 않는다), 추가 과제의 상위 사업·외주 인원(참고). */
export function lifecycleTags(lc: Lifecycle | null | undefined, name: string): string[] {
  if (!lc) return [];
  const out: string[] = [];
  if (!name.trimStart().startsWith(`[${lc.stage}]`)) out.push(lc.stage);
  if (lc.parent) out.push(`상위 ${lc.parent}`);
  if (lc.parent && (lc.external_headcount ?? 0) > 0) out.push(`외주 ${lc.external_headcount}명(참고)`);
  return out;
}

/** 사업 기간: 계획 밖까지 이어지는 사업은 원래 기간과 계획 안 기간을 함께("24.6~28.1월 · 계획 안 26.10~27.3월"), 아니면 계획 안 기간만. */
export function projectPeriod(p: Project, start: string | null | undefined): string {
  if (!start) return "";
  const inPlan = p.start_month === p.end_month ? monthLabel(p.start_month, start)
    : `${monthLabel(p.start_month, start).slice(0, -1)}~${monthLabel(p.end_month, start)}`;
  return p.period ? `${shortMonth(p.period.start)}~${shortMonth(p.period.end)}월 · 계획 안 ${inPlan}` : inPlan;
}
