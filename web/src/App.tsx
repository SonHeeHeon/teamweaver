import { useEffect, useState } from "react";
import { fetchMeta, streamOptimize } from "./api/client";
import type { Meta, PlanEvent } from "./api/types";
import { RequirementsTab } from "./components/RequirementsTab";
import { PlanCards } from "./components/PlanCards";
import { AssignmentTable } from "./components/AssignmentTable";

type Tab = "req" | "whatif";

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [tab, setTab] = useState<Tab>("req");
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [plans, setPlans] = useState<PlanEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchMeta().then(setMeta).catch((e) => setError(String(e)));
  }, []);

  async function run() {
    setRunning(true);
    setError(null);
    setPlans([]);
    setSelected(null);
    try {
      // Plan A가 먼저 도착하면 즉시 렌더된다 -- 대안 B/C/D를 기다리지 않는다.
      // 이것이 Plan 4의 SSE 점진 반환(A안)이 사용자 눈에 보이는 지점이다.
      for await (const ev of streamOptimize({ weights })) {
        if (ev.event === "plan") {
          setPlans((prev) => [...prev, ev.data]);
          setSelected((cur) => cur ?? ev.data.label);
          setTab("whatif");
        } else if (ev.event === "error") {
          setError(ev.data.message);
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  if (error && !meta) return <p className="p-8 text-red-600">불러오기 실패: {error}</p>;
  if (!meta) return <p className="p-8 text-slate-500">불러오는 중…</p>;

  const current = plans.find((p) => p.label === selected) ?? null;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white px-8 py-4">
        <h1 className="text-xl font-semibold text-slate-900">TeamWeaver</h1>
        <p className="text-sm text-slate-500">지식 그래프 · LLM 기반 지능형 인력 배치</p>
      </header>

      <nav className="flex gap-1 border-b border-slate-200 bg-white px-8">
        {([["req", "요건 설정"], ["whatif", "What-if 대시보드"]] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-4 py-3 text-sm font-medium ${
              tab === id
                ? "border-slate-900 text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="p-8">
        {error && (
          <p className="mb-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-700">{error}</p>
        )}
        {tab === "req" ? (
          <RequirementsTab meta={meta} weights={weights} onWeightsChange={setWeights}
                           onRun={run} running={running} />
        ) : (
          <div className="space-y-6">
            <PlanCards plans={plans} selected={selected} onSelect={setSelected} />
            {running && (
              <p className="text-sm text-slate-500">
                {plans.length === 0
                  ? "Plan A 계산 중… (최초 실행은 약 8초)"
                  : `대안 계산 중… (${plans.length}개 도착)`}
              </p>
            )}
            {current && (
              <AssignmentTable entries={current.entries} people={meta.people}
                               projects={meta.projects} />
            )}
          </div>
        )}
      </main>
    </div>
  );
}
