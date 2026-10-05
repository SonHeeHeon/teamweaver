import { isAlloc, type AppliedStep, type Person } from "../api/types";
import { formatAlloc, formatMonthly } from "./allocFormat";

interface Props {
  label: string;
  history: AppliedStep[];
  violations: string[];
  people: Person[];
  onUndo: () => void;
  onReset: () => void;
}

/** 달별 값 요약: 모두 같으면 한 비율, 다르면 구간. */
function describe(m: Record<string, number>): string {
  const vals = Object.values(m);
  return vals.every((v) => v === vals[0]) ? `${formatAlloc(vals[0])}(모든 달)` : formatMonthly(m);
}

const signed = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(4)}`;

/** 적용한 교체 이력(K10). 명단은 최적화 결과가 아니라 사람이 고친 것임을 밝힌다. */
export function AppliedPanel({ label, history, violations, people, onUndo, onReset }: Props) {
  if (history.length === 0) return null;
  const name = new Map(people.map((p) => [p.id, p.name]));
  const nameOf = (id: string) => name.get(id) ?? id;
  const allocs = history.filter(isAlloc).length;
  const title = allocs === 0 ? `교체 ${history.length}건 적용`
    : `변경 ${history.length}건 적용(교체 ${history.length - allocs}건 · 투입률 조정 ${allocs}건)`;
  return (
    <section className="rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm">
      <p className="font-semibold text-indigo-900">
        Plan {label} · {title} — 최적화 결과가 아니라 사람이 고친 명단이다
      </p>
      <ol className="mt-2 list-inside list-decimal space-y-1 text-indigo-900">
        {history.map((h, k) => (
          <li key={k}>
            {isAlloc(h)
              ? <>투입률 조정: {nameOf(h.person_id)} ({h.project_id}) {describe(h.monthly_alloc)}</>
              : <>{nameOf(h.out_person_id)} → {nameOf(h.in_person_id)} ({h.project_id})</>}
            {" "}· Δ {signed(h.objective_delta)}
            {h.warnings.length > 0 && (
              <span className="ml-2 font-medium text-red-700">경고: {h.warnings.join(", ")}</span>
            )}
          </li>
        ))}
      </ol>
      {violations.length > 0 && (
        <p role="alert" className="mt-2 rounded border border-red-300 bg-red-50 px-3 py-2 text-red-800">
          지금 명단의 제약 위반: {violations.join(" / ")}
        </p>
      )}
      {allocs > 0 && (
        <p className="mt-2 text-xs text-indigo-800">
          달별 투입률이 들어간 명단은 최적화율 분모(이론상 최댓값)를 '달마다 따로' 기준으로 계산한다. 이 상한이 더 커서
          조정이 작아도 최적화율이 몇 % 낮아 보일 수 있다 -- 품질이 그만큼 나빠졌다는 뜻이 아니다.
        </p>
      )}
      <p className="mt-2 text-xs text-indigo-800">
        적용 내역은 서버에 저장되어 같은 데이터·설정·가중치로 계산한 이 플랜을 여는 모든 사용자에게 공유된다.
      </p>
      <div className="mt-3 flex gap-2">
        <button onClick={onUndo}
                className="rounded-md border border-indigo-300 bg-white px-3 py-1.5 font-medium text-indigo-900">
          마지막 적용 취소
        </button>
        <button onClick={onReset}
                className="rounded-md border border-indigo-300 bg-white px-3 py-1.5 font-medium text-indigo-900">
          원래 플랜으로
        </button>
      </div>
    </section>
  );
}
