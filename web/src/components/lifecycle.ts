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

const YM = /^(\d{4})-(\d{2})$/;
const abs = (ym: string | null | undefined): number | null => {
  const m = YM.exec(ym ?? "");
  return m ? Number(m[1]) * 12 + Number(m[2]) - 1 : null;
};

/** 사업 시작·끝의 절대 달 번호(연×12+월-1) -- 배치 명단 정렬용(2026-10-21 사용자 "시작 빠른 순인데 왜 j001부터").
 *  계획 밖까지 이어지는 사업은 원래 기간(period), 아니면 계획 시작 달 + 계획 안 순번. 계획 시작 달을 모르면 null --
 *  그때는 모든 사업을 계획 안 순번으로만 비교한다(원래 기간과 순번을 섞으면 순서가 틀린다: 예전에 글자로 섞어 비교해
 *  "0000000"이 "2024-02"보다 앞에 와서 계획 안에서만 도는 사업이 늘 맨 앞이었다). */
export function absoluteMonth(p: Project, which: "start" | "end", horizonStart: string | null | undefined): number | null {
  const base = abs(horizonStart);
  if (base === null) return null;
  return abs(p.period?.[which]) ?? base + (which === "start" ? p.start_month : p.end_month);
}
