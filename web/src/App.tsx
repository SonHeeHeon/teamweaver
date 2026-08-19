import { useEffect, useState } from "react";
import { downloadReport, fetchMeta, postWhatif, streamOptimize } from "./api/client";
import type { Meta, PlanEvent, Swap, WhatifResponse } from "./api/types";
import { RequirementsTab } from "./components/RequirementsTab";
import { PlanCards } from "./components/PlanCards";
import { AssignmentTable } from "./components/AssignmentTable";
import { NetworkGraph } from "./components/NetworkGraph";
import { SwapControl } from "./components/SwapControl";
import { BriefingPanel } from "./components/BriefingPanel";

type Tab = "req" | "whatif";

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [tab, setTab] = useState<Tab>("req");
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [plans, setPlans] = useState<PlanEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [whatif, setWhatif] = useState<WhatifResponse | null>(null);
  const [whatifBusy, setWhatifBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  // 그래프에서 강조할 인물. 교체 "투입" 대상은 아직 배치 전이라 그래프에
  // 노드가 없다 -- 강조해도 보이지 않는다. 그래서 빠지는 쪽(out)을 강조해
  // "이 사람을 빼면 협업망 어디에 구멍이 나는지"를 보여준다.
  const [highlighted, setHighlighted] = useState<string | null>(null);

  useEffect(() => {
    fetchMeta().then(setMeta).catch((e) => setError(String(e)));
  }, []);

  async function run() {
    setRunning(true);
    setError(null);
    setPlans([]);
    setSelected(null);
    setWhatif(null);
    setHighlighted(null);
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

  /** 플랜을 바꾸면 이전 What-if 결과는 무의미하다 -- 그 델타·브리핑은 이전
   *  플랜의 entries를 기준으로 계산된 값이라, 그대로 두면 지금 보고 있는
   *  플랜의 결과인 것처럼 읽힌다. */
  function selectPlan(label: string) {
    setSelected(label);
    setWhatif(null);
    setHighlighted(null);
  }

  async function runSwap(swap: Swap) {
    if (!current) return;
    setWhatifBusy(true);
    setHighlighted(swap.out_person_id);
    try {
      setWhatif(await postWhatif(current.entries, swap, weights));
    } catch (e) {
      setError(String(e));
    } finally {
      setWhatifBusy(false);
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
            <PlanCards plans={plans} selected={selected} onSelect={selectPlan} />
            {current && (
              <button
                disabled={pdfBusy}
                onClick={async () => {
                  setPdfBusy(true);
                  try { await downloadReport(current, whatif); }
                  catch (e) { setError(String(e)); }
                  finally { setPdfBusy(false); }
                }}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm
                           font-medium text-slate-700 hover:bg-slate-50
                           disabled:cursor-not-allowed disabled:text-slate-400"
              >
                {pdfBusy ? "PDF 생성 중…" : "PDF 내려받기"}
              </button>
            )}
            {running && (
              <p className="text-sm text-slate-500">
                {plans.length === 0
                  ? "Plan A 계산 중… (최초 실행은 약 8초)"
                  : `대안 계산 중… (${plans.length}개 도착)`}
              </p>
            )}
            {current && (
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="space-y-6">
                  <NetworkGraph people={meta.people} coworks={meta.coworks}
                                entries={current.entries} highlight={highlighted} />
                  {/* key로 remount -- 플랜이 바뀌면 이전 플랜에서 고른
                      교체 대상/투입이 남아 있으면 안 된다. */}
                  <SwapControl key={current.label} people={meta.people}
                               entries={current.entries}
                               onSwap={runSwap} busy={whatifBusy} />
                  <BriefingPanel result={whatif} loading={whatifBusy} />
                </div>
                <AssignmentTable entries={current.entries} people={meta.people}
                                 projects={meta.projects} />
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
