import { useCallback, useEffect, useRef, useState } from "react";
import {
  adminLogout, AdminLoginRequiredError, type AdminStatus, applyAlloc, applySwap, DatasetChangedError, EditsConflictError, downloadReport, loadPlanEdits, savePlanEdits, fetchActiveDataset, fetchAdminStatus, fetchMeta,
  fetchSettings, postBaseline, postWhatif, saveSettings, SettingsConflictError,
  stopRun, streamOptimize, exportRecord,
} from "./api/client";
import type {
  OpenRecordResult, RecordRequest,
  AllocChange, AssignEntry,  AppliedStep, AppliedSwap, DatasetInfo, Meta, PlacementSettings, PlanEvent, SettingsResponse, Swap,
  WhatifResponse,
} from "./api/types";
import { evidenceOf } from "./api/types";

/** 플랜 하나에 적용한 교체(K10). stack[k]는 k+1번째 교체를 적용한 뒤의 명단·지표와 그 명단
 *  전체의 위반 문장이다 -- 되돌리기는 맨 위를 빼면 된다. 서버는 저장하지 않는다. */
interface PlanEdit {
  stack: { plan: PlanEvent; violations: string[] }[];
  history: AppliedStep[];
}
import { RequirementsTab, StopButton } from "./components/RequirementsTab";
import { PlanCards } from "./components/PlanCards";
import { PrecomputedNotice } from "./components/PrecomputedNotice";
import { JustificationPanel } from "./components/JustificationPanel";
import { SkillMapTab } from "./components/SkillMapTab";
import { AssignmentTable } from "./components/AssignmentTable";
import { TeamRelations } from "./components/TeamRelations";
import type { SwapPick } from "./components/SwapControl";
import { SwapControl } from "./components/SwapControl";
import { BriefingPanel } from "./components/BriefingPanel";
import { SettingsTab } from "./components/SettingsTab";
import { DatasetTab } from "./components/DatasetTab";
import { AdminLogin } from "./components/AdminLogin";
import { ApplyControl } from "./components/ApplyControl";
import { AppliedPanel } from "./components/AppliedPanel";
import { describeChanges } from "./components/settingsFields";
import { BaselineCard } from "./components/BaselineCard";
import { EmergencyScreen } from "./scenarios/EmergencyScreen";
import { NewProjectsScreen } from "./scenarios/NewProjectsScreen";
import { effectiveSettings, stepBody } from "./api/types";
import { AnnualSteps, datasetLabel, PageHeader, Sidebar, SubTabs, WhatifEmpty, type SubTab, type Tab } from "./components/Shell";
import { ConfirmChanges } from "./components/ConfirmChanges";
import { CurrentWhatif } from "./whatif/CurrentWhatif";
import { currentEntries, fetchWorkspace } from "./scenarios/workspaceApi";
import { withPeople } from "./components/headcount";
import { horizonRange } from "./components/allocFormat";
import { ExportRecordDialog } from "./components/ExportRecordDialog";
import { Dialog } from "./ui";
import { RecordsPanel } from "./components/RecordsPanel";
import { ReviewDocsPanel } from "./review/ReviewDocsPanel";
import { ConcurrencyPanel } from "./scenarios/ConcurrencyPanel";
import { NightlyBanner } from "./nightly/NightlyBanner";
import { NightlyPanel } from "./nightly/NightlyPanel";
import { DataSourcePanel } from "./datasource/DataSourcePanel";




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

