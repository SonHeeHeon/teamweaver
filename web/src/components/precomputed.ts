/** 미리 계산 시각(ISO) → 화면 표기 "10월 6일 23:40"(브라우저 현지 시각), withYear면 "2026년 10월 6일 23:40".
 *  비었으면 "시각 미상", 읽을 수 없으면 원문 그대로. */
export function formatPrecomputedAt(iso: string, withYear = false): string {
  if (!iso) return "시각 미상";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${withYear ? `${d.getFullYear()}년 ` : ""}${d.getMonth() + 1}월 ${d.getDate()}일 ${hh}:${mm}`;
}
