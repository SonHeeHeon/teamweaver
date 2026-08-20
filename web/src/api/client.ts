import type { Meta, AssignEntry, PlanEvent, ReportRequest, Swap, WhatifResponse } from "./types";
import { parseFrames, type SseEvent } from "./sse";

/** 개발 중에는 Vite dev 서버(:5173)에서 API(:8000)를 부르므로 절대 URL이 필요하다.
 *  프로덕션 빌드는 FastAPI가 같은 오리진에서 서빙하므로(Task 8) 빈 문자열이면 된다. */
export const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

export async function fetchMeta(): Promise<Meta> {
  const res = await fetch(`${API_BASE}/api/meta`);
  if (!res.ok) throw new Error(`GET /api/meta 실패: ${res.status}`);
  return (await res.json()) as Meta;
}

export async function postWhatif(
  entries: AssignEntry[], swap: Swap, weights: Record<string, number>,
): Promise<WhatifResponse> {
  const res = await fetch(`${API_BASE}/api/whatif`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries, swap, weights }),
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
