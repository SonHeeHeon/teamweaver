import type { Meta, AssignEntry, Swap, WhatifResponse } from "./types";
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
