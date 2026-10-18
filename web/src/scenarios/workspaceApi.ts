/** 3가지 핵심 상황 화면의 서버 호출(2026-10-19, claude-b) -- api/routes/workspace.py·scenarios.py.
 *  판을 바꾸는 요청(등록·단계·떨어짐·고정·확정·되돌리기)은 화면이 본 판(dataset_version)을 함께 보낸다 -- 다르면 409. */
import { API_BASE } from "../api/client";
import type { AssignEntry } from "../api/types";

export type Stage = "예정" | "제안" | "수주";
export const STAGE_LABEL: Record<Stage, string> = { 예정: "발주 예정", 제안: "제안 중", 수주: "수주" };
export const GRADES = ["특급", "고급", "중급", "초급"] as const;
export type Grade = (typeof GRADES)[number];
export const SECTORS = ["대내", "대외금융", "대외공공"] as const;

export interface Member { person_id: string; name: string; grade: Grade | null; alloc?: number; locked?: boolean }
export interface Seats { [grade: string]: { need: number; filled: number } }
export interface ProjectItem {
  project_id: string; name: string; start: string | null; end: string | null; monthly_budget: number;
  seats: Seats; team: Member[];
}
export interface PipelineItem extends ProjectItem { stage: Stage; predecessor: string | null; registered_at?: string | null }
export interface OngoingItem extends ProjectItem { locked_all: boolean; locked_count: number; ends_in_horizon: boolean }
export interface HistoryEntry { version: string; at: string; kind: string; note: string }
export interface Workspace {
  dataset_version: string; dataset_id: string; synthetic: boolean | null; editable: boolean; reason?: string;
  horizon_start?: string | null; horizon_last?: string | null;
  pipeline: PipelineItem[]; ongoing: OngoingItem[]; bench: Member[]; history: HistoryEntry[];
}

export interface SkillNeed { name: string; min_months: number; headcount: number }
export interface ProjectForm {
  name: string; sector: (typeof SECTORS)[number]; start_month: string; end_month: string; monthly_budget: number;
  stage: Stage; predecessor: string | null; grades: Partial<Record<Grade, number>>; skills: SkillNeed[];
}

export interface CandidateRow {
  person_id: string; name: string; grade: Grade; alloc: number; source: "bench" | "partly_free" | "pull";
  pulled_from: string[]; delta_total: number; delta: Record<string, number>; new_violations: unknown[];
  category: "bench" | "ending" | "pull"; predecessor_team: boolean; monthly_cost?: number;
}
export interface Candidates { project_id: string; predecessor: string | null; bench: CandidateRow[]; ending: CandidateRow[];
                              pull: CandidateRow[]; note: string }

export interface StandbyRow { person_id: string; name: string; grade: Grade; alloc: number; source: string; delta_total: number }
export interface ApplyHint {
  headcount_add: Record<string, Partial<Record<Grade, number>>>; budget_add: Record<string, number>;
  skills_add: Record<string, SkillNeed[]>;
}
export interface EmergencyPlan {
  dataset_version: string; project_id: string; skills: string[]; added_requirements: { name: string }[];
  best: { accepted: boolean; entries?: AssignEntry[]; added?: { person_id: string; name: string }[];
          diff?: { moved?: { person_id: string; from: string; to: string[] }[] };
          delta?: Record<string, number>; added_cost?: number; new_violations?: unknown[]; termination?: string | null };
  candidates: { bench: CandidateRow[]; spare: CandidateRow[]; pull: CandidateRow[] };
  standby: StandbyRow[]; apply_hint: ApplyHint | null; note: string;
}
export interface SkillDictEntry { name: string; aliases: string[]; category?: string }

