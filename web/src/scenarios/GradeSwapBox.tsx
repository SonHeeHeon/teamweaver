/** 등급 대체(2026-10-21 사용자 결정 "위 아래 모두 허용하고, 사유는 전부 받고, 예산을 넘으면 증액을 함께 적자", claude-a).
 *  손 편성으로 어떤 등급이 정원을 넘고 같은 사업에 다른 등급 빈자리가 있으면, 막지 않고 "중급 자리 1개를 고급으로" 대체한다 --
 *  확정하면 그 사업 정원이 바뀌고(자리 등급 −n, 들어간 등급 +n) 사유는 확정 이력에 남는다(인사팀 검토 자료 사실). 서버가 같은 규칙
 *  (`core/evaluate/grade_swap`)으로 다시 계산해 맞춰 본다. */
import { GRADES, type ApplyHint, type Grade, type GradeSwap } from "./workspaceApi";

export const swapKey = (projectId: string, s: GradeSwap) => `${projectId}:${s.seat}>${s.grade}`;
/** 위 등급이 아래 자리를 메우면 "위", 아래 등급이 위 자리를 메우면 "아래" */
export const swapDirection = (s: GradeSwap): "up" | "down" =>
  GRADES.indexOf(s.grade as Grade) < GRADES.indexOf(s.seat as Grade) ? "up" : "down";

export type SwapReasons = Record<string, string>;

/** 사업별 대체를 한 목록으로(긴급 투입 apply_hint는 사업별, 신규 사업 draft는 그 사업 하나) */
export function swapList(bySite: Record<string, GradeSwap[]> | undefined): { projectId: string; swap: GradeSwap }[] {
  return Object.entries(bySite ?? {}).flatMap(([projectId, xs]) => xs.map((swap) => ({ projectId, swap })));
}

export function swapsReady(items: { projectId: string; swap: GradeSwap }[], reasons: SwapReasons): boolean {
  return items.every(({ projectId, swap }) => (reasons[swapKey(projectId, swap)] ?? "").trim().length > 0);
}

/** 확정 요청의 grade_swaps(사유를 붙인다) */
export function swapBody(items: { projectId: string; swap: GradeSwap }[], reasons: SwapReasons) {
  return items.map(({ projectId, swap }) => ({ project_id: projectId, ...swap, reason: (reasons[swapKey(projectId, swap)] ?? "").trim() }));
}

/** 긴급 투입 apply_hint를 확정 요청 모양으로: 대체는 사유를 붙인 목록으로 바꾼다 */
export function hintBody(hint: ApplyHint, reasons: SwapReasons) {
  const { grade_swaps, ...rest } = hint;
  const items = swapList(grade_swaps);
  return { ...rest, ...(items.length ? { grade_swaps: swapBody(items, reasons) } : {}) };
}

