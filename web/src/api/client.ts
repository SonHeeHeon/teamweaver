import type {
  Meta, AssignEntry, PlacementSettings, PlanEvent, ReportRequest, SettingsResponse, Swap,
  WhatifResponse,
} from "./types";
import { parseFrames, type SseEvent } from "./sse";
import { swapWarnings } from "./whatifWarnings";

/** 개발 중에는 Vite dev 서버(:5173)에서 API(:8000)를 부르므로 절대 URL이 필요하다.
 *  프로덕션 빌드는 FastAPI가 같은 오리진에서 서빙하므로(Task 8) 빈 문자열이면 된다. */
export const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

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
  settings: PlacementSettings, basedOn: string | null,
): Promise<SettingsResponse> {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings, based_on: basedOn }),
  });
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
  milpParams: PlacementSettings | null,
): Promise<WhatifResponse> {
  const res = await fetch(`${API_BASE}/api/whatif`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries, swap, weights, ...(milpParams ? { milp_params: milpParams } : {}) }),
  });
  if (!res.ok) throw new Error(`POST /api/whatif 실패: ${res.status}`);
  return (await res.json()) as WhatifResponse;
}

export interface OptimizeRequest {
  weights: Record<string, number>;
  milp_params?: Record<string, unknown>;
  n_alternatives?: number;
}

/** 브라우저 EventSource는 GET 전용인데 /api/optimize는 POST다 -- 그래서
 *  fetch로 body 스트림을 직접 읽고 SSE 프레임을 손으로 파싱한다. */
export async function* streamOptimize(req: OptimizeRequest): AsyncGenerator<SseEvent> {
  const res = await fetch(`${API_BASE}/api/optimize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ n_alternatives: 3, milp_params: {}, ...req }),
  });
  if (!res.ok || !res.body) throw new Error(`POST /api/optimize 실패: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const { events, rest } = parseFrames(buf);
    buf = rest;
    for (const ev of events) yield ev;
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
  };
  const res = await fetch(`${API_BASE}/api/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
