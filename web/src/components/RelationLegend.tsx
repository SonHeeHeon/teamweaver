import type { ReactNode } from "react";
import { LEGEND_ORDER, TONE, strokeOf } from "./relationTone";

export function LegendLine({ stroke, width = 2, dash }: { stroke: string; width?: number; dash?: string }) {
  return <svg width={22} height={8} className="mr-1 inline-block align-middle"><line x1={1} y1={4} x2={21} y2={4} stroke={stroke} strokeWidth={width} strokeDasharray={dash} /></svg>;
}

/** 굵기·색 범례. 평가가 실리지 않은 데이터(실데이터)면 색 대신 그 사실을 적는다. */
export function RelationLegend({ hasReview, extra }: { hasReview: boolean; extra?: ReactNode }) {
  return (
    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-slate-600">
      <span><LegendLine stroke="#64748b" width={strokeOf(6)} />6개월</span>
      <span><LegendLine stroke="#64748b" width={strokeOf(12)} />12개월</span>
      <span><LegendLine stroke="#64748b" width={strokeOf(24)} />24개월</span>
      {hasReview
        ? LEGEND_ORDER.map((k) => {
          const t = TONE[k];
          return <span key={k}><LegendLine stroke={t.stroke} dash={"dash" in t ? "4 3" : undefined} />{t.label}</span>;
        })
        : <span>선 색 없음: 그린 관계에 실린 동료 평가가 없다(실제 인사 자료는 평가 내용을 싣지 않는다).</span>}
      {extra}
    </p>
  );
}
