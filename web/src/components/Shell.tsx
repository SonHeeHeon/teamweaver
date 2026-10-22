import type { ReactNode } from "react";
import type { DatasetInfo } from "../api/types";
import { Brand } from "./LoginScreen";

/** 메뉴(2026-10-18 사용자 결정 — 3가지 핵심 상황 중심): 신규 사업 편성(가장 잦음, 첫 화면) · 긴급 투입 · 연간 계획(드묾), 그 아래 지금 배치와 관리.
 *  2026-10-21 사용자 "what if는 현재 프로젝트 인력 배치에서 부서장이 이렇게 바꿔봤을때 … 지금의 화면은 연간 계획 화면과 맞물려있는 개념":
 *  예전 What-if 대시보드(계산한 안 A~D 위에서 바꿔 보기)는 연간 계획 ② 편성안 비교·확정(`plans`)이 되고,
 *  What-if(`whatif`)는 지금 실제 배치(작업 데이터)에서 바꿔 보는 새 화면이다. */
export type Tab = "pipeline" | "emergency" | "annual" | "plans" | "whatif" | "skills" | "reviews" | "records" | "data" | "settings";

export const NAV: { group: string; items: { id: Tab; label: string; hint?: string; sub?: boolean }[] }[] = [
  { group: "핵심 상황", items: [{ id: "pipeline", label: "신규 사업 편성" }, { id: "emergency", label: "긴급 투입" },
                                { id: "annual", label: "연간 계획", hint: "백지" }, { id: "plans", label: "편성안 비교·확정", sub: true }] },
  { group: "지금 배치", items: [{ id: "whatif", label: "What-if" }, { id: "skills", label: "조직 기술 지도" }] },
  { group: "관리", items: [{ id: "reviews", label: "검토 자료" }, { id: "records", label: "편성 기록" }, { id: "data", label: "데이터" },
                           { id: "settings", label: "배치 설정" }] },
];

export const PAGE: Record<Tab, { title: string; sub: string }> = {
  pipeline: { title: "신규 사업 편성", sub: "발주 예정·제안 중인 사업에 팀을 꾸립니다 — 쉬는 인력, 끝나 가는 인력, 진행 사업에서 몇 명만 옮기기" },
  emergency: { title: "긴급 투입", sub: "진행 사업에 이슈가 생겼을 때 사람을 더 넣습니다 — 후보 순위와 최선 조합" },
  annual: { title: "연간 계획 · ① 조건·계산", sub: "사업마다 등급별 인원·요구 기술·예산을 확인하고 편성안 A~D를 계산합니다" },
  plans: { title: "연간 계획 · ② 편성안 비교·확정", sub: "계산한 안 A~D를 비교하고, 사람을 바꿔 다듬고, 고른 안을 지금 배치로 확정하거나 PDF로 냅니다" },
  whatif: { title: "What-if", sub: "지금 실제 배치에서 사람을 바꿔 보고 점수·규칙·팀 관계 변화를 봅니다 — 마음에 들면 확정합니다" },
  skills: { title: "조직 기술 지도", sub: "기술마다 실무 가능·숙련 인원과 요구 사업을 봅니다" },
  reviews: { title: "인사팀 검토 자료", sub: "확정한 편성마다 사업별 소명 자료를 고치고 PDF로 냅니다" },
  records: { title: "편성 기록", sub: "지난 편성을 파일로 내보내고 다시 열어 봅니다" },
  data: { title: "데이터", sub: "인력·사업 CSV 묶음을 올려 작업 데이터를 바꿉니다" },
  settings: { title: "배치 설정", sub: "최소 투입률·익숙한 사이 기준·계산 시간 같은 조직 공통 규칙" },
};

export function datasetLabel(d: DatasetInfo | null): string {
  if (!d) return "불러오는 중";
  if (d.record_title) return d.record_title;
  if (d.source === "fixture") return "기본 예시 데이터";
  return d.dataset_id;
}

interface SidebarProps {
  tab: Tab;
  onTab: (t: Tab) => void;
  dataset: DatasetInfo | null;
  user: string | null;
  onLogout: (() => void) | null;
}

