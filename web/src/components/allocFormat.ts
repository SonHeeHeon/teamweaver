/** 투입률을 %로 보인다. 내림이라 실제 값보다 크게 보이지 않는다(C3) -- 서버가 최소 투입률을
 *  지키려고 0.205999 같은 값을 그대로 둘 때, 반올림하면 "21%"로 가용률(20.6%)을 넘어 보였다. */
export function formatAlloc(alloc: number): string {
  const tenths = Math.floor(alloc * 1000 + 1e-6);
  return `${tenths % 10 === 0 ? tenths / 10 : (tenths / 10).toFixed(1)}%`;
}

/** 월별 투입률을 구간으로 요약한다: {0:.2,1:.2,2:.2,3:1,4:1,5:1} → "1~3월 20%, 4~6월 100%". 없으면 "". */
export function formatMonthly(monthly?: Record<string, number> | null): string {
  if (!monthly) return "";
  const months = Object.keys(monthly).map(Number).sort((a, b) => a - b);
  const parts: string[] = [];
  let start = months[0];
  for (let k = 1; k <= months.length; k++) {
    const prev = months[k - 1], cur = months[k];
    if (cur === undefined || cur !== prev + 1 || monthly[cur] !== monthly[prev]) {
      const label = start === prev ? `${start + 1}월` : `${start + 1}~${prev + 1}월`;
      parts.push(`${label} ${formatAlloc(monthly[prev])}`);
      start = cur;
    }
  }
  return parts.join(", ");
}
