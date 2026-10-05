import type {
  Meta, ApplySwapResponse, AssignEntry, DatasetInfo, PlacementSettings, PlanEvent, ReportRequest,
  SavedPlanEdits, SettingsResponse, Step, Swap, UploadResult, WhatifResponse, AllocChange,
} from "./types";
import { parseFrames, type SseEvent } from "./sse";
import { swapWarnings } from "./whatifWarnings";

/** 개발 중에는 Vite dev 서버(:5173)에서 API(:8000)를 부르므로 절대 URL이 필요하다.
 *  프로덕션 빌드는 FastAPI가 같은 오리진에서 서빙하므로(Task 8) 빈 문자열이면 된다. */
export const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

/** 409 dataset_changed: 화면이 본 뒤 다른 사용자가 서버의 데이터셋을 바꿨다(K9).
 *  App이 받아 새 데이터로 화면을 다시 불러온다. */
export class DatasetChangedError extends Error {}

async function throwIfDatasetChanged(res: Response): Promise<void> {
  if (res.status !== 409) return;
  const body = await res.clone().json().catch(() => ({}));
  if (body?.detail?.code === "dataset_changed") {
    throw new DatasetChangedError(body.detail.message ?? "서버의 데이터셋이 바뀌었다");
  }
}

function adminHeaders(token: string | null): Record<string, string> {
  return token ? { "X-Admin-Token": token } : {};
}

/** 관리자 로그인 상태(K14). login_required면 비밀번호 로그인이 필요한 서버, protected=false면
 *  관리자 동작이 아무 보호 없이 열려 있다(비밀번호·토큰 미설정). */
export interface AdminStatus {
  login_required: boolean;
  protected: boolean;
  logged_in: boolean;
  expires_at: number | null;
  token_required: boolean;
}

/** 관리자 동작이 401을 받았다 -- 로그인하지 않았거나 세션이 끝났다. */
export class AdminLoginRequiredError extends Error {}

// 관리자 세션은 HttpOnly 쿠키다. dev(:5173 → :8000)는 교차 origin이라 include로 보내야 한다.
const WITH_COOKIE: RequestCredentials = "include";

export async function fetchAdminStatus(): Promise<AdminStatus> {
  const res = await fetch(`${API_BASE}/api/admin`, { credentials: WITH_COOKIE });
  if (!res.ok) throw new Error(`GET /api/admin 실패: ${res.status}`);
  return (await res.json()) as AdminStatus;
}

/** 비밀번호가 맞으면 서버가 세션 쿠키를 심는다. 틀리면 이유(잠금 포함)를 담아 throw. */
export async function adminLogin(password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/login`, {
    method: "POST", credentials: WITH_COOKIE,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(String(detail.detail ?? `로그인 실패(${res.status})`));
  }
}

export async function adminLogout(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/admin/logout`, { method: "POST", credentials: WITH_COOKIE });
  if (!res.ok) throw new Error(`로그아웃 실패(${res.status})`);
}

function throwIfLoginRequired(res: Response): void {
  if (res.status === 401) throw new AdminLoginRequiredError("관리자 로그인이 필요하다.");
}

export async function fetchMeta(): Promise<Meta> {
  const res = await fetch(`${API_BASE}/api/meta`);
  if (!res.ok) throw new Error(`GET /api/meta 실패: ${res.status}`);
  return (await res.json()) as Meta;
}

export async function fetchSettings(): Promise<SettingsResponse> {
  const res = await fetch(`${API_BASE}/api/settings`);
  if (!res.ok) throw new Error(`GET /api/settings 실패: ${res.status}`);
  return (await res.json()) as SettingsResponse;
}

/** 409 = 화면이 읽은 뒤 다른 사람이 먼저 저장했다. 호출자가 새로 읽어 다시 보여 줘야 한다. */
export class SettingsConflictError extends Error {}

/** basedOn은 화면이 읽은 설정의 updated_at이다 -- 그사이 다른 저장이 있으면 서버가 409로 거부한다. */
export async function saveSettings(
  settings: PlacementSettings, basedOn: string | null, adminToken: string | null = null,
): Promise<SettingsResponse> {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "PUT", credentials: WITH_COOKIE,
    headers: { "Content-Type": "application/json", ...adminHeaders(adminToken) },
    body: JSON.stringify({ settings, based_on: basedOn }),
  });
  throwIfLoginRequired(res);
  if (res.status === 409) {
    const detail = await res.json().catch(() => ({}));
    throw new SettingsConflictError(String(detail.detail ?? "다른 사용자가 먼저 저장했다"));
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(`설정 저장 실패(${res.status}): ${JSON.stringify(detail.detail ?? "")}`);
  }
  return (await res.json()) as SettingsResponse;
}

