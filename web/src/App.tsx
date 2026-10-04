import { useEffect, useRef, useState } from "react";
import {
  downloadReport, fetchMeta, fetchSettings, postWhatif, saveSettings, SettingsConflictError,
  streamOptimize,
} from "./api/client";
import type {
  Meta, PlacementSettings, PlanEvent, SettingsResponse, Swap, WhatifResponse,
} from "./api/types";
import { RequirementsTab } from "./components/RequirementsTab";
import { PlanCards } from "./components/PlanCards";
import { AssignmentTable } from "./components/AssignmentTable";
import { NetworkGraph } from "./components/NetworkGraph";
import { SwapControl } from "./components/SwapControl";
import { BriefingPanel } from "./components/BriefingPanel";
import { SettingsTab } from "./components/SettingsTab";
import { describeChanges } from "./components/settingsFields";

type Tab = "req" | "whatif" | "settings";



export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [tab, setTab] = useState<Tab>("req");
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [plans, setPlans] = useState<PlanEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [whatif, setWhatif] = useState<WhatifResponse | null>(null);
  // whatif가 "어떤 교체"에 대한 결과인지. PDF가 스스로는 알 수 없으므로
  // downloadReport에 함께 실어 보낸다.
  const [lastSwap, setLastSwap] = useState<Swap | null>(null);
  const [whatifBusy, setWhatifBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  // 그래프에서 강조할 인물. 교체 "투입" 대상은 아직 배치 전이라 그래프에
  // 노드가 없다 -- 강조해도 보이지 않는다. 그래서 빠지는 쪽(out)을 강조해
  // "이 사람을 빼면 협업망 어디에 구멍이 나는지"를 보여준다.
  const [highlighted, setHighlighted] = useState<string | null>(null);
  // 진행 중인 what-if 요청이 지금 보고 있는 플랜에 속하는지 판별하는 세대
  // 카운터. selectPlan과 runSwap 시작 시 증가시키고, await 이후 세대가
  // 바뀌었으면(다른 플랜으로 넘어갔거나 새 스왑이 시작됐으면) 응답을 버린다
  // -- 그렇지 않으면 이전 플랜 entries로 계산된 브리핑이 새 플랜 아래
  // 표시된다(35초 안팎 걸리는 LLM 브리핑 창에서 실제로 발생 가능).
  const swapGen = useRef(0);
  // 관리자 배치 설정(K8). 서버에 저장된 조직 공통 값이다.
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  // 지금 보이는 플랜들을 *계산한* 기준. what-if·PDF는 최신 설정·가중치가 아니라
  // 이 스냅숏을 쓴다 -- 계산 뒤 설정이나 가중치를 바꾸고 교체를 검토하면 플랜과
  // 교체 점수가 서로 다른 기준이 되기 때문이다. null = 설정을 못 불러와 모델
  // 기본값으로 계산했다.
  const [planBasis, setPlanBasis] =
    useState<{ params: PlacementSettings | null; weights: Record<string, number> } | null>(null);

  useEffect(() => {
    fetchMeta().then(setMeta).catch((e) => setError(String(e)));
    fetchSettings().then(setSettings).catch((e) => setSettingsError(String(e)));
  }, []);

  async function run() {
    swapGen.current += 1;          // 재실행 -- 진행 중이던 what-if 응답도 무효화
    setRunning(true);
    setError(null);
    setPlans([]);
    setSelected(null);
    setWhatif(null);
    setLastSwap(null);
    setHighlighted(null);
    setWhatifBusy(false);
    // 실행할 때마다 서버 설정을 다시 읽는다: 페이지를 연 뒤 다른 관리자가 바꾼
    // 설정도 반영하고, 첫 로딩이 끝나기 전에 눌러도 모델 기본값으로 새지 않는다.
    // 읽기에 실패하면 마지막으로 알던 설정을 쓰고(없으면 모델 기본값) 경고한다.
    let params: PlacementSettings | null = settings?.settings ?? null;
    try {
      const fresh = await fetchSettings();
      setSettings(fresh);
      setSettingsError(null);
      params = fresh.settings;
    } catch (e) {
      setSettingsError(String(e));
    }
    setPlanBasis({ params, weights });
    try {
      // Plan A가 먼저 도착하면 즉시 렌더된다 -- 대안 B/C/D를 기다리지 않는다.
      // 이것이 Plan 4의 SSE 점진 반환(A안)이 사용자 눈에 보이는 지점이다.
      for await (const ev of streamOptimize(
          { weights, ...(params ? { milp_params: { ...params } } : {}) })) {
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
    swapGen.current += 1;          // 진행 중이던 what-if 응답을 무효화한다
    setSelected(label);
    setWhatif(null);
    setLastSwap(null);
    setHighlighted(null);
    setWhatifBusy(false);
  }

  async function runSwap(swap: Swap) {
    if (!current) return;
    const gen = ++swapGen.current;
    setWhatifBusy(true);
    setHighlighted(swap.out_person_id);
    try {
      const res = await postWhatif(current.entries, swap, planBasis?.weights ?? weights,
                                   planBasis?.params ?? null);
      if (gen !== swapGen.current) return;   // 그 사이 플랜이 바뀌었다 -- 폐기
      setWhatif(res);
      setLastSwap(swap);
    } catch (e) {
      if (gen !== swapGen.current) return;
      setError(String(e));
    } finally {
      if (gen === swapGen.current) setWhatifBusy(false);
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
        {([["req", "요건 설정"], ["whatif", "What-if 대시보드"], ["settings", "배치 설정"]] as const)
          .map(([id, label]) => (
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
        {settingsError && (
          <p role="alert" className="mb-4 rounded-md bg-amber-50 px-4 py-2 text-sm text-amber-800">
            배치 설정을 불러오지 못했다({settingsError}).{" "}
            {settings ? "마지막으로 불러온 설정으로 계산한다."
                      : "지금 계산하면 서버 모델 기본값으로 계산된다(배치 설정 미적용)."}
          </p>
        )}
        {tab === "settings" ? (
          settings ? (
            <SettingsTab data={settings}
                         onSave={async (s) => {
                           try {
                             setSettings(await saveSettings(s, settings.updated_at));
                             setSettingsError(null);
                           } catch (e) {
                             if (e instanceof SettingsConflictError) {
                               // 최신 값을 다시 읽어 폼을 갈아 끼우고, 이유를 알린다.
                               fetchSettings().then(setSettings).catch(() => {});
                             }
                             throw e;
                           }
                         }} />
          ) : (
            <p className="text-sm text-slate-500">
              {settingsError ? "배치 설정을 불러오지 못했다." : "배치 설정을 불러오는 중…"}
            </p>
          )
        ) : tab === "req" ? (
          <RequirementsTab meta={meta} weights={weights} onWeightsChange={setWeights}
                           onRun={run} running={running} />
        ) : (
          <div className="space-y-6">
            {plans.length > 0 && planBasis && settings && (
              planBasis.params === null
              || describeChanges(planBasis.params, settings.settings).length > 0) && (
              <p role="status" className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2
                                         text-sm text-amber-800">
                {planBasis.params
                  ? `이 결과는 이전 설정으로 계산됐다(바뀐 설정: ${
                      describeChanges(planBasis.params, settings.settings).join(", ")}). `
                  : "이 결과는 배치 설정 없이 서버 모델 기본값으로 계산됐다. "}
                현재 설정을 반영하려면 요건 설정 탭에서 다시 실행할 것.
                교체 검토와 PDF는 계산 당시 기준을 그대로 쓴다.
              </p>
            )}
            <PlanCards plans={plans} selected={selected} onSelect={selectPlan} />
            {current && (
              <button
                disabled={pdfBusy}
                onClick={async () => {
                  setPdfBusy(true);
                  try {
                    await downloadReport(current, whatif, lastSwap, planBasis?.params ?? null);
                  }
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
