import { useEffect, useRef, useState } from "react";
import {
  adminLogout, AdminLoginRequiredError, type AdminStatus, applyAlloc, applySwap, DatasetChangedError, EditsConflictError, downloadReport, loadPlanEdits, savePlanEdits, fetchActiveDataset, fetchAdminStatus, fetchMeta,
  fetchSettings, postWhatif, saveSettings, SettingsConflictError,
  streamOptimize,
} from "./api/client";
import type {
  AllocChange, AssignEntry,  AppliedStep, AppliedSwap, DatasetInfo, Meta, PlacementSettings, PlanEvent, SettingsResponse, Swap,
  WhatifResponse,
} from "./api/types";

/** 플랜 하나에 적용한 교체(K10). stack[k]는 k+1번째 교체를 적용한 뒤의 명단·지표와 그 명단
 *  전체의 위반 문장이다 -- 되돌리기는 맨 위를 빼면 된다. 서버는 저장하지 않는다. */
interface PlanEdit {
  stack: { plan: PlanEvent; violations: string[] }[];
  history: AppliedStep[];
}
import { RequirementsTab } from "./components/RequirementsTab";
import { PlanCards } from "./components/PlanCards";
import { AssignmentTable } from "./components/AssignmentTable";
import { NetworkGraph } from "./components/NetworkGraph";
import { SwapControl } from "./components/SwapControl";
import { BriefingPanel } from "./components/BriefingPanel";
import { SettingsTab } from "./components/SettingsTab";
import { DatasetTab } from "./components/DatasetTab";
import { AdminLogin } from "./components/AdminLogin";
import { ApplyControl } from "./components/ApplyControl";
import { AppliedPanel } from "./components/AppliedPanel";
import { describeChanges } from "./components/settingsFields";
import { stepBody } from "./api/types";

type Tab = "req" | "whatif" | "settings" | "data";



