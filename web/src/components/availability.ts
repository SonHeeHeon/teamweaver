/** 투입할 수 없는 인력(2026-10-19 사용자 지시): 그 달에 육아휴직 등으로 가용률이 0이면 그 달이 걸린 사업에는 넣을 수 없다.
 *  화면은 이런 사람을 보이되 고를 수 없게 하고 사유를 빨간 글씨로 보인다. 서버 api/unavailable.py와 같은 문장이다. */
import type { Person } from "../api/types";
import { monthLabel } from "./allocFormat";

/** months 중 가용률 0인 달. 가용률 정보가 없으면(예전 서버) 없음. */
export function blockedMonths(p: Person | undefined, months: number[]): number[] {
  const a = p?.availability;
  if (!a) return [];
  return months.filter((m) => m >= 0 && m < a.length && a[m] <= 0);
}

/** 사유 "육아휴직 — 27.1~27.9월 투입 불가". 막힌 달이 없으면 null. 한 달 휴가는 사업이 자체 감당해 가용률을 깎지 않는다. */
export function blockedReason(p: Person | undefined, months: number[], start?: string | null): string | null {
  const b = blockedMonths(p, months);
  if (!b.length) return null;
  const kind = p?.leave?.kind && p.leave.kind !== "한 달 휴가" ? p.leave.kind : "가용률 0%";
  const first = monthLabel(b[0], start), last = monthLabel(b[b.length - 1], start);
  return `${kind} — ${b.length === 1 ? first : `${first.slice(0, -1)}~${last}`} 투입 불가`;
}

/** 계획 기간 전체 달 번호. */
export function allMonths(p: Person | undefined): number[] {
  return Array.from({ length: p?.availability?.length ?? 0 }, (_, k) => k);
}
