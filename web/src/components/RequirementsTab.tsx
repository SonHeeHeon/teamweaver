import type { Meta } from "../api/types";
import { WeightSlider } from "./WeightSlider";

const DEFAULT_WEIGHT = 3;   // core/config.py 의 DEFAULT_WEIGHT 와 같은 값

interface Props {
  meta: Meta;
  weights: Record<string, number>;
  onWeightsChange: (w: Record<string, number>) => void;
  onRun: () => void;
  running: boolean;
}

export function RequirementsTab({ meta, weights, onWeightsChange, onRun, running }: Props) {
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <section>
        <h2 className="mb-3 text-lg font-semibold text-slate-900">스킬 가중치</h2>
        <p className="mb-4 text-sm text-slate-500">
          1~5점. 값이 높을수록 그 스킬의 적합도가 배치 점수에 크게 반영된다.
        </p>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          {meta.skills.map((s) => (
            <WeightSlider
              key={s}
              skill={s}
              value={weights[s] ?? DEFAULT_WEIGHT}
              onChange={(v) => onWeightsChange({ ...weights, [s]: v })}
            />
          ))}
        </div>
        <button
          onClick={onRun}
          disabled={running}
          className="mt-4 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
                     hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {running ? "최적화 중…" : "최적화 실행"}
        </button>
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
                  <td className="px-3 py-2 text-slate-900">{p.name}</td>
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