function AdminTokenField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="block max-w-sm text-sm text-slate-700">
      관리자 토큰
      <input type="password" value={value} onChange={(e) => onChange(e.target.value)}
             autoComplete="off" aria-describedby="admin-token-help"
             className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1 text-sm" />
      <span id="admin-token-help" className="mt-1 block text-xs text-slate-500">
        이 서버는 설정 저장·데이터 전환에 관리자 토큰을 요구한다. 토큰은 이 화면 메모리에만 둔다.
      </span>
    </label>
  );
}

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
    useState<{ params: PlacementSettings | null; weights: Record<string, number>;
               datasetVersion: string } | null>(null);
  // 관리자 토큰(K9): 서버가 TEAMWEAVER_ADMIN_TOKEN을 요구할 때만 입력받는다. 메모리에만 둔다.
  // 관리자 상태(K14). 토큰만 설정된 서버(스크립트용)면 예전처럼 토큰 입력칸을 쓴다.
  const [admin, setAdmin] = useState<AdminStatus | null>(null);
  const [adminToken, setAdminToken] = useState("");
  const [loginReason, setLoginReason] = useState<string | null>(null);
  const adminRequired = !!admin?.token_required;
  const needLogin = !!admin?.login_required && !admin.logged_in;
  function refreshAdmin() {
    fetchAdminStatus().then(setAdmin).catch(() => {});
  }
  /** 관리자 동작이 401: 세션이 없거나 끝났다 -- 로그인 화면으로. */
  function adminExpired() {
    setLoginReason("로그인이 필요하거나 세션이 끝났다. 다시 로그인할 것.");
    refreshAdmin();
  }

  // 지금 서버가 계산에 쓰는 데이터셋(K9). 업로드로 바뀌면 진행 중이던 최적화
  // 스트림의 남은 플랜도 버린다 -- runGen이 바뀌면 이전 데이터셋의 결과다.
  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const runGen = useRef(0);
  // 플랜별 적용 상태(K10). 재실행·데이터셋 전환이면 비운다. 다른 플랜을 봐도 유지한다.
  const [edits, setEdits] = useState<Record<string, PlanEdit>>({});
  // 적용 상태의 최신값(K13). 저장 본문은 렌더 클로저가 아니라 여기서 만든다 -- 빠른 적용·취소나
  // 복원과 겹칠 때 옛 상태로 저장하지 않게.
  const editsRef = useRef<Record<string, PlanEdit>>({});
  function commitEdits(next: Record<string, PlanEdit>) {
    editsRef.current = next;
    setEdits(next);
  }
  // 플랜별 저장 요청 줄(앞 요청이 끝난 뒤 다음을 보낸다). 서버가 매긴 revision을 플랜별로 기억해
  // 저장 때 보낸다 -- 서버는 다르면 409(compare-and-set). 클라이언트 시각을 믿지 않는다(Codex 3차).
  // 작거나 같은 요청을 무시한다 -- 다른 탭과 섞여도 마지막 상태가 이긴다(Opus 리뷰 M1).
  const saveChain = useRef<Record<string, Promise<void>>>({});
  // 저장 줄·revision은 플랜 라벨이 아니라 서명(plan_token)별로 둔다 -- 재실행하면 같은 "A"라도
  // 다른 계산(다른 저장 키)이다(Opus 검증 M1). chainGen이 바뀌면 줄에 남은 저장은 버린다(충돌 뒤).
  const knownRevision = useRef<Record<string, number>>({});
  const chainGen = useRef<Record<string, number>>({});
  // 플랜별 로컬 변경 세대(적용·취소·원래대로마다 +1). 늦게 온 복원이 그사이의 변경을 덮지 못하게.
  const mutGen = useRef<Record<string, number>>({});
  const bumpMut = (label: string) => { mutGen.current[label] = (mutGen.current[label] ?? 0) + 1; };
  // 지금 검토 결과(whatif)가 기준으로 삼은 명단. 적용 직전에 현재 명단과 같은지 본다.
  const reviewBase = useRef<AssignEntry[] | null>(null);
  const selectedRef = useRef<string | null>(null);
  const [applyBusy, setApplyBusy] = useState(false);
  // 저장된 적용 교체를 불러왔다는 안내(K13). 재실행·전환이면 지운다.
  const [notice, setNotice] = useState<string | null>(null);
  // 요청한 대안 중 조건을 만족해 나온 수(C2). 모자라면 "대안 없음" 안내를 플랜 카드 아래에 둔다.
  const [altShortfall, setAltShortfall] =
    useState<{ found: number; requested: number; solverFailed: boolean } | null>(null);
  // 적용 요청의 세대. 플랜 전환·재실행·되돌리기·선택 변경이면 올려, 늦게 온 응답(성공·409
  // 모두)을 버리고 버튼 잠금도 바로 푼다 -- 늦은 409가 더 최신 실행의 플랜을 지우지 않게.
  const applyGen = useRef(0);
  function cancelApply() {
    applyGen.current += 1;
    setApplyBusy(false);
  }

  /** 교체 선택이 바뀌었다: 이전 선택으로 검토한 결과는 화면의 선택과 맞지 않으므로 버린다. */
  function selectionChanged() {
    if (!whatif && !lastSwap && !whatifBusy) return;
    swapGen.current += 1;
    cancelApply();
    setWhatif(null);
    setLastSwap(null);
    setWhatifBusy(false);
  }

  useEffect(() => {
    fetchMeta().then(setMeta).catch((e) => setError(String(e)));
    fetchSettings().then(setSettings).catch((e) => setSettingsError(String(e)));
    fetchActiveDataset().then(setDataset).catch(() => setDataset(null));
    refreshAdmin();
  }, []);

  // 관리자 탭을 열 때마다 로그인 상태를 다시 본다(세션 만료·다른 탭의 로그아웃 반영).
  useEffect(() => {
    if (tab === "settings" || tab === "data") refreshAdmin();
  }, [tab]);

  /** 활성 데이터셋이 바뀌었다: 이전 데이터의 플랜·교체 검토·가중치(기술 이름이 다를 수
   *  있다)를 모두 비우고 meta를 새로 읽는다. 옛 플랜으로 교체를 검토하면 없는 ID라 실패한다. */
  async function datasetSwitched(info: DatasetInfo) {
    runGen.current += 1;
    swapGen.current += 1;
    cancelApply();
    setDataset(info);
    setPlans([]);
    setAltShortfall(null);
    commitEdits({});
    setNotice(null);
    setSelected(null);
    setWhatif(null);
    setLastSwap(null);
    setHighlighted(null);
    setWhatifBusy(false);
    setPlanBasis(null);
    setWeights({});
    setRunning(false);
    try {
      const fresh = await fetchMeta();
      setMeta(fresh);
      if (fresh.dataset_version !== info.version) {
        setError("데이터셋이 그사이 다시 바뀌었다. 데이터 탭에서 지금 쓰는 데이터를 확인할 것.");
      }
    } catch (e) {
      // 옛 meta를 남기면 새 데이터셋 결과에 옛 이름이 붙는다 -- 비우고 실패를 보여 준다.
      setMeta(null);
      setError(`데이터셋 전환 후 메타 정보를 불러오지 못했다: ${String(e)}`);
    }
  }

  /** 다른 사용자가 서버의 데이터셋을 바꿔 요청이 409로 거부됐다: 새 데이터로 다시 불러온다. */
  async function externalSwitch() {
    try {
      const info = await fetchActiveDataset();
      await datasetSwitched(info);
      setError("다른 사용자가 데이터셋을 바꿨다. 화면을 새 데이터로 다시 불러왔으니 다시 실행할 것.");
    } catch (e) {
      setError(String(e));
    }
  }

  async function run() {
    const gen = ++runGen.current;
    swapGen.current += 1;          // 재실행 -- 진행 중이던 what-if 응답도 무효화
    cancelApply();
    setRunning(true);
    setError(null);
    setPlans([]);
    setAltShortfall(null);
    commitEdits({});
    setNotice(null);
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
    if (gen !== runGen.current || !meta) return;   // 설정을 읽는 사이 데이터셋이 바뀌었다
    const datasetVersion = meta.dataset_version;
    setPlanBasis({ params, weights, datasetVersion });
    try {
      // Plan A가 먼저 도착하면 즉시 렌더된다 -- 대안 B/C/D를 기다리지 않는다.
      // 이것이 Plan 4의 SSE 점진 반환(A안)이 사용자 눈에 보이는 지점이다.
      for await (const ev of streamOptimize(
          { weights, dataset_version: datasetVersion,
            ...(params ? { milp_params: { ...params } } : {}) })) {
        if (gen !== runGen.current) break;   // 그사이 데이터셋이 바뀌었다 -- 남은 결과 폐기
        if (ev.event === "plan") {
          setPlans((prev) => [...prev, ev.data]);
          void restoreEdits(ev.data, gen);
          setSelected((cur) => cur ?? ev.data.label);
          setTab("whatif");
        } else if (ev.event === "done") {
          const requested = ev.data.requested_alternatives;
          const found = Math.max(0, ev.data.count - 1);
          if (requested !== undefined && found < requested)
            setAltShortfall({ found, requested, solverFailed: ev.data.stop_reason === "time_limit" });
        } else if (ev.event === "error") {
          setError(ev.data.message);
        }
      }
    } catch (e) {
      if (e instanceof DatasetChangedError) await externalSwitch();
      else if (gen === runGen.current) setError(String(e));
    } finally {
      if (gen === runGen.current) setRunning(false);
    }
  }

  /** 플랜을 바꾸면 이전 What-if 결과는 무의미하다 -- 그 델타·브리핑은 이전
   *  플랜의 entries를 기준으로 계산된 값이라, 그대로 두면 지금 보고 있는
   *  플랜의 결과인 것처럼 읽힌다. */
  function selectPlan(label: string) {
    swapGen.current += 1;          // 진행 중이던 what-if 응답을 무효화한다
    cancelApply();
    setSelected(label);
    setWhatif(null);
    setLastSwap(null);
    setHighlighted(null);
    setWhatifBusy(false);
  }

  selectedRef.current = selected;

  async function runSwap(swap: Swap) {
    if (!current) return;
    const gen = ++swapGen.current;
    const base = current.entries;
    setWhatifBusy(true);
    setHighlighted(swap.out_person_id);
    try {
      const res = await postWhatif(current.entries, swap, planBasis?.weights ?? weights,
                                   planBasis?.params ?? null, planBasis?.datasetVersion ?? null);
      if (gen !== swapGen.current) return;   // 그 사이 플랜이 바뀌었다 -- 폐기
      reviewBase.current = base;
      setWhatif(res);
      setLastSwap(swap);
    } catch (e) {
      if (gen !== swapGen.current) return;          // 취소된 검토의 오류(늦은 409 포함)는 무시
      if (e instanceof DatasetChangedError) { await externalSwitch(); return; }
      setError(String(e));
    } finally {
      if (gen === swapGen.current) setWhatifBusy(false);
    }
  }

  /** 이 플랜에 저장해 둔 적용 교체가 있으면 서버가 원 플랜에서 다시 적용한 단계로 스택을
   *  복원한다(K13). 저장 키는 원 플랜 서명이라, 같은 데이터·규칙·가중치로 다시 계산한 플랜에만 붙는다. */
  async function restoreEdits(plan: PlanEvent, gen: number, force = false) {
    if (!plan.plan_token) return;
    const startMut = mutGen.current[plan.label] ?? 0;
    try {
      const saved = await loadPlanEdits(plan.plan_token);
      if (gen !== runGen.current) return;
      // 그사이 이 플랜에서 적용·취소가 있었으면(세대가 바뀜) 그 상태가 우선이다 -- 스택이 비어
      // 있어도(취소) 덮지 않는다(Codex 3차). force는 저장 충돌 후 서버 상태로 맞출 때.
      if (!force && (mutGen.current[plan.label] ?? 0) !== startMut) return;
      knownRevision.current[plan.plan_token] = saved.revision;
      if (!force && saved.steps.length === 0) return;
      const edit: PlanEdit = {
        stack: saved.steps.map((st) => ({
          plan: { ...plan, entries: st.entries, objective: st.objective, fulfillment: st.fulfillment,
                  optimization_ratio: st.optimization_ratio, unfilled: st.unfilled },
          violations: st.evaluation.violations.map((v) => v.message) })),
        history: saved.steps.map((st) => ({ ...st.swap, objective_delta: st.objective_delta,
                                             feasible: st.feasible, warnings: st.warnings })),
      };
      const next = { ...editsRef.current };
      if (edit.history.length) next[plan.label] = edit; else delete next[plan.label];
      bumpMut(plan.label);
      commitEdits(next);
      // 명단이 바뀌었으니 이 플랜의 진행 중 검토·적용은 무효다(옛 명단 기준, Codex 3차).
      if (selectedRef.current === plan.label) {
        swapGen.current += 1;
        cancelApply();
        setWhatif(null);
        setLastSwap(null);
        setWhatifBusy(false);
        reviewBase.current = null;
      }
      setNotice(force
        ? `Plan ${plan.label}: 다른 화면에서 먼저 저장해 이 변경은 저장되지 않았다. 최신 저장분(${edit.history.length}건)을 불러왔다.`
        : `Plan ${plan.label}: 저장해 둔 적용 교체 ${edit.history.length}건을 불러왔다.`);
    } catch (e) {
      // 복원 실패는 작업을 막지 않는다 -- 전역 오류가 아니라 안내로 알린다.
      if (gen === runGen.current) setNotice(`Plan ${plan.label}: 저장해 둔 적용 교체를 불러오지 못했다(${String(e)}).`);
    }
  }

  /** 플랜의 적용 교체 목록을 서버에 저장한다(빈 목록이면 지운다). 화면 상태는 이미 바뀐 뒤라,
   *  실패하면 "저장 안 됨"을 알린다 -- 조용히 넘어가면 새로고침 때 사라진다. */
  function persistEdits(label: string, history: AppliedStep[]) {
    const base = plans.find((p) => p.label === label);
    if (!base?.plan_token || !planBasis) return;
    const token = base.plan_token;
    const body = {
      plan_label: label, base_entries: base.entries, weights: planBasis.weights,
      milp_params: planBasis.params, dataset_version: planBasis.datasetVersion,
      swaps: history.map(stepBody),
    };
    const runAt = runGen.current;
    const enqueuedAt = chainGen.current[token] ?? 0;
    // 저장은 매번 이력 전체를 보낸다. 줄에서 기다리는 사이 이 플랜의 화면 상태가 다시 바뀌었으면
    // (다음 적용·취소, 409 뒤 서버 상태 복원) 이 저장은 낡았다 -- 특히 강제 복원 전에 한 적용은
    // 옛 이력 기준이라 보내면 다른 화면이 먼저 저장한 것을 덮는다(K13 남은 SHOULD).
    const mutAt = mutGen.current[label] ?? 0;
    const prev = saveChain.current[token] ?? Promise.resolve();
    // expected_revision은 보내는 순간의 값을 쓴다(앞 저장의 응답으로 갱신된 뒤).
    saveChain.current[token] = prev.then(() => {
      // 앞 저장이 충돌해 서버 상태로 다시 맞추는 중이면, 그 전에 줄 선 저장은 버린다(덮어쓰기 방지).
      if ((chainGen.current[token] ?? 0) !== enqueuedAt) return null;
      // 같은 실행 안에서만 본다: mutGen은 라벨 기준이라, 재실행 뒤 새 플랜의 복원이 올린 세대로
      // 이전 실행의 사용자 편집 저장을 버리면 안 된다(자체 리뷰 SHOULD).
      if (runAt === runGen.current && (mutGen.current[label] ?? 0) !== mutAt) return null;
      return savePlanEdits(token, { ...body, expected_revision: knownRevision.current[token] ?? 0 });
    }).then((r) => {
      if (r && runAt === runGen.current) knownRevision.current[token] = r.revision;
    }).catch((e) => {
      if (runAt !== runGen.current) return;          // 재실행·전환 뒤의 늦은 오류는 무시
      if (e instanceof EditsConflictError) {
        knownRevision.current[token] = e.revision;
        chainGen.current[token] = (chainGen.current[token] ?? 0) + 1;
        // 강제 복원도 같은 줄에 이어 붙여, 줄의 다른 저장과 순서가 섞이지 않게 한다(Opus 검증 S1).
        saveChain.current[token] = (saveChain.current[token] ?? Promise.resolve())
          .then(() => restoreEdits(base, runGen.current, true));
      } else if (e instanceof DatasetChangedError) void externalSwitch();
      else setError(`적용 교체를 서버에 저장하지 못했다(새로고침하면 사라질 수 있다): ${String(e)}`);
    });
  }

  /** 검토한 교체를 명단에 적용한다. 검토와 같은 기준(계산 당시 설정·가중치·데이터셋)으로
   *  서버가 명단 전체를 다시 평가하고, 화면은 그 결과를 스택에 쌓는다. */
  async function applyReviewedSwap() {
    if (!current || !whatif || !lastSwap || !selected) return;
    const label = selected;
    const swap = lastSwap;
    // 검토가 기준으로 삼은 명단과 지금 명단이 다르면(그사이 복원 등) 적용하지 않는다(Codex 3차).
    if (reviewBase.current !== current.entries) {
      setNotice(`Plan ${label}: 검토한 뒤 명단이 바뀌어 이 검토 결과는 적용할 수 없다. 다시 검토할 것.`);
      setWhatif(null);
      setLastSwap(null);
      return;
    }
    const gen = ++applyGen.current;
    const baseEntries = current.entries;      // 이 적용이 기준으로 삼은 명단
    setApplyBusy(true);
    try {
      const res = await applySwap(current.entries, swap, planBasis?.weights ?? weights,
                                  planBasis?.params ?? null, planBasis?.datasetVersion ?? null);
      if (gen !== applyGen.current) return;
      // 그사이 저장분 복원 등으로 이 플랜의 명단이 바뀌었으면, 옛 명단 기준 결과를 쌓지 않는다
      // (스택과 이력이 어긋난다, Opus 2라운드 S-a).
      const nowTop = editsRef.current[label]?.stack.at(-1)?.plan.entries
        ?? plans.find((p) => p.label === label)?.entries;
      if (nowTop !== baseEntries) {
        setNotice(`Plan ${label}: 적용하는 사이 명단이 바뀌어(저장분 복원 등) 이 적용은 취소했다. 다시 검토할 것.`);
        return;
      }
      swapGen.current += 1;
      setWhatifBusy(false);      // 진행 중이던 검토는 무효 -- 응답이 버려지므로 진행 표시도 내린다(리뷰 S1)
      const plan: PlanEvent = { ...current, entries: res.entries, objective: res.objective,
                                fulfillment: res.fulfillment,
                                optimization_ratio: res.optimization_ratio, unfilled: res.unfilled };
      // 경고 문장은 서버가 교체 전후를 비교해 만든 것을 쓴다(PDF 재계산과 같은 함수).
      const record: AppliedSwap = { ...swap, objective_delta: res.objective_delta,
                                    feasible: res.feasible, warnings: res.warnings };
      const e = editsRef.current[label] ?? { stack: [], history: [] };
      const nextEdit = {
        stack: [...e.stack, { plan, violations: res.evaluation.violations.map((v) => v.message) }],
        history: [...e.history, record] };
      bumpMut(label);
      commitEdits({ ...editsRef.current, [label]: nextEdit });
      persistEdits(label, nextEdit.history);
      setWhatif(null);            // 검토 결과는 적용으로 소비됐다 -- 새 명단에서 다시 검토한다
      setLastSwap(null);
      setHighlighted(null);
    } catch (e) {
      if (gen !== applyGen.current) return;          // 취소된 요청의 오류(늦은 409 포함)는 무시
      if (e instanceof DatasetChangedError) { await externalSwitch(); return; }
      setError(String(e));
    } finally {
      if (gen === applyGen.current) setApplyBusy(false);
    }
  }

  /** 한 사람의 달별 투입률 조정을 적용한다(사람별 달별 조정). 교체 적용과 같은 세대·저장 규칙을 따른다. */
  async function applyAllocChange(change: AllocChange): Promise<boolean> {
    if (!current || !selected) return false;
    const label = selected;
    const gen = ++applyGen.current;
    const baseEntries = current.entries;
    setApplyBusy(true);
    try {
      const res = await applyAlloc(current.entries, change, planBasis?.weights ?? weights,
                                   planBasis?.params ?? null, planBasis?.datasetVersion ?? null);
      if (gen !== applyGen.current) {
        setNotice(`Plan ${label}: 조정하는 사이 다른 작업이 시작돼 이 조정은 적용하지 않았다. 다시 할 것.`);
        return false;
      }
      const nowTop = editsRef.current[label]?.stack.at(-1)?.plan.entries
        ?? plans.find((p) => p.label === label)?.entries;
      if (nowTop !== baseEntries) {
        setNotice(`Plan ${label}: 조정하는 사이 명단이 바뀌어(저장분 복원 등) 이 조정은 취소했다. 다시 할 것.`);
        return false;
      }
      swapGen.current += 1;
      setWhatifBusy(false);
      const plan: PlanEvent = { ...current, entries: res.entries, objective: res.objective,
                                fulfillment: res.fulfillment,
                                optimization_ratio: res.optimization_ratio, unfilled: res.unfilled };
      const record: AppliedStep = { ...change, objective_delta: res.objective_delta,
                                    feasible: res.feasible, warnings: res.warnings };
      const e = editsRef.current[label] ?? { stack: [], history: [] };
      const nextEdit = {
        stack: [...e.stack, { plan, violations: res.evaluation.violations.map((v) => v.message) }],
        history: [...e.history, record] };
      bumpMut(label);
      commitEdits({ ...editsRef.current, [label]: nextEdit });
      persistEdits(label, nextEdit.history);
      setWhatif(null);            // 명단이 바뀌었으니 진행 중 검토 결과는 무효
      setLastSwap(null);
      setHighlighted(null);
      return true;
    } catch (e) {
      if (gen !== applyGen.current) return false;
      if (e instanceof DatasetChangedError) { await externalSwitch(); return false; }
      setError(String(e));
      return false;
    } finally {
      if (gen === applyGen.current) setApplyBusy(false);
    }
  }

  function undoApply() {
    if (!selected) return;
    swapGen.current += 1;
    cancelApply();
    setWhatif(null);
    setLastSwap(null);
    const e = editsRef.current[selected];
    if (!e) return;
    const next = { stack: e.stack.slice(0, -1), history: e.history.slice(0, -1) };
    const out = { ...editsRef.current };
    if (next.history.length === 0) delete out[selected]; else out[selected] = next;
    bumpMut(selected);
    commitEdits(out);
    persistEdits(selected, next.history);
  }

  function resetApply() {
    if (!selected) return;
    swapGen.current += 1;
    cancelApply();
    setWhatif(null);
    setLastSwap(null);
    if (!editsRef.current[selected]) return;
    const out = { ...editsRef.current };
    delete out[selected];
    bumpMut(selected);
    commitEdits(out);
    persistEdits(selected, []);
  }

  if (error && !meta) return <p className="p-8 text-red-600">불러오기 실패: {error}</p>;
  if (!meta) return <p className="p-8 text-slate-500">불러오는 중…</p>;

  // 화면·교체 검토·PDF가 쓰는 명단은 적용한 교체가 있으면 그 결과다(K10).
  const shown = (p: PlanEvent) => edits[p.label]?.stack.at(-1)?.plan ?? p;
  const original = plans.find((p) => p.label === selected) ?? null;
  const current = original ? shown(original) : null;
  const edit = selected ? edits[selected] : undefined;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white px-8 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">TeamWeaver</h1>
            <p className="text-sm text-slate-500">지식 그래프 · LLM 기반 지능형 인력 배치</p>
          </div>
          <div className="text-sm">
            {admin?.login_required && (admin.logged_in ? (
              <span className="text-slate-600">
                관리자{" · "}
                <button className="underline" onClick={async () => {
                  try { await adminLogout(); } catch (e) { setError(String(e)); }
                  setLoginReason(null); refreshAdmin();
                }}>로그아웃</button>
              </span>
            ) : (
              <button className="rounded-md border border-slate-300 px-3 py-1 text-slate-700"
                      onClick={() => setTab("settings")}>관리자 로그인</button>
            ))}
            {admin && !admin.protected && (
              <span role="status" className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">
                관리자 비밀번호 미설정 — 누구나 배치 설정·데이터를 바꿀 수 있다
              </span>
            )}
          </div>
        </div>
      </header>

      <nav className="flex gap-1 border-b border-slate-200 bg-white px-8">
        {([["req", "요건 설정"], ["whatif", "What-if 대시보드"], ["settings", "배치 설정"],
           ["data", "데이터"]] as const)
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
        {/* 편집 중에 세션이 끝났으면(loginReason) 탭 내용을 언마운트하지 않고 위에 로그인을 띄운다 --
            설정 폼에 입력하던 값이 사라지지 않게. 처음 여는 경우엔 로그인 화면이 탭을 대신한다. */}
        {(tab === "data" || tab === "settings") && needLogin && loginReason && (
          <div className="mb-6">
            <AdminLogin reason={loginReason} onLoggedIn={() => { setLoginReason(null); refreshAdmin(); }} />
          </div>
        )}
        {(tab === "data" || tab === "settings") && needLogin && !loginReason ? (
          <AdminLogin onLoggedIn={() => { setLoginReason(null); refreshAdmin(); }} />
        ) : tab === "data" ? (
          <div className="space-y-4">
            {adminRequired && <AdminTokenField value={adminToken} onChange={setAdminToken} />}
            <DatasetTab active={dataset} onSwitched={datasetSwitched}
                        adminToken={adminToken || null} onLoginRequired={adminExpired} />
          </div>
        ) : tab === "settings" ? (
          settings ? (
            <div className="space-y-4">
            {adminRequired && <AdminTokenField value={adminToken} onChange={setAdminToken} />}
            <SettingsTab data={settings}
                         onSave={async (s) => {
                           try {
                             setSettings(await saveSettings(s, settings.updated_at,
                                                            adminToken || null));
                             setSettingsError(null);
                           } catch (e) {
                             if (e instanceof AdminLoginRequiredError) adminExpired();
                             if (e instanceof SettingsConflictError) {
                               // 최신 값을 다시 읽어 폼을 갈아 끼우고, 이유를 알린다.
                               fetchSettings().then(setSettings).catch(() => {});
                             }
                             throw e;
                           }
                         }} />
            </div>
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
            <PlanCards plans={plans.map(shown)} selected={selected} onSelect={selectPlan} />
            {altShortfall && (
              <p className="mt-2 text-xs text-slate-500">
                {altShortfall.solverFailed
                  ? `대안 계산이 시간 안에 끝나지 않아 ${altShortfall.found}개만 냈다(요청 ${altShortfall.requested}개). 다시 실행하면 더 나올 수 있다.`
                  : <>
                      {altShortfall.found === 0
                        ? "조건을 만족하는 대안 없음"
                        : `조건을 만족하는 대안 ${altShortfall.found}개(요청 ${altShortfall.requested}개)`}
                      {" — 대안은 Plan A와 구성이 달라야 하고, 품질이 A의 95% 이상이며, 미충원이 A보다 많지 않아야 한다."}
                    </>}
              </p>
            )}
            {current && (
              <button
                disabled={pdfBusy}
                onClick={async () => {
                  setPdfBusy(true);
                  try {
                    await downloadReport(current, whatif, lastSwap, planBasis?.params ?? null,
                                         planBasis?.datasetVersion ?? null,
                                         edit && original
                                           ? { base: original.entries,
                                               swaps: edit.history.map(stepBody) }
                                           : null,
                                         { weights: planBasis?.weights ?? weights,
                                           planToken: original?.plan_token ?? null });
                  }
                  catch (e) {
                    if (e instanceof DatasetChangedError) await externalSwitch();
                    else setError(String(e));
                  }
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
            {notice && (
              <p role="status" className="rounded-md bg-indigo-50 px-4 py-2 text-sm text-indigo-900">{notice}</p>
            )}
            {current && edit && (
              <AppliedPanel label={current.label} history={edit.history}
                            violations={edit.stack.at(-1)?.violations ?? []}
                            people={meta.people} onUndo={undoApply} onReset={resetApply} />
            )}
            {current && (
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="space-y-6">
                  <NetworkGraph people={meta.people} coworks={meta.coworks}
                                entries={current.entries} highlight={highlighted} />
                  {/* key로 remount -- 플랜이 바뀌면 이전 플랜에서 고른
                      교체 대상/투입이 남아 있으면 안 된다. */}
                  <SwapControl key={`${current.label}-${edit?.history.length ?? 0}`}
                               people={meta.people} onSelectionChange={selectionChanged}
                               entries={current.entries}
                               onSwap={runSwap} busy={whatifBusy} />
                  <BriefingPanel result={whatif} loading={whatifBusy}
                                 nameOf={(id) => meta.people.find((p) => p.id === id)?.name ?? id} />
                  <ApplyControl key={`${current.label}-${edit?.history.length ?? 0}-${lastSwap
                                  ? `${lastSwap.out_person_id}-${lastSwap.in_person_id}` : ""}`}
                                result={whatifBusy ? null : whatif} swap={lastSwap}
                                nameOf={(id) => meta.people.find((p) => p.id === id)?.name ?? id}
                                busy={applyBusy} onApply={applyReviewedSwap} />
                </div>
                <AssignmentTable key={current.label} entries={current.entries} people={meta.people}
                                 projects={meta.projects} onAdjust={applyAllocChange} busy={applyBusy}
                                 minAlloc={planBasis?.params?.min_alloc ?? 0.2} />
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