export default function App({ onLoggedOut, initialTab = "pipeline" }: {
  onLoggedOut?: () => void;
  /** 첫 화면. 기본은 신규 사업 편성(가장 잦은 상황, 2026-10-18) -- 시험은 계산 흐름을 보려고 연간 계획에서 시작한다 */
  initialTab?: Tab;
} = {}) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  // 확정 화면에서 "인사팀 검토 자료 만들기"로 넘어온 자료(claude-a 2026-10-19) -- 사업이 없으면(자동 편성) 목록만 연다
  const [reviewOpen, setReviewOpen] = useState<{ version: string; projectId: string } | null>(null);
  const openReviewDoc = useCallback((version: string, projectId: string | null) => {
    setReviewOpen(projectId ? { version, projectId } : null);
    setTab("reviews");
  }, []);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [plans, setPlans] = useState<PlanEvent[]>([]);
  /** 상황 화면이 데이터를 바꿔 편성안을 닫았을 때 그 화면 이름(What-if 빈 화면이 이유를 보인다, 2026-10-21 사용자 질문) */
  const [closedBy, setClosedBy] = useState<string | null>(null);
  /** 연간 계획 "이 안으로 확정"(2026-10-21 화면 검토 P1-1): 확정 창을 띄운 안과, 확정 뒤 안내 문장 */
  const [planConfirmOpen, setPlanConfirmOpen] = useState(false);
  const [planConfirmed, setPlanConfirmed] = useState<string | null>(null);
  /** 지금 배치(작업 데이터) -- 안 A~D가 지금 대비 몇 명을 옮기는지(P1-2). 백지면 빈 목록, 읽지 못하면 null */
  const [currentBase, setCurrentBase] = useState<AssignEntry[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // 새로 푸는 계산의 실행 번호(SSE start) -- "계산 멈추기"가 쓴다. 캐시·미리 계산이면 없다(멈출 것이 없다).
  const [runId, setRunId] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [stoppedNote, setStoppedNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [whatif, setWhatif] = useState<WhatifResponse | null>(null);
  // whatif가 "어떤 교체"에 대한 결과인지. PDF가 스스로는 알 수 없으므로
  // downloadReport에 함께 실어 보낸다.
  const [lastSwap, setLastSwap] = useState<Swap | null>(null);
  const [whatifBusy, setWhatifBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  // PDF에 사업별 인사팀 소명 글을 넣을지(사업마다 AI 글이라 1~2분 더 걸린다)
  const [pdfJustify, setPdfJustify] = useState(false);
  // PDF 내려받기를 누르면 소명 포함 여부를 묻는다(사용자 2026-10-20 -- 하위 탭 옆 체크박스 대신)
  const [pdfAsk, setPdfAsk] = useState(false);
  // 그래프에서 강조할 인물. 교체 "투입" 대상은 아직 배치 전이라 그래프에
  // 노드가 없다 -- 강조해도 보이지 않는다. 그래서 빠지는 쪽(out)을 강조해
  // "이 사람을 빼면 협업망 어디에 구멍이 나는지"를 보여준다.
  const [swapPick, setSwapPick] = useState<SwapPick | null>(null);
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
               datasetVersion: string; nAlternatives?: number } | null>(null);
  // What-if 대시보드 아래쪽 하위 탭(2026-10-18 개편)과 "편성 기록 내보내기" 창
  const [subTab, setSubTab] = useState<SubTab>("roster");
  const [exportOpen, setExportOpen] = useState(false);
  // 편성 기록을 열었고 그 기록에 운영 중 K 비교가 있으면, 운영 중 편성 탭이 기록 당시 설정으로 묻는다(저장 결과가 바로 나오게).
  const [recordOperating, setRecordOperating] = useState<PlacementSettings | null>(null);
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
  // 진행 중인 최적화 스트림의 연결. 다시 실행·데이터셋 전환·화면을 떠나면 끊는다 -- 서버가 끊김을 보고 그 계산을
  // 바로 중단해 슬롯을 돌려준다(2026-10-12; 예전엔 다음 이벤트가 올 때까지 연결을 쥐고 있었다).
  const runAbort = useRef<AbortController | null>(null);
  useEffect(() => () => runAbort.current?.abort(), []);
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
    useState<{ found: number; requested: number; solverFailed: boolean; precomputed: boolean; recorded?: boolean } | null>(null);
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

  // 지금 배치(작업 데이터) -- 안 A~D의 "지금 대비 옮김"(2026-10-21 P1-2). 데이터 판이 바뀔 때마다 다시 읽는다(편집할 수 없는 데이터면 비교하지 않음)
  useEffect(() => {
    if (!dataset?.version) { setCurrentBase(null); return; }
    let live = true;
    fetchWorkspace().then((w) => { if (live) setCurrentBase(w.editable ? currentEntries(w) : null); })
      .catch(() => { if (live) setCurrentBase(null); });
    return () => { live = false; };
  }, [dataset?.version]);

  // 관리자 탭을 열 때마다 로그인 상태를 다시 본다(세션 만료·다른 탭의 로그아웃 반영).
  useEffect(() => {
    if (tab === "settings" || tab === "data" || tab === "records" || tab === "reviews") refreshAdmin();
  }, [tab]);

  /** 활성 데이터셋이 바뀌었다: 이전 데이터의 플랜·교체 검토·가중치(기술 이름이 다를 수
   *  있다)를 모두 비우고 meta를 새로 읽는다. 옛 플랜으로 교체를 검토하면 없는 ID라 실패한다. */
  async function datasetSwitched(info: DatasetInfo) {
    runGen.current += 1;
    runAbort.current?.abort();
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
    setWhatifBusy(false);
    setPlanBasis(null);
    setWeights({});
    setRunning(false);
    setRecordOperating(null);
    setSubTab("roster");
    setClosedBy(null);
    setPlanConfirmed(null);
    setPlanConfirmOpen(false);
    // 인원이 바뀌면 자동 계산 시간도 바뀐다 -- 설정 탭이 이전 데이터 기준 값을 보이지 않게 다시 읽는다(리뷰 S2).
    fetchSettings().then(setSettings).catch(() => {});
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

  /** 상황 화면(신규 사업 편성·긴급 투입)이 직접 데이터를 바꾼 뒤 -- 새 판을 읽되 "다른 사용자가 바꿨다" 경고는 띄우지 않는다.
   *  편성안이 있었으면 닫힌 이유를 What-if 빈 화면에 남긴다(편성안은 계산한 판에 묶여 새 판에서는 교체 검토·PDF가 맞지 않는다). */
  async function ownSwitch(by?: string) {
    const hadPlans = plans.length > 0;
    try {
      await datasetSwitched(await fetchActiveDataset());
      if (hadPlans && by) setClosedBy(by);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  /** fresh: 캐시·저장 결과를 쓰지 않고 다시 푼다("다시 계산"). 버튼 onClick이 이벤트를 넘겨도 켜지지 않게 true만 본다.
   *  record: 편성 기록을 열었을 때 -- 기록의 요청(가중치·설정·대안 수) 그대로 보내면 서버가 기록의 편성안을 캐시에서 바로 낸다. */
  async function run(fresh?: unknown, record?: { request: RecordRequest; datasetVersion: string }) {
    const recompute = fresh === true;
    const gen = ++runGen.current;
    runAbort.current?.abort();                  // 이전 실행이 아직 돌고 있으면 끊는다(서버가 중단)
    const abort = new AbortController();
    runAbort.current = abort;
    swapGen.current += 1;          // 재실행 -- 진행 중이던 what-if 응답도 무효화
    cancelApply();
    setRunning(true);
    setRunId(null);
    setStopping(false);
    setStoppedNote(null);
    setError(null);
    setClosedBy(null);
    setPlanConfirmed(null);
    setPlans([]);
    setAltShortfall(null);
    commitEdits({});
    setNotice(null);
    setSelected(null);
    setWhatif(null);
    setLastSwap(null);
    setWhatifBusy(false);
    // 실행할 때마다 서버 설정을 다시 읽는다: 페이지를 연 뒤 다른 관리자가 바꾼
    // 설정도 반영하고, 첫 로딩이 끝나기 전에 눌러도 모델 기본값으로 새지 않는다.
    // 읽기에 실패하면 마지막으로 알던 설정을 쓰고(없으면 모델 기본값) 경고한다.
    let params: PlacementSettings | null = settings ? effectiveSettings(settings) : null;
    try {
      const fresh = await fetchSettings();
      setSettings(fresh);
      setSettingsError(null);
      params = effectiveSettings(fresh);      // 자동 계산 시간이면 서버가 정한 실제 값(claude-a 요청)
    } catch (e) {
      setSettingsError(String(e));
    }
    if (record) params = record.request.milp_params as unknown as PlacementSettings;   // 기록 당시 설정
    if (gen !== runGen.current || (!meta && !record)) return;   // 설정을 읽는 사이 데이터셋이 바뀌었다
    const datasetVersion = record ? record.datasetVersion : meta!.dataset_version;
    const useWeights = record ? record.request.weights : weights;
    setPlanBasis({ params, weights: useWeights, datasetVersion,
                   nAlternatives: record ? record.request.n_alternatives : 3 });
    try {
      // Plan A가 먼저 도착하면 즉시 렌더된다 -- 대안 B/C/D를 기다리지 않는다.
      // 이것이 Plan 4의 SSE 점진 반환(A안)이 사용자 눈에 보이는 지점이다.
      for await (const ev of streamOptimize(
          { weights: useWeights, dataset_version: datasetVersion, ...(recompute ? { fresh: true } : {}),
            ...(record ? { n_alternatives: record.request.n_alternatives } : {}),
            ...(params ? { milp_params: { ...params } } : {}) }, abort.signal)) {
        if (gen !== runGen.current) break;   // 그사이 데이터셋이 바뀌었다 -- 남은 결과 폐기
        if (ev.event === "start") {
          setRunId(ev.data.run_id);
        } else if (ev.event === "plan") {
          setPlans((prev) => [...prev, ev.data]);
          void restoreEdits(ev.data, gen);
          setSelected((cur) => cur ?? ev.data.label);
          setTab("plans");
        } else if (ev.event === "done") {
          const requested = ev.data.requested_alternatives;
          const found = Math.max(0, ev.data.count - 1);
          if (ev.data.stop_reason === "user_stopped") {
            // 사용자가 멈췄다 -- "대안 없음"이 아니다
            setStoppedNote(ev.data.count === 0
              ? "계산을 멈췄다. 멈추기 전까지 쓸 만한 배치를 찾지 못해 결과가 없다."
              : `계산을 멈췄다. 받은 안 ${ev.data.count}개 — 멈출 때 계산 중이던 안은 그때까지 찾은 가장 좋은 배치다(최선 증명 전).`);
          } else if (requested !== undefined && found < requested)
            setAltShortfall({ found, requested, solverFailed: ev.data.stop_reason === "time_limit",
                              precomputed: ev.data.precomputed_at != null,
                              recorded: !!(ev.data as { precomputed_label?: string | null }).precomputed_label });
        } else if (ev.event === "error") {
          setError(ev.data.message);
        }
      }
    } catch (e) {
      if (e instanceof DatasetChangedError) await externalSwitch();
      else if (gen === runGen.current) setError(String(e));
    } finally {
      if (gen === runGen.current) {
        setRunning(false);
        setRunId(null);
        setStopping(false);
      }
    }
  }

  /** 편성 기록을 열었다: 데이터가 바뀌었으니 화면을 새 데이터로 다시 불러오고, 기록의 요청 그대로 계산을 요청한다 --
   *  서버가 기록의 편성안을 캐시에 넣어 두었으므로 바로 나온다(서명도 그 경로가 붙인다). */
  async function recordOpened(res: OpenRecordResult) {
    await datasetSwitched(res.dataset);
    setWeights(res.request.weights);
    setRecordOperating((res.operating_params as unknown as PlacementSettings | null) ?? null);
    setTab("plans");
    await run(false, { request: res.request, datasetVersion: res.dataset.version });
  }

  async function downloadPdf(withJustify: boolean) {
    if (!current) return;
    setPdfAsk(false);
    setPdfJustify(withJustify);
    setPdfBusy(true);
    try {
      await downloadReport(current, whatif, lastSwap, planBasis?.params ?? null,
                           planBasis?.datasetVersion ?? null,
                           edit && original
                             ? { base: original.entries,
                                 swaps: edit.history.map(stepBody) }
                             : null,
                           { weights: planBasis?.weights ?? weights,
                             planToken: original?.plan_token ?? null,
                             // 원안의 서명된 계산 신뢰도 -- PDF 배지(교체를 적용했으면 "원안 기준"으로 찍힌다)
                             evidence: original ? evidenceOf(original) : null },
                           withJustify && kgData);
    }
    catch (e) {
      if (e instanceof DatasetChangedError) await externalSwitch();
      else setError(String(e));
    }
    finally { setPdfBusy(false); }
  }

  /** "계산 멈추기": 지금 푸는 안은 그때까지의 해로 끝내고 다음 대안은 시작하지 않는다. 결과는 같은 스트림으로 온다. */
  async function stopRunning() {
    if (!runId || stopping) return;
    setStopping(true);
    try {
      await stopRun(runId);          // 이미 끝났으면(false) 스트림이 곧 done으로 끝난다
    } catch (e) {
      setStopping(false);
      setError(String(e));
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
    setWhatifBusy(false);
  }

  selectedRef.current = selected;

  async function runSwap(swap: Swap) {
    if (!current) return;
    const gen = ++swapGen.current;
    const base = current.entries;
    setWhatifBusy(true);
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
  // 지식 그래프(인사팀 소명)는 CSV 묶음 데이터(업로드·시연 데이터)에서만 만들 수 있다
  const kgData = dataset?.source === "upload" || dataset?.source === "demo-bundle" || dataset?.source === "record";
  const original = plans.find((p) => p.label === selected) ?? null;
  const current = original ? shown(original) : null;
  const edit = selected ? edits[selected] : undefined;

  const horizon = meta ? horizonRange(meta.horizon_start, meta.horizon_months ?? 6) : "";
  const pageContext = tab === "plans" && dataset
    ? `${withPeople(datasetLabel(dataset), dataset.people)} · 사업 ${dataset.projects}건${horizon ? ` · 계획 기간 ${horizon}` : ""}`
    : undefined;
  const showResults = plans.length > 0 || running;

  return (
    <div className="flex min-h-screen flex-col bg-canvas md:flex-row">
      <Sidebar tab={tab} onTab={setTab} dataset={dataset} user={admin?.user ?? null}
               onLogout={admin?.login_required && admin.logged_in ? () => {
                 adminLogout().catch(() => {}).finally(() => (onLoggedOut ? onLoggedOut() : window.location.reload()));
               } : null} />
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader tab={tab} context={pageContext} actions={tab === "plans" && current ? (
          <>
            <button className="btn" onClick={() => setExportOpen(true)}>편성 기록 내보내기</button>
            {dataset && (
              <button className="btn" onClick={() => setPlanConfirmOpen(true)} disabled={running}
                      title="이 안(화면에서 바꾼 것 포함)을 지금 배치(작업 데이터)로 확정합니다 -- 그 뒤 신규 사업 편성·긴급 투입·What-if로 이어집니다">
                안 {current.label}로 확정</button>
            )}
            <button className="btn-primary" disabled={pdfBusy} onClick={() => (kgData ? setPdfAsk(true) : void downloadPdf(false))}>
              {pdfBusy ? (pdfJustify && kgData ? "PDF 생성 중…(사업별 소명 포함, 1~2분)" : "PDF 생성 중…") : "PDF 내려받기"}
            </button>
          </>
        ) : undefined} />
        {admin?.default_credentials && admin.logged_in && (tab === "settings" || tab === "data" || tab === "records" || tab === "reviews") && (
          <p role="status" className="mx-4 mt-4 rounded-md bg-amber-50 px-4 py-2 text-[13px] text-amber-700 md:mx-8">
            기본 관리자 계정(admin)과 기본 비밀번호로 들어와 있습니다. 다른 사람이 접속하는 주소로 쓰려면 서버의
            TEAMWEAVER_ADMIN_PASSWORD_HASH로 비밀번호를 바꾸세요.
          </p>
        )}
        {admin && !admin.protected && (
          <p role="status" className="mx-4 mt-4 rounded-md bg-amber-50 px-4 py-2 text-[13px] text-amber-700 md:mx-8">
            관리자 비밀번호 미설정(로그인이 꺼진 서버) — 누구나 배치 설정·데이터를 바꿀 수 있습니다.
          </p>
        )}

      <main className="flex-1 px-4 py-6 md:px-8">
        {/* 운영 중 편성 탭은 다른 탭에 가도 사라지지 않게 늘 띄워 두고 숨긴다 -- 35초짜리 비교 결과를 잃지 않게(리뷰) */}
        {/* 전역 error로 숨기지 않는다 -- 운영 탭은 자체 오류 표시가 있고, 다른 탭의 오류 때문에 탭이 빈 화면이 되면 안 된다(리뷰 2라운드 MUST) */}
        {/* 3가지 핵심 상황(claude-b 2026-10-19): 신규 사업 편성·긴급 투입. 다른 메뉴에 가도 상태가 남게 숨김 방식으로 띄운다.
            확정·등록·고정은 작업 데이터의 새 판을 만들므로 App이 데이터 정보를 다시 읽는다(ownSwitch -- "다른 사용자" 경고 없이). */}
        {/* 아침 알림(claude-b 요청 (1)): 첫 화면 위 띠 -- 데이터가 바뀌면 다시 읽는다 */}
        {tab === "pipeline" && <NightlyBanner refreshKey={dataset?.version ?? ""} />}
        <div className={tab === "pipeline" ? "" : "hidden"}>
          <NewProjectsScreen milpParams={recordOperating ?? (settings ? effectiveSettings(settings) : null)}
                             datasetVersion={dataset?.version ?? null} onDatasetChanged={() => { void ownSwitch("신규 사업 편성"); }}
                             onReviewDoc={openReviewDoc} people={meta?.people} projects={meta?.projects} />
        </div>
        <div className={tab === "emergency" ? "" : "hidden"}>
          <EmergencyScreen datasetVersion={dataset?.version ?? null} onDatasetChanged={() => { void ownSwitch("긴급 투입"); }}
                           onReviewDoc={openReviewDoc} people={meta?.people} projects={meta?.projects} />
        </div>
        {/* What-if(지금 배치, 2026-10-21): 담은 변경이 메뉴를 오가도 남게 숨김 방식 */}
        <div className={tab === "whatif" ? "" : "hidden"}>
          <CurrentWhatif active={tab === "whatif"} datasetVersion={dataset?.version ?? null} meta={meta}
                         settings={settings ? effectiveSettings(settings) : null}
                         onDatasetChanged={() => { void ownSwitch("What-if"); }} onReviewDoc={openReviewDoc} />
        </div>
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
        {(tab === "data" || tab === "settings" || tab === "records" || tab === "reviews") && needLogin && loginReason && (
          <div className="mb-6">
            <AdminLogin reason={loginReason} onLoggedIn={() => { setLoginReason(null); refreshAdmin(); }} />
          </div>
        )}
        {(tab === "data" || tab === "settings" || tab === "records" || tab === "reviews") && needLogin && !loginReason ? (
          <AdminLogin onLoggedIn={() => { setLoginReason(null); refreshAdmin(); }} />
        ) : tab === "reviews" ? (
          <div className="space-y-4">
            {adminRequired && <AdminTokenField value={adminToken} onChange={setAdminToken} />}
            <ReviewDocsPanel adminToken={adminToken || null} onLoginRequired={adminExpired} open={reviewOpen}
                             onOpened={() => setReviewOpen(null)} />
          </div>
        ) : tab === "records" ? (
          <div className="max-w-[1080px] space-y-4">
            {adminRequired && <AdminTokenField value={adminToken} onChange={setAdminToken} />}
            <RecordsPanel adminToken={adminToken || null} onLoginRequired={adminExpired} onOpened={recordOpened}
                          onExportCurrent={current ? () => setExportOpen(true) : null}
                          activeRecordId={dataset?.record_id ?? null} />
          </div>
        ) : tab === "data" ? (
          <div className="space-y-4">
            {adminRequired && <AdminTokenField value={adminToken} onChange={setAdminToken} />}
            <DatasetTab active={dataset} onSwitched={datasetSwitched} showRecords={false}
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
            <DataSourcePanel adminToken={adminToken || null} datasetVersion={dataset?.version ?? null}
                             onSwitched={(info) => void datasetSwitched(info)} onExternalChange={() => void externalSwitch()}
                             onLoginRequired={adminExpired} />
            <NightlyPanel adminToken={adminToken || null} onLoginRequired={adminExpired} />
            </div>
          ) : (
            <p className="text-sm text-slate-500">
              {settingsError ? "배치 설정을 불러오지 못했다." : "배치 설정을 불러오는 중…"}
            </p>
          )
        ) : tab === "pipeline" || tab === "emergency" || tab === "whatif" ? null : tab === "skills" ? (
          <SkillMapTab datasetVersion={meta.dataset_version} onDatasetChanged={() => void externalSwitch()} />
        ) : tab === "annual" ? (
          <>
          <AnnualSteps tab={tab} onTab={setTab} hasPlans={plans.length > 0} />
          <RequirementsTab meta={meta}
                           onRun={run} running={running} operating={dataset?.scenario === "operating"}
                           onStop={runId ? () => void stopRunning() : null} stopping={stopping}
                           onOpenOperating={() => setTab("pipeline")} />
          </>
        ) : !showResults ? (
          <>
          <AnnualSteps tab={tab} onTab={setTab} hasPlans={false} />
          <WhatifEmpty dataset={dataset} onCompute={() => setTab("annual")} onRecords={() => setTab("records")} closedBy={closedBy}
                       confirmed={planConfirmed} />
          </>
        ) : (
          <div className="space-y-5">
            <AnnualSteps tab={tab} onTab={setTab} hasPlans />
            {plans.length > 0 && planBasis && settings && (
              planBasis.params === null
              || describeChanges(planBasis.params, effectiveSettings(settings)).length > 0) && (
              <p role="status" className="rounded-md border border-amber-200 bg-amber-50 px-4 py-2
                                         text-sm text-amber-800">
                {planBasis.params
                  ? `이 결과는 지금과 다른 설정으로 계산됐다(바뀐 설정: ${
                      describeChanges(planBasis.params, effectiveSettings(settings)).join(", ")}). `
                  : "이 결과는 배치 설정 없이 서버 모델 기본값으로 계산됐다. "}
                현재 설정을 반영하려면 요건 설정 탭에서 다시 실행할 것.
                교체 검토와 PDF는 계산 당시 기준을 그대로 쓴다.
              </p>
            )}
            {!running && plans.some((p) => p.cached) && (
              <PrecomputedNotice at={plans.find((p) => p.precomputed_at != null)?.precomputed_at ?? null}
                                 label={plans.find((p) => p.precomputed_label)?.precomputed_label ?? null}
                                 source={plans.find((p) => p.precomputed_at != null)?.precomputed_source ?? null}
                                 onRecompute={() => void run(true)} />
            )}
            {stoppedNote && (
              <p role="status" className="rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
                {stoppedNote}
              </p>
            )}
            {running && (
              <p className="text-sm text-slate-500">
                {runId && <StopButton onStop={() => void stopRunning()} stopping={stopping} className="mr-2 py-1" />}
                {plans.length === 0
                  ? "Plan A 계산 중… (최초 실행은 약 8초)"
                  : `대안 계산 중… (${plans.length}개 도착)`}
                {planBasis?.params && (
                  <span className="ml-1">
                    · 최악의 경우 약 {Math.ceil((4 * planBasis.params.time_limit) / 60)}분(Plan A와 대안 3개,
                    각 {planBasis.params.time_limit}초 한도)
                  </span>
                )}
              </p>
            )}
            <PlanCards plans={plans.map(shown)} selected={selected} onSelect={selectPlan}
                       editCounts={Object.fromEntries(Object.entries(edits).map(([l, e]) => [l, e.stack.length]))}
                       baseline={currentBase ?? undefined} />
            {altShortfall && (
              <p className="mt-2 text-xs text-slate-500">
                {altShortfall.solverFailed
                  ? altShortfall.precomputed
                    // 저장 결과는 다시 실행해도 같은 결과가 나온다 -- "다시 계산"만 새로 푼다(리뷰 S1)
                    ? `${altShortfall.recorded ? "기록 당시" : "미리 계산 때"} 대안 계산이 시간 안에 끝나지 않아 ${altShortfall.found}개만 냈다(요청 ${altShortfall.requested}개). 위의 '다시 계산'으로 새로 풀면 더 나올 수 있다.`
                    : `대안 계산이 시간 안에 끝나지 않아 ${altShortfall.found}개만 냈다(요청 ${altShortfall.requested}개). 다시 실행하면 더 나올 수 있다.`
                  : <>
                      {altShortfall.found === 0
                        ? "조건을 만족하는 대안 없음"
                        : `조건을 만족하는 대안 ${altShortfall.found}개(요청 ${altShortfall.requested}개)`}
                      {" — 대안은 Plan A와 구성이 달라야 하고, 품질이 A의 95% 이상이며, 미충원이 A보다 많지 않아야 한다."}
                    </>}
              </p>
            )}
            {notice && (
              <p role="status" className="rounded-md bg-indigo-50 px-4 py-2 text-sm text-indigo-900">{notice}</p>
            )}
            {current && edit && (
              <AppliedPanel label={current.label} history={edit.history}
                            violations={edit.stack.at(-1)?.violations ?? []}
                            people={meta.people} horizonStart={meta.horizon_start} onUndo={undoApply} onReset={resetApply} />
            )}
            {current && (
              <>
                <SubTabs value={subTab} onChange={setSubTab} showJustify={kgData && !!planBasis} />
                <div className={subTab === "roster" ? "" : "hidden"}>
                  {/* 전일 투입(2026-10-19 서비스 기본)은 투입률이 100%뿐이라 달별 조정이 없다 */}
                  <AssignmentTable key={current.label} entries={current.entries} people={meta.people}
                                   projects={meta.projects}
                                   onAdjust={planBasis?.params?.allocation_mode === "full" ? undefined : applyAllocChange}
                                   busy={applyBusy}
                                   minAlloc={planBasis?.params?.min_alloc ?? 0.2} horizonStart={meta.horizon_start}
                                   unfilled={current.unfilled} baseline={currentBase ?? undefined} />
                  {planBasis?.params?.allocation_mode === "full" && current.unfilled.length > 0 && (
                    <div className="mt-4">
                      <ConcurrencyPanel datasetVersion={planBasis.datasetVersion} entries={current.entries}
                                        weights={planBasis.weights}
                                        projectName={(id) => meta.projects.find((j) => j.id === id)?.name ?? id}
                                        onGranted={(msg) => { void ownSwitch().then(() => setNotice(msg)); }}
                                        onStale={() => void externalSwitch()} />
                    </div>
                  )}
                </div>
                <div className={subTab === "swap" ? "grid gap-5 lg:grid-cols-2" : "hidden"}>
                  <div className="space-y-5">
                    <SwapControl key={`${current.label}-${edit?.history.length ?? 0}`}
                                 people={meta.people} onSelectionChange={selectionChanged}
                                 entries={current.entries}
                                 onSwap={runSwap} busy={whatifBusy}
                                 projects={meta.projects} horizonStart={meta.horizon_start} onPick={setSwapPick} />
                    <BriefingPanel result={whatif} loading={whatifBusy}
                                   nameOf={(id) => meta.people.find((p) => p.id === id)?.name ?? id} />
                    <ApplyControl key={`${current.label}-${edit?.history.length ?? 0}-${lastSwap
                                    ? `${lastSwap.out_person_id}-${lastSwap.in_person_id}` : ""}`}
                                  result={whatifBusy ? null : whatif} swap={lastSwap}
                                  nameOf={(id) => meta.people.find((p) => p.id === id)?.name ?? id}
                                  busy={applyBusy} onApply={applyReviewedSwap} />
                  </div>
                  <TeamRelations people={meta.people} projects={meta.projects} coworks={meta.coworks}
                                 entries={current.entries} pick={swapPick} />
                </div>
                {kgData && planBasis && (
                  <div className={subTab === "justify" ? "" : "hidden"}>
                    <JustificationPanel
                      meta={meta} datasetVersion={planBasis.datasetVersion} entries={current.entries}
                      applied={edit && original ? { base: original.entries, steps: edit.history.map(stepBody) } : null}
                      weights={planBasis.weights} params={planBasis.params} planLabel={original?.label ?? current.label}
                      planToken={original?.plan_token ?? null} onDatasetChanged={() => void externalSwitch()}
                      visible={subTab === "justify"} />
                  </div>
                )}
                <div className={subTab === "baseline" ? "" : "hidden"}>
                  <BaselineCard key={`${current.label}-${edit?.history.length ?? 0}`} entries={current.entries}
                                active={subTab === "baseline"}
                                onDatasetChanged={() => { void externalSwitch(); }}
                                load={(e) => postBaseline({ dataset_version: meta.dataset_version,
                                                            milp_params: planBasis?.params ?? null }, e,
                                                          planBasis?.weights ?? weights)} />
                </div>
              </>
            )}
          </div>
        )}
      </main>
      </div>
      {pdfAsk && current && (
        <Dialog title="PDF 내려받기" labelId="pdf-ask-title"
                actions={<>
                  <button className="btn-ghost" onClick={() => setPdfAsk(false)}>취소</button>
                  <button className="btn" onClick={() => void downloadPdf(false)}>소명 빼고 받기</button>
                  <button className="btn-primary" onClick={() => void downloadPdf(true)}>소명 포함해 받기</button>
                </>}>
          배치 명단·점수·교체 이력이 담긴 PDF를 만듭니다. 사업마다 "왜 이 팀인가"를 설명하는 <b>인사팀 소명</b>을 함께 넣을까요?
          넣으면 AI가 사업마다 글을 써서 1~2분 더 걸립니다(이미 써 둔 글은 다시 쓰지 않습니다).
        </Dialog>
      )}
      {planConfirmOpen && current && dataset && (
        <ConfirmChanges datasetVersion={dataset.version} entries={current.entries} source="plan" projects={meta?.projects}
                        title={`안 ${current.label}를 지금 배치로 확정`}
                        lead={currentBase && currentBase.length > 0
                          ? "이 안은 모두를 처음부터 다시 짠 결과라 지금 배치에서 많은 사람이 옮겨질 수 있습니다. 아래 수를 확인하고 확정하세요. 몇 명만 바꾸려면 What-if나 신규 사업 편성의 자동 편성(K)이 맞습니다."
                          : "이 안이 지금 배치가 됩니다. 그 뒤 신규 사업 편성·긴급 투입·What-if로 이어서 씁니다."}
                        note={`연간 계획 확정: 안 ${current.label}${edit?.history.length ? ` + 변경 ${edit.history.length}건` : ""}`}
                        onClose={() => setPlanConfirmOpen(false)}
                        onStale={() => { setPlanConfirmOpen(false); void externalSwitch(); }}
                        onDone={(msg) => {
                          const label = current.label;
                          setPlanConfirmOpen(false);
                          void ownSwitch().then(() => setPlanConfirmed(
                            `안 ${label}를 지금 배치로 ${msg} 이제 신규 사업 편성·긴급 투입·What-if에서 이어서 씁니다.`));
                        }} />
      )}
      {exportOpen && current && planBasis && (
        <ExportRecordDialog
          editedCount={Object.values(edits).reduce((n, e) => n + e.stack.length, 0)}
          defaultTitle={`${datasetLabel(dataset)} 편성 · ${new Date().toISOString().slice(0, 10)}`}
          onClose={() => setExportOpen(false)}
          onExport={async (title) => {
            try {
              await exportRecord({ title, weights: planBasis.weights,
                                   milp_params: planBasis.params as unknown as Record<string, unknown> | null,
                                   n_alternatives: planBasis.nAlternatives ?? 3,
                                   dataset_version: planBasis.datasetVersion, plans });
            } catch (e) {
              if (e instanceof DatasetChangedError) { setExportOpen(false); await externalSwitch(); return; }
              throw e;
            }
          }} />
      )}
    </div>
  );
}
