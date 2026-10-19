/** 야간 배치 API(claude-b `api/nightly.py`, 화면은 claude-a 2026-10-19 -- claude-b 요청 (1)(2)). */
import { API_BASE, AdminLoginRequiredError } from "../api/client";

export interface NightlyReport {
  status?: "ok" | "attention";
  summary?: string;
  attention?: { title: string | null; detail: string | null }[];
  finished_at?: string;
  started_at?: string;
  stopped?: boolean;
}

export interface NightlyStatus {
  enabled: boolean;
  window: string;
  next_run_at: string | null;
  running: boolean;
  progress: { phase?: string; detail?: string } | null;
  last: NightlyReport | null;
}

export async function fetchNightly(): Promise<NightlyStatus | null> {
  const res = await fetch(`${API_BASE}/api/nightly`, { credentials: "include" });
  if (res.status === 503 || res.status === 404) return null;      // 야간 배치가 없는 서버
  if (!res.ok) throw new Error(`밤 배치 상태를 읽지 못했다(${res.status})`);
  return (await res.json()) as NightlyStatus;
}

async function post(path: string, body: unknown, token: string | null): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", ...(token ? { "X-Admin-Token": token } : {}) },
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new AdminLoginRequiredError("관리자 로그인이 필요하다.");
  return res;
}

/** 지금 한 번 돌리기(2~300분). 이미 돌고 있으면 false. */
export async function runNightly(minutes: number, token: string | null = null): Promise<boolean> {
  const res = await post("/api/nightly/run", { minutes }, token);
  if (res.status === 409) return false;
  if (!res.ok) throw new Error(`밤 배치를 시작하지 못했다(${res.status})`);
  return true;
}

export async function stopNightly(token: string | null = null): Promise<void> {
  const res = await post("/api/nightly/stop", {}, token);
  if (!res.ok) throw new Error(`밤 배치를 멈추지 못했다(${res.status})`);
}
