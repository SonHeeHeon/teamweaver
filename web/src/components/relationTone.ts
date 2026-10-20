/** 두 사람 관계를 그림으로 그릴 때의 색·굵기(2026-10-21 공용으로 옮김 -- 화면 교체 검토 "팀원과의 관계" 그림과
 *  PDF 팀별 협업 지도가 같은 규칙을 쓴다. 같은 초록이 화면과 PDF에서 다른 뜻이면 헷갈린다). */

/** 평가 극성(-1~1)을 말로 -- 0.3 경계는 화면 표시용(계산에는 원래 값). */
export function reviewWord(r: number | null | undefined): string | null {
  if (r == null) return null;
  return r >= 0.3 ? "평가 좋음" : r <= -0.3 ? "평가 나쁨" : "평가 보통";
}

export const TONE = {
  good: { stroke: "#059669", label: "평가 좋음" },
  mid: { stroke: "#94a3b8", label: "평가 보통" },
  bad: { stroke: "#dc2626", label: "평가 나쁨" },
  none: { stroke: "#cbd5e1", dash: "3 2", label: "평가 없음" },
} as const satisfies Record<string, { stroke: string; dash?: string; label: string }>;
export type Tone = keyof typeof TONE;
export const DRAW_ORDER: Tone[] = ["none", "mid", "good", "bad"];   // 나쁨이 맨 위에 보이게
export const LEGEND_ORDER: Tone[] = ["good", "mid", "bad", "none"];

export function toneOf(r: number | null | undefined): Tone {
  const w = reviewWord(r);
  return w === "평가 좋음" ? "good" : w === "평가 나쁨" ? "bad" : w ? "mid" : "none";
}

/** 선 굵기 = 함께 일한 개월(6개월 1.6 · 12개월 2.2 · 24개월 3.4, 30개월에서 멈춤) */
export const strokeOf = (months: number) => 1 + Math.min(months, 30) / 10;

/** 점선은 굵기에 맞춰 마디를 늘리고 끝을 자른다(둥근 끝이면 굵은 점선이 실선처럼 메워진다 -- PDF 실측) */
export function lineStyle(tone: Tone, months: number) {
  const t = TONE[tone], sw = strokeOf(months), dashed = "dash" in t;
  return {
    stroke: t.stroke, strokeWidth: sw,
    strokeDasharray: dashed ? `${Math.max(3, sw * 1.6)} ${Math.max(2, sw * 1.3)}` : undefined,
    strokeLinecap: (dashed ? "butt" : "round") as "butt" | "round",
  };
}