/** milpParams는 *플랜을 계산한* 설정이다(최신 설정이 아니다) -- 설정을 바꾼 뒤
 *  예전 플랜을 교체 검토하면, 플랜과 교체 점수가 서로 다른 기준이 된다. null이면
 *  보내지 않아 서버 모델 기본값으로 계산된다(설정을 못 불러온 상태에서 계산한 플랜). */
export async function postWhatif(
  entries: AssignEntry[], swap: Swap, weights: Record<string, number>,
  milpParams: PlacementSettings | null, datasetVersion: string | null = null,
): Promise<WhatifResponse> {
  const res = await fetch(`${API_BASE}/api/whatif`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries, swap, weights,
                           ...(milpParams ? { milp_params: milpParams } : {}),
                           ...(datasetVersion ? { dataset_version: datasetVersion } : {}) }),
  });
  await throwIfDatasetChanged(res);
  if (!res.ok) throw new Error(`POST /api/whatif 실패: ${res.status}`);
  return (await res.json()) as WhatifResponse;
}

/** 검토한 교체를 명단에 적용한 결과를 받는다(K10). 서버는 저장하지 않는다 -- 화면이 들고 있다.
 *  인자는 postWhatif와 같다(같은 기준으로 계산되어야 검토값과 적용값이 일치한다). */
