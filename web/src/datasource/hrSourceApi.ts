/** 데이터 원천 API(claude-a 2026-10-19, `api/routes/hr_source.py`): 인사 시스템 API 받기(기본)·CSV(대체)·진행 중 사업 기술 경력 갱신 주체. */
import { API_BASE, AdminLoginRequiredError } from "../api/client";
import type { DatasetInfo } from "../api/types";

export type Owner = "service" | "source";

export interface SyncResult {
  at: string;
  trigger: "nightly" | "manual";
  status: "off" | "fail" | "invalid" | "unchanged" | "applied";
  detail: string;
  host?: string;
  changed?: string[];
  version?: string;
  /** 켰지만 서버에 저장하지 못했다(재기동하면 이전 데이터) */
  persisted?: boolean;
  persist_error?: string | null;
}

export interface Aging {
  owner: Owner;
  as_of: string | null;
  cutoff: string | null;
  people: number;
  skills: number;
  max_months: number;
  /** "no_work_skills" = 업무별 사용 기술 표가 없어 늘리지 못함("이미 최신"과 다르다) */
  skipped?: "no_work_skills" | null;
}

export interface HrSourceStatus {
  mode: "api" | "csv";
  config_error: string | null;
  host: string | null;
  tables: { name: string; from: "api" | "data"; service: boolean }[];
  last: SyncResult | null;
  running: boolean;
  editable: boolean;
  not_editable_reason: string | null;
  skill_months_owner: Owner | null;
  aging: Aging | null;
  as_of: string | null;
}

export async function fetchHrSource(): Promise<HrSourceStatus | null> {
  const res = await fetch(`${API_BASE}/api/hr-source`, { credentials: "include" });
  if (res.status === 404) return null;                           // 이 기능이 없는 서버
  if (!res.ok) throw new Error(`데이터 원천 상태를 읽지 못했다(${res.status})`);
  return (await res.json()) as HrSourceStatus;
}

async function post(path: string, body: unknown, token: string | null) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", ...(token ? { "X-Admin-Token": token } : {}) },
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new AdminLoginRequiredError("관리자 로그인이 필요하다.");
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data?.detail;
    throw new Error(typeof d === "string" ? d : d?.message ?? `요청 실패(${res.status})`);
  }
  return data;
}

export async function syncNow(token: string | null = null):
    Promise<{ result: SyncResult; dataset: DatasetInfo; status: HrSourceStatus }> {
  return post("/api/hr-source/sync", {}, token);
}

export async function setOwner(datasetVersion: string, owner: Owner, token: string | null = null):
    Promise<{ dataset: DatasetInfo; status: HrSourceStatus; unchanged?: boolean; persisted?: boolean;
              persist_error?: string | null }> {
  return post("/api/hr-source/skill-months-owner", { dataset_version: datasetVersion, owner }, token);
}