export function Sidebar({ tab, onTab, dataset, user, onLogout }: SidebarProps) {
  return (
    <aside className="flex w-full shrink-0 flex-col bg-ink text-[#DCE7E2] md:sticky md:top-0 md:h-screen md:w-[232px]">
      <div className="flex items-center justify-between px-5 pb-2 pt-5 md:pb-5">
        <Brand light />
        {/* 휴대폰 폭에서는 아래 사용자 칸이 숨으므로 로그아웃을 여기에도 둔다(Codex 리뷰 P2) */}
        {onLogout && (
          <button onClick={onLogout} className="rounded-md border border-[#3B6359] px-2 py-0.5 text-xs text-[#DCE7E2] md:hidden">
            로그아웃
          </button>
        )}
      </div>
      <nav aria-label="메뉴" className="flex gap-1 overflow-x-auto px-3 pb-3 md:block md:overflow-visible md:pb-0">
        {NAV.map((g) => (
          <div key={g.group} className="flex gap-1 md:block">
            <div className="hidden px-2 pb-1.5 pt-3.5 text-xs text-[#8FB0A5] md:block">{g.group}</div>
            {g.items.map((it) => (
              <button key={it.id} onClick={() => onTab(it.id)} aria-current={tab === it.id ? "page" : undefined}
                      className={`flex w-full items-center justify-between whitespace-nowrap rounded-md py-[7px] text-left
                                  ${it.sub ? "pl-6 pr-2.5 text-[13px]" : "px-2.5 text-sm"} ${tab === it.id
                                    ? "bg-[#24504A] font-semibold text-white shadow-[inset_3px_0_0_#9FD0BE]"
                                    : "text-[#DCE7E2] hover:bg-[#1C443B]"}`}>
                <span>{it.sub && <span aria-hidden className="mr-1 text-[#8FB0A5]">└</span>}{it.label}</span>
                {it.hint && <small aria-hidden className="ml-3 hidden text-[11.5px] font-normal text-[#8FB0A5] md:inline">{it.hint}</small>}
              </button>
            ))}
          </div>
        ))}
      </nav>
      <div className="hidden flex-1 md:block" />
      <div className="hidden border-t border-[#2C5148] px-5 pb-1 pt-3.5 text-[12.5px] text-[#B9CEC6] md:block">
        지금 데이터
        <b className="my-0.5 block text-[13px] font-semibold text-white">{datasetLabel(dataset)}</b>
        {dataset && <span className="num">{dataset.people}명 · 사업 {dataset.projects}건{dataset.synthetic ? " · 가상 데이터" : ""}</span>}
      </div>
      {onLogout && (
        <div className="hidden items-center justify-between px-5 pb-5 pt-2.5 text-[12.5px] text-[#B9CEC6] md:flex">
          <span>{user ?? "관리자"}</span>
          <button onClick={onLogout}
                  className="rounded-md border border-[#3B6359] px-2 py-0.5 text-xs text-[#DCE7E2] hover:bg-[#1C443B]">
            로그아웃
          </button>
        </div>
      )}
    </aside>
  );
}