export async function applySwap(
  entries: AssignEntry[], swap: Swap, weights: Record<string, number>,
  milpParams: PlacementSettings | null, datasetVersion: string | null,
): Promise<ApplySwapResponse> {
  const res = await fetch(`${API_BASE}/api/plans/apply-swap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries, swap, weights,
                           ...(milpParams ? { milp_params: milpParams } : {}),
                           ...(datasetVersion ? { dataset_version: datasetVersion } : {}) }),
  });
  await throwIfDatasetChanged(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(`교체 적용 실패(${res.status}): ${JSON.stringify(detail.detail ?? "")}`);
  }
  return (await res.json()) as ApplySwapResponse;
}

/** 한 사람의 달별 투입률 조정을 명단에 적용한다(사람별 달별 조정). 응답은 교체 적용과 같은 모양. */
export async function applyAlloc(
  entries: AssignEntry[], change: AllocChange, weights: Record<string, number>,
  milpParams: PlacementSettings | null, datasetVersion: string | null,
): Promise<ApplySwapResponse> {
  const res = await fetch(`${API_BASE}/api/plans/apply-alloc`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries, change, weights,
                           ...(milpParams ? { milp_params: milpParams } : {}),
                           ...(datasetVersion ? { dataset_version: datasetVersion } : {}) }),
  });
  await throwIfDatasetChanged(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(`투입률 조정 실패(${res.status}): ${JSON.stringify(detail.detail ?? "")}`);
  }
  return (await res.json()) as ApplySwapResponse;
}

/** 플랜에 적용한 교체를 서버에 저장한다(K13). swaps가 비면 저장분을 지운다.
 *  서버는 원 플랜 서명(plan_token)을 검증하고 교체를 다시 적용해 본 뒤 저장한다. */
export async function savePlanEdits(
  planToken: string,
  body: { plan_label: string; base_entries: AssignEntry[]; weights: Record<string, number>;
          milp_params: PlacementSettings | null; dataset_version: string; swaps: Step[];
          expected_revision: number },
): Promise<{ revision: number }> {
  const res = await fetch(`${API_BASE}/api/plans/edits/${encodeURIComponent(planToken)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await throwIfDatasetChanged(res);
  if (res.status === 409) {
    const detail = (await res.json().catch(() => ({}))).detail ?? {};
    if (detail.code === "edits_changed") throw new EditsConflictError(Number(detail.revision ?? 0));
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(`적용 교체 저장 실패(${res.status}): ${JSON.stringify(detail.detail ?? "")}`);
  }
  return (await res.json()) as { revision: number };
}

/** 409 edits_changed: 다른 화면이 먼저 저장했다(서버 revision이 내가 본 것과 다르다). */
export class EditsConflictError extends Error {
  revision: number;
  constructor(revision: number) {
    super("다른 화면에서 먼저 저장했다");
    this.revision = revision;
  }
}

/** 저장된 적용 교체와 서버 revision을 불러온다(저장분이 없으면 steps가 비고 revision 0). */
export async function loadPlanEdits(planToken: string): Promise<SavedPlanEdits> {
  const res = await fetch(`${API_BASE}/api/plans/edits/${encodeURIComponent(planToken)}`);
  if (!res.ok) throw new Error(`적용 교체 불러오기 실패(${res.status})`);
  return (await res.json()) as SavedPlanEdits;
}

export interface OptimizeRequest {
  weights: Record<string, number>;
  milp_params?: Record<string, unknown>;
  n_alternatives?: number;
  dataset_version?: string;
}

/** 브라우저 EventSource는 GET 전용인데 /api/optimize는 POST다 -- 그래서
 *  fetch로 body 스트림을 직접 읽고 SSE 프레임을 손으로 파싱한다. */
export async function* streamOptimize(req: OptimizeRequest): AsyncGenerator<SseEvent> {
  const res = await fetch(`${API_BASE}/api/optimize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ n_alternatives: 3, milp_params: {}, ...req }),
  });
  await throwIfDatasetChanged(res);
  if (!res.ok || !res.body) throw new Error(`POST /api/optimize 실패: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const { events, rest } = parseFrames(buf);
      buf = rest;
      for (const ev of events) yield ev;
    }
  } finally {
    // 호출자가 중간에 빠져나가면(데이터셋 전환 등) 연결을 닫아 남은 스트림을 받지 않는다.
    reader.cancel().catch(() => {});
  }
}

/** 확정 배치를 서버로 보내 PDF를 받아 브라우저에 내려받게 한다.
 *
 *  swap은 whatif가 어떤 교체에 대한 브리핑인지를 PDF에 함께 실어 보내기
 *  위한 것이다 -- whatif만으로는 "무엇을 무엇으로 바꾼 검토인지" PDF가 알
 *  방법이 없다. whatif가 없으면(교체를 시도한 적 없으면) swap도 보내지
 *  않는다. */
export async function downloadReport(
  plan: PlanEvent, whatif: WhatifResponse | null, swap: Swap | null,
  milpParams: PlacementSettings | null = null,
  datasetVersion: string | null = null,
  applied: { base: AssignEntry[]; swaps: Step[] } | null = null,
  basis: { weights: Record<string, number>; planToken: string | null } =
    { weights: {}, planToken: null },
) {
  const body: ReportRequest = {
    plan_label: plan.label,
    entries: plan.entries,
    objective: plan.objective,
    fulfillment: plan.fulfillment,
    optimization_ratio: plan.optimization_ratio,
    unfilled: plan.unfilled,
    briefing: whatif?.briefing ?? null,
    fallback_used: whatif?.fallback_used ?? false,
    swap: whatif ? swap : null,
    objective_delta: whatif?.objective_delta ?? null,
    swap_violations: whatif ? swapWarnings(whatif) : [],
    milp_params: milpParams,
    dataset_version: datasetVersion,
    applied_swaps: applied?.swaps ?? [],
    base_entries: applied ? applied.base : null,
    // 원 플랜 서명 검증과 교체 재계산의 기준 -- 플랜을 계산한 그때의 가중치다.
    weights: basis.weights,
    plan_token: basis.planToken,
  };
  const res = await fetch(`${API_BASE}/api/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await throwIfDatasetChanged(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(`PDF 생성 실패(${res.status}): ${detail.detail ?? ""}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `teamweaver-plan-${plan.label}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // click() 직후 동기로 revoke하면 브라우저가 아직 blob을 읽기 전이라
  // 내려받기가 취소될 수 있다. 한 틱 뒤로 미룬다.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export async function fetchActiveDataset(): Promise<DatasetInfo> {
  const res = await fetch(`${API_BASE}/api/datasets/active`);
  if (!res.ok) throw new Error(`GET /api/datasets/active 실패: ${res.status}`);
  return (await res.json()) as DatasetInfo;
}

/** zip 파일을 원본 본문으로 보낸다(multipart 아님 -- api/routes/datasets.py 참고).
 *  200(전환됨)과 422(검증 오류로 전환 안 됨)는 둘 다 리포트를 담은 정상 결과로 돌려준다.
 *  그 밖(413 크기 초과, 409 처리 중 등)은 이유를 담아 throw한다. */
export async function uploadDataset(file: Blob, adminToken: string | null = null,
): Promise<UploadResult> {
  const res = await fetch(`${API_BASE}/api/datasets`, {
    method: "POST",
    credentials: WITH_COOKIE,
    headers: { "Content-Type": "application/zip", ...adminHeaders(adminToken) },
    body: file,
  });
  throwIfLoginRequired(res);
  if (res.status === 200 || res.status === 422) return (await res.json()) as UploadResult;
  const detail = await res.json().catch(() => ({}));
  throw new Error(`업로드 실패(${res.status}): ${detail.detail ?? ""}`);
}

export async function resetDataset(adminToken: string | null = null): Promise<DatasetInfo> {
  // JSON으로 보낸다: 서버는 교차 사이트 단순 POST를 막으려고 JSON 요청만 받는다.
  const res = await fetch(`${API_BASE}/api/datasets/reset`, {
    method: "POST",
    credentials: WITH_COOKIE,
    headers: { "Content-Type": "application/json", ...adminHeaders(adminToken) },
    body: "{}",
  });
  throwIfLoginRequired(res);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(`되돌리기 실패(${res.status}): ${detail.detail ?? ""}`);
  }
  return (await res.json()) as DatasetInfo;
}
