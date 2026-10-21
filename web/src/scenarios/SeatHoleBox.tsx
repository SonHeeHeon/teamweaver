/** 빼 온 자리 결정(2026-10-21 사용자 "빼간 자리를 매꿔야한다는게 있어야할거같네. 못메꾼다면 메꾸지 않는다라는 표기를 통해 더이상 그건
 *  컨트롤 안해도 되는걸로", claude-a). 이 확정으로 진행 사업에 새로 생긴 빈자리마다 메울 사람을 넣거나 "메우지 않음"과 사유를 적어야
 *  확정된다 -- 메우지 않으면 그 사업 그 등급 정원이 줄어(예산 그대로) 그 뒤로 빈자리로 세지 않는다. 서버가 같은 규칙
 *  (`core/evaluate/seat_holes`)으로 다시 계산해 맞춰 본다. 등급 대체 상자(`GradeSwapBox`)와 같은 모양. */
import { Chip, GRADE_TONE } from "../ui";
import type { FillLine, FillOption, FillPick, Grade, Holes, SeatWaiver } from "./workspaceApi";

export const holeKey = (projectId: string, grade: string) => `${projectId}:${grade}`;
/** 메우지 않기로 고른 빈자리 → 사유(고르면 열쇠가 생기고, 사유는 빈 글일 수 있다) */
export type WaiverReasons = Record<string, string>;

export function holeList(holes: Holes | undefined): { projectId: string; grade: Grade; count: number }[] {
  return Object.entries(holes ?? {}).flatMap(([projectId, hs]) => hs.map((h) => ({ projectId, ...h })));
}

export function holesReady(holes: Holes | undefined, waivers: WaiverReasons): boolean {
  return holeList(holes).every((h) => (waivers[holeKey(h.projectId, h.grade)] ?? "").trim().length > 0);
}

/** 확정 요청의 seat_waivers(지금 빈자리 중 메우지 않기로 한 것만) */
export function waiverBody(holes: Holes | undefined, waivers: WaiverReasons): SeatWaiver[] {
  return holeList(holes).filter((h) => holeKey(h.projectId, h.grade) in waivers)
    .map((h) => ({ project_id: h.projectId, grade: h.grade, count: h.count, reason: (waivers[holeKey(h.projectId, h.grade)] ?? "").trim() }));
}

export function SeatHoleBox({ holes, fillOptions, fills, waivers, onWaive, onFill, onUnfill, removable, named = (id) => id, disabled }: {
  holes?: Holes;
  fillOptions?: Record<string, FillOption[]>;
  /** 같은 확정에서 빼 온 사업에 들어간 사람 */
  fills?: FillLine[];
  waivers: WaiverReasons;
  /** reason이 null이면 "메우지 않음"을 끈다 */
  onWaive: (key: string, reason: string | null) => void;
  /** 없으면 메울 사람을 고르지 못한다(자동 계산 결과 확정 -- 메우려면 손 편성) */
  onFill?: (pick: FillPick) => void;
  onUnfill?: (personId: string) => void;
  /** 화면에서 고른 메울 사람(빼기 단추를 보인다) */
  removable?: Set<string>;
  named?: (projectId: string) => string;
  disabled?: boolean;
}) {
  const items = holeList(holes);
  const filled = fills ?? [];
  if (!items.length && !filled.length) return null;
  const missing = items.filter((h) => !(waivers[holeKey(h.projectId, h.grade)] ?? "").trim()).length;
  return (
    <section aria-label="빼 온 자리" className={`rounded-md border px-3 py-2.5 text-sm ${items.length
      ? "border-amber-300 bg-amber-50 text-amber-950" : "border-slate-200 bg-slate-50 text-slate-700"}`}>
      {filled.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
          <span className="font-medium">빼 온 자리를 메운 사람</span>
          {filled.map((f) => (
            <span key={`${f.project_id}:${f.person_id}`} className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-1.5 py-0.5">
              {named(f.project_id)} 자리 ← <Chip tone={GRADE_TONE[f.grade]}>{f.grade}</Chip> {f.name}
              <span className="text-xs text-slate-500">({f.from_bench ? "대기 인력" : "다른 배치에서"})</span>
              {onUnfill && removable?.has(f.person_id) && (
                <button type="button" className="ml-0.5 px-1 text-xs text-slate-500 hover:text-red-700" disabled={disabled}
                        aria-label={`${f.name} 메우기 취소`} onClick={() => onUnfill(f.person_id)}>✕</button>
              )}
            </span>
          ))}
        </div>
      )}
      {items.length > 0 && (
        <>
          <p className={`font-medium ${filled.length ? "mt-2" : ""}`}>
            빼 온 자리 {items.reduce((n, h) => n + h.count, 0)}개가 빕니다
            {missing > 0 ? ` — 메울 사람을 넣거나 '메우지 않음'과 사유를 적어야 확정할 수 있습니다` : " — 모두 정했습니다"}
          </p>
          <ul className="mt-1.5 space-y-2.5">
            {items.map((h) => {
              const key = holeKey(h.projectId, h.grade);
              const waived = key in waivers;
              const opts = fillOptions?.[h.projectId] ?? [];
              return (
                <li key={key}>
                  <p className="text-[13px]">{named(h.projectId)}의 <b>{h.grade} 자리 {h.count}개</b>가 빕니다.</p>
                  {onFill && !waived && (opts.length > 0 ? (
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="text-slate-600">메울 사람</span>
                      {opts.map((o) => (
                        <button key={o.person_id} type="button" className="btn px-1.5 py-0.5 text-xs" disabled={disabled}
                                title={`기술 적합 ${o.skill_fit.toFixed(2)}${o.grade !== h.grade ? " · 다른 등급이라 등급 대체(사유)가 함께 걸린다" : ""}`}
                                onClick={() => onFill({ person_id: o.person_id, project_id: h.projectId, alloc: o.alloc })}>
                          <Chip tone={GRADE_TONE[o.grade]}>{o.grade}</Chip> {o.name} 넣기
                        </button>
                      ))}
                    </div>
                  ) : <p className="mt-1 text-xs text-slate-600">지금 바로 넣을 수 있는 사람이 없습니다.</p>)}
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                    <label className="inline-flex items-center gap-1">
                      <input type="checkbox" checked={waived} disabled={disabled}
                             onChange={(e) => onWaive(key, e.target.checked ? "" : null)} />
                      메우지 않음
                    </label>
                    {waived && (
                      <input className="field min-w-0 flex-1 py-1" maxLength={200} disabled={disabled}
                             aria-label={`${h.projectId} ${h.grade} 자리 메우지 않는 사유`}
                             placeholder="사유(필수) 예: 남은 기간은 지금 인원으로 충분, 고객과 범위 축소 합의"
                             value={waivers[key]} onChange={(e) => onWaive(key, e.target.value)} />
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          <p className="mt-1.5 text-xs text-slate-600">
            메우지 않으면 그 사업의 그 등급 정원이 줄어 더 이상 빈자리로 세지 않습니다(예산은 그대로, 남은 기간 전체). 사유는 확정 기록과
            인사팀 검토 자료에 남고, 확정 취소·되돌리기를 하면 정원도 돌아옵니다.
          </p>
        </>
      )}
    </section>
  );
}