export function GradeSwapBox({ items, budgetAdd, reasons, onReason, named = (id) => id, disabled }: {
  items: { projectId: string; swap: GradeSwap }[];
  /** 대체가 있어 예산을 넘는 만큼 함께 늘릴 월 예산(사업별 합) */
  budgetAdd?: number;
  reasons: SwapReasons;
  onReason: (key: string, value: string) => void;
  named?: (projectId: string) => string;
  disabled?: boolean;
}) {
  if (!items.length) return null;
  const missing = items.filter(({ projectId, swap }) => !(reasons[swapKey(projectId, swap)] ?? "").trim()).length;
  return (
    <section aria-label="등급 대체" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm text-amber-950">
      <p className="font-medium">
        등급 대체 {items.length}건{missing > 0 ? ` — 사유 ${missing}건을 적어야 확정할 수 있습니다` : " — 사유를 모두 적었습니다"}
      </p>
      <ul className="mt-1.5 space-y-2">
        {items.map(({ projectId, swap }) => {
          const key = swapKey(projectId, swap);
          const down = swapDirection(swap) === "down";
          return (
            <li key={key}>
              <p className="text-[13px]">
                {named(projectId)}의 <b>{swap.seat} 자리 {swap.count}개</b>를 <b>{swap.grade}</b>으로 채웁니다
                <span className={down ? "text-red-700" : "text-slate-600"}> ({down ? "아래 등급 대체" : "위 등급 대체"})</span>.
                확정하면 이 사업 정원이 {swap.seat} −{swap.count} · {swap.grade} +{swap.count}로 바뀝니다.
              </p>
              <label className="mt-1 flex items-center gap-2 text-xs">
                <span className="shrink-0 text-slate-600">사유(필수)</span>
                <input className="field w-full py-1" maxLength={200} disabled={disabled}
                       aria-label={`${projectId} ${swap.seat} 자리 ${swap.grade} 대체 사유`}
                       placeholder={down ? "예: 사업 범위상 이 등급으로 충분, 발주처와 협의함" : "예: 중급이 모두 다른 사업에 투입 중"}
                       value={reasons[key] ?? ""} onChange={(e) => onReason(key, e.target.value)} />
              </label>
            </li>
          );
        })}
      </ul>
      {(budgetAdd ?? 0) > 0 && (
        <p className="mt-1.5 text-xs">비용이 예산을 넘어 월 예산 +{(budgetAdd ?? 0).toLocaleString()}이(가) 함께 늘어납니다.</p>
      )}
      <p className="mt-1 text-xs text-slate-600">
        사유는 확정 기록과 인사팀 검토 자료에 남습니다(아래 등급 대체는 '확인할 사실'). 확정 취소·되돌리기를 하면 정원·예산도 함께 돌아갑니다.
      </p>
    </section>
  );
}

/** 예산 증액 사유(2026-10-22 사용자 "전부 하자" -- 화면 검토 P3-2): 손 편성이 월 예산을 새로 넘기면 막지 않고, 넘친 만큼 증액을 사유와 함께 받는다.
 *  금액은 서버 미리 계산(`budget_raises` -- core.evaluate.grade_swap.budget_raises) 그대로 보내고 확정이 같은 함수로 맞춰 본다. */
export type RaiseReasons = Record<string, string>;

export function raisesReady(raises: Record<string, number> | undefined, reasons: RaiseReasons): boolean {
  return Object.keys(raises ?? {}).every((j) => (reasons[j] ?? "").trim().length > 0);
}

export function raiseBody(raises: Record<string, number> | undefined, reasons: RaiseReasons) {
  return Object.entries(raises ?? {}).map(([project_id, amount]) => ({ project_id, amount, reason: (reasons[project_id] ?? "").trim() }));
}

export function BudgetRaiseBox({ raises, reasons, onReason, named = (id) => id, disabled }: {
  raises: Record<string, number> | undefined;
  reasons: RaiseReasons;
  onReason: (projectId: string, value: string) => void;
  named?: (projectId: string) => string;
  disabled?: boolean;
}) {
  const items = Object.entries(raises ?? {});
  if (!items.length) return null;
  const missing = items.filter(([j]) => !(reasons[j] ?? "").trim()).length;
  return (
    <section aria-label="예산 증액" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm text-amber-950">
      <p className="font-medium">
        예산 초과 {items.length}건{missing > 0 ? ` — 증액 사유 ${missing}건을 적어야 확정할 수 있습니다` : " — 사유를 모두 적었습니다"}
      </p>
      <ul className="mt-1.5 space-y-2">
        {items.map(([j, amount]) => (
          <li key={j}>
            <p className="text-[13px]">{named(j)}: 이 확정으로 월 비용이 예산을 <b>{amount.toLocaleString()}</b> 넘습니다. 확정하면 월 예산을 그만큼 늘립니다.</p>
            <label className="mt-1 flex items-center gap-2 text-xs">
              <span className="shrink-0 text-slate-600">증액 사유(필수)</span>
              <input className="field w-full py-1" maxLength={200} disabled={disabled} aria-label={`${j} 예산 증액 사유`}
                     placeholder="예: 발주처 요청으로 범위가 늘어 인원 추가, 영업과 협의함"
                     value={reasons[j] ?? ""} onChange={(e) => onReason(j, e.target.value)} />
            </label>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs text-slate-600">
        사유는 확정 기록·인사팀 검토 자료('확인할 사실')·변경 보고서에 남습니다. 확정 취소·되돌리기를 하면 예산도 함께 돌아갑니다.
      </p>
    </section>
  );
}
