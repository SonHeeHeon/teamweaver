/** 투입률을 %로 보인다. 내림이라 실제 값보다 크게 보이지 않는다(C3) -- 서버가 최소 투입률을
 *  지키려고 0.205999 같은 값을 그대로 둘 때, 반올림하면 "21%"로 가용률(20.6%)을 넘어 보였다. */
export function formatAlloc(alloc: number): string {
  const tenths = Math.floor(alloc * 1000 + 1e-6);
  return `${tenths % 10 === 0 ? tenths / 10 : (tenths / 10).toFixed(1)}%`;
}

/** 계획 달 순번(0부터)의 달력 달 "26.10"(시작 달을 모르면 null). */
function calendar(m: number, start?: string | null): string | null {
  const [y, mo] = (start ?? "").split("-").map(Number);
  if (!y || !mo) return null;
  const d = new Date(y, mo - 1 + m, 1);
  return `${String(d.getFullYear()).slice(2)}.${d.getMonth() + 1}`;
}

/** 계획 달 순번(0부터) → 표시 이름. 시작 달을 알면 실제 달("26.10월"), 모르면 예전처럼 순번("1월").
 *  12개월 계획(2026-10-19 계획기간 1년 확장)에서 순번 "1월~12월"은 달력 달처럼 읽혀 헷갈린다. */
export function monthLabel(m: number, start?: string | null): string {
  return `${calendar(m, start) ?? m + 1}월`;
}

/** 계획 기간 "2026-10 ~ 2027-09 · 12개월". 시작 달이 없으면 "". */
export function horizonRange(start: string | null | undefined, months: number): string {
  const [y, mo] = (start ?? "").split("-").map(Number);
  if (!y || !mo || !months) return "";
  const d = new Date(y, mo - 1 + months - 1, 1);
  return `${start} ~ ${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")} · ${months}개월`;
}

/** 월별 투입률을 구간으로 요약한다: {0:.2,1:.2,2:.2,3:1,4:1,5:1} → "1~3월 20%, 4~6월 100%". 없으면 "".
 *  시작 달(start, "2026-10")을 주면 실제 달로: "26.10~26.12월 20%, 27.1~27.3월 100%". */
export function formatMonthly(monthly?: Record<string, number> | null, start?: string | null): string {
  if (!monthly) return "";
  const months = Object.keys(monthly).map(Number).sort((a, b) => a - b);
  const parts: string[] = [];
  const short = (m: number) => calendar(m, start) ?? String(m + 1);
  let first = months[0];
  for (let k = 1; k <= months.length; k++) {
    const prev = months[k - 1], cur = months[k];
    if (cur === undefined || cur !== prev + 1 || monthly[cur] !== monthly[prev]) {
      const label = first === prev ? monthLabel(first, start) : `${short(first)}~${monthLabel(prev, start)}`;
      parts.push(`${label} ${formatAlloc(monthly[prev])}`);
      first = cur;
    }
  }
  return parts.join(", ");
}