/** 서버 오류를 사람이 읽을 문장으로(문자열 detail, {message, violations, report} 모두). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const d = (body as { detail?: unknown }).detail;
    let msg = `요청 실패(${res.status})`;
    if (typeof d === "string") msg = d;
    else if (d && typeof d === "object" && "message" in d) {
      msg = String((d as { message: string }).message);
      const v = (d as { violations?: { message?: string }[] }).violations;
      if (v?.length) msg += " " + v.slice(0, 3).map((x) => x.message).filter(Boolean).join(" · ");
    } else if (Array.isArray(d)) msg = d.map((x) => (x as { msg?: string }).msg).filter(Boolean).join(" · ") || msg;
    if (res.status === 409 && d && typeof d === "object" && (d as { code?: string }).code === "dataset_changed") {
      msg = "다른 곳에서 데이터가 바뀌어 새 데이터로 다시 불러왔습니다. 다시 하세요.";
    }
    throw new ApiError(msg, res.status, d);
  }
  return (await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

type WithWs = { workspace: Workspace; persisted?: boolean; persist_error?: string | null };

/** 바꾼 판은 켜졌지만 서버에 남기지 못했으면(재기동하면 이전 데이터로 뜬다) 완료 문장 뒤에 알린다. */
export function withPersistNote(done: string, res: object): string {
  const r = res as { persisted?: boolean; persist_error?: string | null };
  return r.persisted === false ? `${done} 주의: ${r.persist_error ?? "서버에 저장하지 못했다."}` : done;
}

export const fetchWorkspace = () => call<Workspace>("/api/workspace");
export const fetchProjectForm = (id: string) => call<ProjectForm>(`/api/workspace/projects/${encodeURIComponent(id)}/form`);
export const fetchSkillDictionary = () => call<SkillDictEntry[]>("/api/skills/dictionary");
export const postProject = (v: string, project: ProjectForm) =>
  call<WithWs & { project_id: string }>("/api/workspace/projects", json("POST", { dataset_version: v, project }));
export const patchStage = (v: string, id: string, stage: Stage) =>
  call<WithWs>(`/api/workspace/projects/${encodeURIComponent(id)}`, json("PATCH", { dataset_version: v, stage }));
export const dropProject = (v: string, id: string) =>
  call<WithWs & { freed: string[] }>(`/api/workspace/projects/${encodeURIComponent(id)}?dataset_version=${encodeURIComponent(v)}`,
                                     { method: "DELETE" });
export const postLock = (v: string, projectId: string, people: string[] | null, locked: boolean) =>
  call<WithWs>("/api/workspace/lock", json("POST", { dataset_version: v, project_id: projectId, people, locked }));
export const postApply = (v: string, body: { entries: AssignEntry[]; note?: string;
  source: "auto" | "manual" | "emergency" | "plan" } & Partial<ApplyHint>) =>
  call<WithWs & { changes: { added: string[]; removed: string[]; changed: string[] } }>(
    "/api/workspace/apply", json("POST", { dataset_version: v, ...body }));
export const postUndo = (v: string) => call<WithWs>("/api/workspace/undo", json("POST", { dataset_version: v }));
export const fetchCandidates = (id: string, v: string) =>
  call<Candidates>(`/api/pipeline/${encodeURIComponent(id)}/candidates?dataset_version=${encodeURIComponent(v)}`);
export const postEmergency = (body: { dataset_version: string; project_id: string; n: number; grade: Grade | null;
  skills: string[]; pull_budget: number; note: string }) => call<EmergencyPlan>("/api/emergency/plan", json("POST", body));
export const fetchStandby = (v: string, projectId: string) =>
  call<{ source: string | null; computed_at: string | null; projects: Record<string, StandbyRow[]> }>(
    `/api/emergency/standby?dataset_version=${encodeURIComponent(v)}&project_id=${encodeURIComponent(projectId)}`);

/** 판이 바뀌어 거절됐는가(409 dataset_changed) -- 화면이 새 판을 다시 읽어야 한다. */
export function isDatasetChanged(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409 && !!e.detail && typeof e.detail === "object"
    && (e.detail as { code?: string }).code === "dataset_changed";
}

/** 지금 배치 전체(파이프라인 + 진행 사업) -- 손 편성·확정의 기준 명단. */
export function currentEntries(ws: Workspace): AssignEntry[] {
  const out: AssignEntry[] = [];
  for (const p of [...ws.pipeline, ...ws.ongoing]) {
    for (const m of p.team) out.push({ person_id: m.person_id, project_id: p.project_id, alloc: m.alloc ?? 1 });
  }
  return out;
}
