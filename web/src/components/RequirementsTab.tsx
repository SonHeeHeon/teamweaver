import type { Meta } from "../api/types";
import { WeightSlider } from "./WeightSlider";
import { LifecycleChips } from "./LifecycleChips";
import { projectPeriod } from "./lifecycle";

const DEFAULT_WEIGHT = 3;   // core/config.py 의 DEFAULT_WEIGHT 와 같은 값

interface Props {
  meta: Meta;
  weights: Record<string, number>;
  onWeightsChange: (w: Record<string, number>) => void;
  onRun: () => void;
  running: boolean;
  /** 실행 중 "계산 멈추기"(그때까지의 해를 쓰고 끝냄). 없으면(캐시·미리 계산이라 멈출 것이 없음) 버튼을 숨긴다. */
  onStop?: (() => void) | null;
  stopping?: boolean;
  /** 운영 중 시연 묶음이면 '전부 다시 짜기'의 한계를 안내한다(claude-a 리허설 요청 2026-10-07). */
  operating?: boolean;
  onOpenOperating?: () => void;
}

export function RequirementsTab({ meta, weights, onWeightsChange, onRun, running, onStop, stopping, operating,
                                 onOpenOperating }: Props) {
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
      <section>
        <h2 className="mb-3 text-lg font-semibold text-slate-900">스킬 가중치</h2>
        <p className="mb-4 text-sm text-slate-500">
          1~5점. 값이 높을수록 그 스킬의 적합도가 배치 점수에 크게 반영된다.
        </p>
        <div className="panel grid gap-x-6 px-4 py-3 sm:grid-cols-2">
          {meta.skills.map((s) => (
            <WeightSlider
              key={s}
              skill={s}
              value={weights[s] ?? DEFAULT_WEIGHT}
              onChange={(v) => onWeightsChange({ ...weights, [s]: v })}
            />
          ))}
        </div>
        <div className="sticky bottom-0 z-10 -mx-1 mt-4 flex items-center gap-2 border-t border-line bg-canvas/95 px-1 py-3 backdrop-blur">
          <button onClick={onRun} disabled={running} className="btn-primary whitespace-nowrap px-5">
            {running ? "최적화 중…" : "최적화 실행"}
          </button>
          {running && onStop && <StopButton onStop={onStop} stopping={!!stopping} className="py-2" />}
          <span className="text-xs text-slate-500">안 A부터 먼저 보이고, 대안 B~D가 이어서 도착합니다.</span>
        </div>
        {operating && (
          <p role="note" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            지금은 <b>운영 중</b> 데이터다(대부분이 진행 사업에 배치돼 있다). "최적화 실행"은 모두를 처음부터 다시 짠다 —
            사업 정원·예산이 지금 명단에 맞춰져 있어 200·300명은 계산 시간 안에 빈자리가 남을 수 있다.
            신규 제안 편성·사업 보강은{" "}
            {onOpenOperating
              ? <button onClick={onOpenOperating} className="font-medium underline">신규 사업 편성 화면</button>
              : "신규 사업 편성·긴급 투입 화면"}
            에서 본다.
          </p>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold text-slate-900">
          프로젝트 제약 ({meta.projects.length}건)
        </h2>
        <div className="max-h-[28rem] overflow-y-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-slate-50 text-left text-slate-600">
              <tr>
                <th className="px-3 py-2 font-medium">프로젝트</th>
                <th className="px-3 py-2 font-medium">구분</th>
                <th className="px-3 py-2 font-medium">정원</th>
                <th className="px-3 py-2 text-right font-medium">월 예산</th>
              </tr>
            </thead>
            <tbody>
              {meta.projects.map((p) => (
                <tr key={p.id} className="border-t border-slate-100">
                  <td className="px-3 py-2 text-slate-900">
                    {p.name}<LifecycleChips lc={p.lifecycle} name={p.name} />
                    {/* 기간은 이름 아래 줄 -- 따로 열을 두면 반쪽 폭 표에서 정원 칸이 등급마다 접힌다(10-19 브라우저 점검) */}
                    {meta.horizon_start && <span className="block text-xs text-slate-500">{projectPeriod(p, meta.horizon_start)}</span>}
                  </td>
                  <td className="px-3 py-2 text-slate-600">{p.sector} · {p.phase}</td>
                  <td className="px-3 py-2 text-slate-600">
                    {Object.entries(p.grade_headcount).map(([g, n]) => `${g} ${n}`).join(", ")}
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
