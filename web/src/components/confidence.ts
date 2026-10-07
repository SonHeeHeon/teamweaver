/** 증명된 상한 대비 차이(%) = |상한−해|/|해| × 100. 해가 0에 가깝거나 값이 없으면 null. */
export function gapPct(objective: number | null | undefined, bound: number | null | undefined): number | null {
  if (objective == null || bound == null || !Number.isFinite(objective) || !Number.isFinite(bound)) return null;
  const denom = Math.abs(objective);
  if (denom < 1e-9) return null;
  return (Math.abs(bound - objective) / denom) * 100;
}
