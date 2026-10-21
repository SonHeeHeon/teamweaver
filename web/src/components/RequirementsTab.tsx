import type { Meta, Project } from "../api/types";
import { LifecycleChips } from "./LifecycleChips";
import { projectPeriod } from "./lifecycle";

const GRADE_ORDER = ["특급", "고급", "중급", "초급"];   // 정원은 위 등급부터(원천 행 순서대로 섞여 보였다, 2026-10-21 화면 검토)
/** 최소 레벨 → 경력(입력 단계 경계 12/36/60/96개월, core/ingest/convert.MONTH_BANDS) */
const LEVEL_YEARS: Record<number, string> = { 1: "", 2: "1년+", 3: "3년+", 4: "5년+", 5: "8년+" };
const LEVEL_MONTHS: Record<number, number> = { 1: 0, 2: 12, 3: 36, 4: 60, 5: 96 };

/** "메타데이터 관리 1년+ ×3" -- 사업이 등록한 요구 기술 그대로(계산이 쓰는 값) */
function requirementText(r: NonNullable<Project["requirements"]>[number]): string {
  const years = LEVEL_YEARS[r.min_level] ?? `레벨 ${r.min_level}+`;
  return `${r.skill}${years ? ` ${years}` : ""} ×${r.headcount}`;
}

interface Props {
  meta: Meta;
  onRun: () => void;
  running: boolean;
  /** 실행 중 "계산 멈추기"(그때까지의 해를 쓰고 끝냄). 없으면(캐시·미리 계산이라 멈출 것이 없음) 버튼을 숨긴다. */
  onStop?: (() => void) | null;
  stopping?: boolean;
  /** 운영 중 시연 묶음이면 '전부 다시 짜기'의 한계를 안내한다(claude-a 리허설 요청 2026-10-07). */
  operating?: boolean;
  onOpenOperating?: () => void;
}

/** 연간 계획 ① 조건·계산(2026-10-21 사용자 결정): 예전 "기술 가중치"(회사 전체 하나, 사업 안 기술 사이 무게만 바꿈)는 없앴다 --
 *  계산은 사업마다 등록한 요구 기술·등급별 인원·예산을 그대로 쓰고, 그것을 이 표에서 보인다. 요청의 weights 칸은 옛 기록 재현용으로 서버에 남는다. */
export function RequirementsTab({ meta, onRun, running, onStop, stopping, operating, onOpenOperating }: Props) {
  return (
    <div className="space-y-4">
      <section>
        <h2 className="text-lg font-semibold text-slate-900">사업 조건 ({meta.projects.length}건)</h2>
        <p className="mb-3 mt-0.5 text-sm text-slate-500">
          계산은 사업마다 등록한 등급별 인원 · 요구 기술 · 한 달 예산을 그대로 씁니다. 사람의 기술이 요구 기술에 맞을수록, 함께 일해 본 사이일수록 점수가 높습니다.
          조건은 신규 사업 편성(등록·수정)이나 데이터 묶음에서 바꿉니다.
        </p>
        <div className="max-h-[34rem] overflow-y-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2 font-medium">프로젝트</th>
                <th className="px-3 py-2 font-medium">구분</th>
                <th className="px-3 py-2 font-medium">정원</th>
                <th className="px-3 py-2 font-medium" title="필요한 기술 · 경력(레벨) · 그 기술이 필요한 인원. 경력은 기술 이력의 개월 수로 정한 레벨이다(1년·3년·5년·8년 경계).">요구 기술 ⓘ</th>
                <th className="px-3 py-2 text-right font-medium" title="한 달에 쓸 수 있는 인건비(가상 비용 점수). 배치한 사람들의 등급별 단가 합이 이 안이어야 합니다.">월 예산 ⓘ</th>
              </tr>
            </thead>
            <tbody>
              {meta.projects.map((p) => (
                <tr key={p.id} className="border-t border-slate-100 align-top">
                  <td className="px-3 py-2 text-slate-900">
                    {p.name}<LifecycleChips lc={p.lifecycle} name={p.name} />
                    {/* 기간은 이름 아래 줄 -- 따로 열을 두면 반쪽 폭 표에서 정원 칸이 등급마다 접힌다(10-19 브라우저 점검) */}
                    {meta.horizon_start && <span className="block text-xs text-slate-500">{projectPeriod(p, meta.horizon_start)}</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-slate-600">{p.sector} · {p.phase}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-slate-600">
                    {Object.entries(p.grade_headcount).sort(([a], [b]) => GRADE_ORDER.indexOf(a) - GRADE_ORDER.indexOf(b))
                      .map(([g, n]) => `${g} ${n}`).join(", ")}
                  </td>
                  <td className="px-3 py-2 text-slate-700">
                    {(p.requirements ?? []).length === 0 ? <span className="text-slate-400">없음</span> : (
                      <ul className="flex flex-wrap gap-1" aria-label={`${p.id} 요구 기술`}>
                        {(p.requirements ?? []).map((r) => (
                          <li key={r.skill} className="rounded bg-slate-100 px-1.5 py-0.5 text-xs"
                              title={`레벨 ${r.min_level} 이상(경력 ${LEVEL_MONTHS[r.min_level] ?? "?"}개월 이상) · ${r.headcount}명에게 필요`}>
                            {requirementText(r)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-900">
                    {p.monthly_budget.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <div className="sticky bottom-0 z-10 -mx-1 flex items-center gap-2 border-t border-line bg-canvas/95 px-1 py-3 backdrop-blur">
        <button onClick={onRun} disabled={running} className="btn-primary whitespace-nowrap px-5">
          {running ? "최적화 중…" : "최적화 실행"}
        </button>
        {running && onStop && <StopButton onStop={onStop} stopping={!!stopping} className="py-2" />}
        <span className="text-xs text-slate-500">안 A부터 먼저 보이고, 대안 B~D가 이어서 도착합니다. 다 되면 ② 편성안 비교로 넘어갑니다.</span>
      </div>
      {operating && (
        <p role="note" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          지금은 <b>운영 중</b> 데이터다(대부분이 진행 사업에 배치돼 있다). "최적화 실행"은 모두를 처음부터 다시 짠다 —
          사업 정원·예산이 지금 명단에 맞춰져 있어 200·300명은 계산 시간 안에 빈자리가 남을 수 있다.
          신규 제안 편성·사업 보강은{" "}
          {onOpenOperating
            ? <button onClick={onOpenOperating} className="font-medium underline">신규 사업 편성 화면</button>
            : "신규 사업 편성·긴급 투입 화면"}
          에서, 지금 배치에서 몇 명만 바꿔 보려면 What-if에서 본다.
        </p>
      )}
    </div>
  );
}


/** "계산 멈추기": 지금 푸는 안은 그때까지 찾은 가장 좋은 해로 끝내고(검증은 그대로), 다음 대안은 시작하지 않는다. */
export function StopButton({ onStop, stopping, className = "ml-2 mt-4 py-2" }:
                           { onStop: () => void; stopping: boolean; className?: string }) {
  return (
    <button onClick={onStop} disabled={stopping}
            title="지금 계산 중인 안은 그때까지 찾은 가장 좋은 배치로 끝내고, 다음 대안은 계산하지 않는다"
            className={`${className} rounded-md border border-red-300 bg-white px-3 text-sm text-red-700
                        hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60`}>
      {stopping ? "멈추는 중…" : "계산 멈추기"}
    </button>
  );
}
