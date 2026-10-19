/** 후보 점수 변화 나눠 보이기(2026-10-19, 사용자 "점수 변화는 나눠 보이도록 하자").
 *  빈자리 하나를 메우면 합계에 빈자리 감점(자리당 100점)이 통째로 더해져 "+101"처럼 보이고 사람끼리의 차이(1~3점)가 묻혔다.
 *  서버(api/routes/scenarios.split_unfilled)가 메운 자리 수와 그 밖의 변화(기술 적합·협업 시너지·익숙한 쌍 감점)를 따로 준다.
 *  순서는 여전히 합계(delta_total)로 정한다 -- 보이는 방식만 나눈다. */

export interface ScoreSplit {
  delta_total?: number;
  /** 메운 자리 수(생기면 음수) */
  unfilled_seats_delta?: number;
  /** 빈자리 몫을 뺀 점수 변화 */
  delta_excl_unfilled?: number;
}

export const signedPts = (x: number | undefined | null) =>
  x === undefined || x === null ? "–" : (x >= 0 ? "+" : "") + x.toFixed(2);

/** 모든 사업 빈자리 합의 변화: 1 → "빈자리 1개 채움", −1 → "빈자리 1개 생김", 0·없음 → "—".
 *  (2026-10-20 사용자 "'1자리 메움','1자리 생김'이 어떤 의미인지 잘 모를거같으니" -- 무엇이 채워지고 생기는지 말한다.
 *  신규 사업 후보 표는 사업별로 나눠 쓴다: scenarios/pickView.seatLines) */
export function seatsText(n: number | undefined | null): string {
  if (!n) return "—";
  return n > 0 ? `빈자리 ${n}개 채움` : `빈자리 ${-n}개 생김`;
}

/** 서버가 나눠 준 행인가(예전 밤 대기 명단처럼 합계만 있으면 false). */
export function hasSplit(r: ScoreSplit): r is ScoreSplit & { unfilled_seats_delta: number; delta_excl_unfilled: number } {
  return typeof r.unfilled_seats_delta === "number" && typeof r.delta_excl_unfilled === "number";
}

/** 한 줄 글: "1자리 메움 · 기술·협업 +1.20"(자리 변화가 없으면 "기술·협업 +1.20"). 나눔이 없는 예전 행은 "점수 +101.20". */
export function splitText(r: ScoreSplit): string {
  if (!hasSplit(r)) return `점수 ${signedPts(r.delta_total)}`;
  const rest = `기술·협업 ${signedPts(r.delta_excl_unfilled)}`;
  return r.unfilled_seats_delta ? `${seatsText(r.unfilled_seats_delta)} · ${rest}` : rest;
}
