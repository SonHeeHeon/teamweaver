/** 공통 화면 부품(2026-10-18, claude-a 틀 -- claude-b 요청 "공통 부품을 먼저"). 상황 화면(신규 사업 편성·긴급 투입)이 같은 모양을 쓰게
 *  모양만 담는다(상태·서버 호출 없음). 색·글꼴은 tailwind.config.js 토큰, 버튼·칸은 index.css의 btn·btn-primary·btn-ghost·panel·field·seal.
 *
 *  - Panel: 제목·설명·오른쪽 동작이 있는 흰 상자(데이터 화면의 "편성 기록" 모양)
 *  - EmptyState: 비어 있을 때 이유 + 할 일(버튼)
 *  - Tabs: 화면 안 하위 탭(What-if의 배치 명단·교체 검토…) -- 숨김 방식으로 쓰면 내용이 남는다
 *  - Dialog: 확인 창(Esc·바깥 클릭 닫기는 쓰는 쪽이 정한다)
 *  - ActionBar: 화면 아래 고정 줄(확정·되돌리기 같은 주 동작)
 *  - Stamp: 인주색 도장 -- "서버가 검사해 통과"에만 쓴다(장식으로 쓰지 않는다)
 *  - Chip: 등급·단계 같은 짧은 표지. tone: grade1~4(특급→초급 램프)·stage·warn·plain
 *  - Notice: 화면 위 안내 띠. tone: info(기록·알림) · warn(주의) · bad(오류)
 */
import type { ReactNode } from "react";

export function Panel({ title, sub, actions, children, id }: {
  title: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; id?: string;
}) {
  return (
    <section className="panel" aria-labelledby={id}>
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 id={id} className="text-base font-semibold text-slate-900">{title}</h2>
          {sub && <p className="mt-0.5 text-[13px] text-slate-500">{sub}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function EmptyState({ title, body, actions }: { title: ReactNode; body?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="px-5 py-8 text-center">
      <p className="font-semibold text-slate-800">{title}</p>
      {body && <p className="mx-auto mt-1 max-w-[52ch] text-[13px] text-slate-500">{body}</p>}
      {actions && <div className="mt-4 flex justify-center gap-2">{actions}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items, right, label }: {
  value: T; onChange: (v: T) => void; items: [T, ReactNode][]; right?: ReactNode; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="mb-4 mt-5 flex flex-wrap items-end gap-0.5 border-b border-line">
      {items.map(([id, text]) => (
        <button key={id} role="tab" aria-selected={value === id} onClick={() => onChange(id)}
                className={`-mb-px border-b-2 px-3.5 py-2 text-sm ${value === id
                  ? "border-ink font-semibold text-ink" : "border-transparent font-medium text-slate-500 hover:text-slate-700"}`}>
          {text}
        </button>
      ))}
      {right && <div className="ml-auto flex items-center gap-2 pb-1.5">{right}</div>}
    </div>
  );
}

export function Dialog({ title, children, actions, labelId = "dialog-title" }: {
  title: ReactNode; children?: ReactNode; actions: ReactNode; labelId?: string;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[rgba(21,55,47,.32)] px-4" role="dialog"
         aria-modal="true" aria-labelledby={labelId}>
      <div className="panel w-full max-w-[480px] p-6">
        <h3 id={labelId} className="text-lg font-bold text-slate-900">{title}</h3>
        <div className="mt-1.5 text-[13.5px] text-slate-600">{children}</div>
        <div className="mt-5 flex justify-end gap-2">{actions}</div>
      </div>
    </div>
  );
}

export function ActionBar({ children, note }: { children: ReactNode; note?: ReactNode }) {
  return (
    <div className="sticky bottom-0 z-10 -mx-1 mt-4 flex flex-wrap items-center gap-2 border-t border-line bg-canvas/95 px-1 py-3 backdrop-blur">
      {children}
      {note && <span className="text-xs text-slate-500">{note}</span>}
    </div>
  );
}

export function Stamp({ top, bottom = "통과", title }: { top: string; bottom?: string; title?: string }) {
  return <span className="seal" title={title}>{top}<small className="block text-[9.5px] font-semibold">{bottom}</small></span>;
}

const CHIP: Record<string, string> = {
  grade1: "bg-g1 text-white", grade2: "bg-g2 text-white", grade3: "bg-g3 text-[#0F2620]", grade4: "bg-g4 text-[#0F2620]",
  stage: "border border-pine-200 bg-[#F1F7F4] text-slate-800", warn: "bg-amber-50 text-amber-700",
  plain: "border border-line text-slate-500",
};
export const GRADE_TONE: Record<string, keyof typeof CHIP> = { 특급: "grade1", 고급: "grade2", 중급: "grade3", 초급: "grade4" };

export function Chip({ children, tone = "plain" }: { children: ReactNode; tone?: keyof typeof CHIP }) {
  return <span className={`inline-block rounded-full px-2 py-px text-xs font-medium ${CHIP[tone]}`}>{children}</span>;
}

export function Notice({ children, tone = "info", role }: { children: ReactNode; tone?: "info" | "warn" | "bad"; role?: string }) {
  const cls = tone === "warn" ? "border-amber-200 bg-amber-50 text-amber-800"
    : tone === "bad" ? "border-red-200 bg-red-50 text-red-700" : "border-sky-200 bg-sky-50 text-sky-900";
  return <div role={role ?? (tone === "bad" ? "alert" : "status")} className={`rounded-md border px-4 py-2 text-sm ${cls}`}>{children}</div>;
}

// 화면 틀(왼쪽 메뉴·제목 줄)은 components/Shell.tsx -- 상황 화면은 App이 그 안에 그린다.
export { PageHeader, WhatifEmpty, datasetLabel } from "../components/Shell";
