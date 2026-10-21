import { useEffect, useState } from "react";
import { VIOLATION_LABEL } from "../scenarios/pickView";
import { DatasetChangedError } from "../api/client";
import type { AssignEntry, BaselineResult, BaselineSummary } from "../api/types";

/** "단순 규칙(기술 1등 우선) 대비" 카드(시연 확장 A, claude-a core.evaluate.baseline).
 *  같은 조건·같은 평가기로 나란히 채점한다. 자리당 평균 적합은 최적화가 더 낮을 수 있다(정원에 없는 등급을 예산 안에서
 *  더 넣어 총 기여를 늘리는 모델 특성) -- 숨기지 않고 함께 보인다. 사업 효과는 NOT_CALIBRATED. */
interface Props {
  entries: AssignEntry[];
  load: (entries: AssignEntry[]) => Promise<BaselineResult>;
  onDatasetChanged?: () => void;
  /** 이 탭이 보이고 있다 -- 처음 보일 때 저절로 계산한다(사용자 2026-10-20 "다시 비교 버튼을 안눌러도 디폴트로", 숨은 탭에서는 부르지 않는다) */
  active?: boolean;
}

const ROWS: { key: keyof BaselineSummary; label: string; better: "up" | "down" | null; digits?: number }[] = [
  { key: "quality", label: "배치 품질(기술+협업−익숙함)", better: "up" },
  { key: "skill", label: "기술 적합", better: "up" },
  { key: "synergy", label: "협업 보상", better: "up" },
  { key: "overfamiliarity", label: "익숙한 쌍 감점", better: "up" },
  { key: "unfilled_seats", label: "빈자리", better: "down", digits: 0 },
  { key: "avg_seat_fit", label: "자리당 평균 기술 적합", better: null, digits: 3 },
  { key: "assignments", label: "배치 건수", better: null, digits: 0 },
];

function fmt(v: number | null | undefined, digits = 2): string {
  return v == null ? "–" : v.toFixed(digits);
}

function violations(v: Record<string, number>): string {
  const n = Object.values(v).reduce((a, b) => a + b, 0);
  // 내부 코드(budget 등) 대신 화면 말로(2026-10-21 화면 검토 -- 후보 표 "넣으면 위반"과 같은 이름)
  return n === 0 ? "없음" : `${n}건 (${Object.entries(v).map(([k, c]) => `${VIOLATION_LABEL[k] ?? k} ${c}`).join(", ")})`;
}

export function BaselineCard({ entries, load, onDatasetChanged, active = true }: Props) {
  const [res, setRes] = useState<BaselineResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try { setRes(await load(entries)); }
    catch (e) {
      if (e instanceof DatasetChangedError && onDatasetChanged) onDatasetChanged();
      else setError(String(e));
    }
    finally { setBusy(false); }
  }
  // 명단이 바뀌면 쓰는 쪽이 key를 바꿔 새로 만든다 -- 처음 보일 때 한 번
  const [started, setStarted] = useState(false);
  useEffect(() => {
    if (active && !started) { setStarted(true); void run(); }
  }, [active, started]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-slate-900">단순 규칙(기술 1등 우선) 대비</h3>
        {busy && <span className="text-xs text-slate-500">계산 중…</span>}
        {error && !busy && (
          <button onClick={run} className="rounded-md border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-50">다시 시도</button>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-500">
        지금 명단을, 사람이 흔히 쓰는 단순한 방법(사업마다 기술이 가장 맞는 사람부터 채우기)으로 짠 명단과 같은 기준으로 채점해 나란히 보여 줍니다.
        "차이"가 초록이면 이 명단이 낫습니다.
      </p>
      {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
      {res && (
        <>
          <p className="mt-1 text-xs text-slate-500">단순 규칙: {res.rule}</p>
          <table className="mt-2 w-full text-xs">
            <thead className="text-left text-slate-500">
              <tr><th className="font-medium">항목</th><th className="font-medium">이 배치</th>
                  <th className="font-medium">단순 규칙</th><th className="font-medium">차이</th></tr>
            </thead>
            <tbody>
              {ROWS.map((r) => {
                const a = res.optimized[r.key] as number | null, b = res.baseline[r.key] as number | null;
                const d = a != null && b != null ? a - b : null;
                // 차이가 없으면 색을 칠하지 않는다(빈자리 0 → 0이 빨갛게 보였다)
                const good = d == null || r.better == null || Math.abs(d) <= 1e-9 ? null : (r.better === "up" ? d > 0 : d < 0);
                return (
                  <tr key={r.key} className="border-t border-slate-100">
                    <td className="py-1">{r.label}</td>
                    <td className="tabular-nums">{fmt(a, r.digits)}</td>
                    <td className="tabular-nums">{fmt(b, r.digits)}</td>
                    <td className={`tabular-nums ${good === true ? "text-emerald-700" : good === false ? "text-red-700" : ""}`}>
                      {d == null ? "–" : `${d > 0 ? "+" : ""}${d.toFixed(r.digits ?? 2)}`}
                    </td>
                  </tr>
                );
              })}
              <tr className="border-t border-slate-100">
                <td className="py-1">제약 위반</td><td>{violations(res.optimized.violations)}</td>
                <td>{violations(res.baseline.violations)}</td><td />
              </tr>
            </tbody>
          </table>
          {(res.optimized.avg_seat_fit ?? 0) < (res.baseline.avg_seat_fit ?? 0) && (
            <p className="mt-2 text-xs text-slate-600">
              자리당 평균 기술 적합은 이 배치가 더 낮다 — 정원에 없는 등급을 예산 안에서 더 넣어 총 기여를 늘리는 계산 특성이다.
            </p>
          )}
          <p className="mt-2 text-xs text-slate-500">{res.note}</p>
        </>
      )}
    </section>
  );
}