/** 연간 계획 두 단계(2026-10-21): ① 조건·계산 → ② 편성안 비교·확정. 두 화면 위에 같은 줄로 보여 어디쯤인지 알게 한다. */
export function AnnualSteps({ tab, onTab, hasPlans }: { tab: Tab; onTab: (t: Tab) => void; hasPlans: boolean }) {
  const steps: [Tab, string][] = [["annual", "① 조건·계산"], ["plans", "② 편성안 비교·확정"]];
  return (
    <nav aria-label="연간 계획 단계" className="mb-4 flex flex-wrap items-center gap-2 text-sm">
      {steps.map(([id, label], i) => (
        <span key={id} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden className="text-slate-400">→</span>}
          <button type="button" onClick={() => onTab(id)} aria-current={tab === id ? "step" : undefined}
                  className={`rounded-full border px-3 py-1 ${tab === id ? "border-ink bg-ink font-semibold text-white"
                    : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
            {label}{id === "plans" && !hasPlans ? <span className="ml-1 text-xs font-normal opacity-70">(아직 없음)</span> : null}
          </button>
        </span>
      ))}
    </nav>
  );
}

export function PageHeader({ tab, context, actions }: { tab: Tab; context?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-line bg-paper px-4 py-4 md:px-8 md:pb-4 md:pt-6">
      <div>
        <h1 className="text-[22px] font-bold tracking-tight text-slate-900">{PAGE[tab].title}</h1>
        <p className="num mt-0.5 text-[13px] text-slate-500">{context ?? PAGE[tab].sub}</p>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

/** 편성안이 아직 없을 때(사용자 지시 3: 빈 화면만 보이면 당황한다). 할 수 있는 일 두 가지를 버튼과 함께. */
export function WhatifEmpty({ dataset, onCompute, onRecords, onPipeline, closedBy, confirmed }: {
  dataset: DatasetInfo | null; onCompute: () => void; onRecords: () => void;
  /** 신규 사업 편성의 자동 편성으로(2026-10-22 화면 검토 P3-4 -- 판이 바뀌어 닫힌 안내에서 바로 다시 계산하러) */
  onPipeline?: () => void;
  /** 연간 계획 "이 안으로 확정" 뒤 안내(2026-10-21) */
  confirmed?: string | null;
  /** 상황 화면이 데이터를 바꿔 보던 편성안을 닫았으면 그 화면 이름(2026-10-21 사용자 "편성안이 없어졌어 … 다시 계산해야한다는거지?") */
  closedBy?: string | null;
}) {
  return (
    <div>
      <div aria-hidden className="mb-2 grid grid-cols-2 gap-3 opacity-55 sm:grid-cols-4">
        {["A", "B", "C", "D"].map((l) => (
          <div key={l} className="grid h-[120px] place-items-center rounded-lg border-[1.5px] border-dashed border-slate-300
                                  text-[40px] font-bold text-slate-400">{l}</div>
        ))}
      </div>
      <section className="panel mx-auto mt-10 max-w-[640px] px-6 py-7 md:px-9 md:py-8" aria-labelledby="whatif-empty">
        {confirmed && (
          <p role="status" className="mb-4 rounded-md border border-pine-200 bg-pine-50 px-3 py-2.5 text-[13px] text-pine-800">{confirmed}</p>
        )}
        {closedBy && !confirmed && (
          <p role="note" className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-[13px] text-amber-900">
            <b>{closedBy}에서 데이터를 바꿔 보던 편성안을 닫았습니다.</b> 편성안은 계산할 때의 데이터(판)에 묶여 있어서, 사업이 늘거나 배치가 바뀐
            새 판에서는 교체 검토·PDF가 맞지 않습니다. 새 판으로 다시 계산하세요 — 전체를 다시 짜려면 연간 계획, 신규 사업만 채워 보려면 신규 사업 편성의
            자동 편성입니다. 편성 기록을 다시 열면 기록 당시 데이터로 돌아가 방금 바꾼 내용이 빠집니다.
            <span className="mt-2 flex flex-wrap gap-2">
              <button type="button" className="btn-primary px-2.5 py-1 text-xs" onClick={onCompute}>새 판으로 연간 계획 다시 계산</button>
              {onPipeline && <button type="button" className="btn px-2.5 py-1 text-xs" onClick={onPipeline}>신규 사업 자동 편성으로</button>}
            </span>
          </p>
        )}
        <h2 id="whatif-empty" className="text-xl font-bold text-slate-900">아직 볼 편성안이 없습니다</h2>
        <p className="mt-1.5 text-slate-500">
          이 화면은 계산한 편성안을 보여 줍니다.
          {dataset ? ` 지금 데이터(${dataset.people}명 · 사업 ${dataset.projects}건)로 아직 계산하지 않았습니다.` : ""}
          {" "}아래 중 하나로 시작하세요.
        </p>
        <div className="mt-5 grid gap-2.5">
          <div className="grid grid-cols-[1fr_auto] items-center gap-4 rounded-lg border border-line px-4 py-3.5">
            <div>
              <b className="block font-semibold">새로 계산하기</b>
              <span className="text-[13px] text-slate-500">① 조건·계산에서 사업 조건을 확인하고 최적화를 실행합니다. 안 하나에 100명 6개월 기준 약 30초(12개월 계획은 약 4배)입니다.</span>
            </div>
            <button className="btn-primary" onClick={onCompute}>① 조건·계산으로</button>
          </div>
          <div className="grid grid-cols-[1fr_auto] items-center gap-4 rounded-lg border border-line px-4 py-3.5">
            <div>
              <b className="block font-semibold">지난 편성 다시 보기</b>
              <span className="text-[13px] text-slate-500">예전에 내보낸 편성 기록(.json)을 열어 그때의 편성안을 봅니다.</span>
            </div>
            <button className="btn" onClick={onRecords}>편성 기록 열기</button>
          </div>
        </div>
      </section>
    </div>
  );
}

/** What-if 대시보드 아래쪽 하위 탭. 탭을 바꿔도 내용은 남는다(숨김) -- 소명 글·교체 검토가 사라지지 않게. */
export type SubTab = "roster" | "swap" | "justify" | "baseline";

export function SubTabs({ value, onChange, showJustify, right }: {
  value: SubTab; onChange: (v: SubTab) => void; showJustify: boolean; right?: ReactNode;
}) {
  const items: [SubTab, string][] = [["roster", "배치 명단"], ["swap", "교체 검토"],
                                     ...(showJustify ? [["justify", "인사팀 소명"] as [SubTab, string]] : []),
                                     ["baseline", "단순 규칙 비교"]];
  return (
    <div role="tablist" aria-label="편성안 보기" className="mb-4 mt-5 flex flex-wrap items-end gap-0.5 border-b border-line">
      {items.map(([id, label]) => (
        <button key={id} role="tab" aria-selected={value === id} onClick={() => onChange(id)}
                className={`-mb-px border-b-2 px-3.5 py-2 text-sm ${value === id
                  ? "border-ink font-semibold text-ink" : "border-transparent font-medium text-slate-500 hover:text-slate-700"}`}>
          {label}
        </button>
      ))}
      {right && <div className="ml-auto flex items-center gap-2 pb-1.5">{right}</div>}
    </div>
  );
}
