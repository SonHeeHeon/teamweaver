/** 투입률을 %로 보인다. 내림이라 실제 값보다 크게 보이지 않는다(C3) -- 서버가 최소 투입률을
 *  지키려고 0.205999 같은 값을 그대로 둘 때, 반올림하면 "21%"로 가용률(20.6%)을 넘어 보였다. */
export function formatAlloc(alloc: number): string {
  const tenths = Math.floor(alloc * 1000 + 1e-6);
  return `${tenths % 10 === 0 ? tenths / 10 : (tenths / 10).toFixed(1)}%`;
}
