/** 3가지 핵심 상황 화면의 서버 호출(2026-10-19, claude-b) -- api/routes/workspace.py·scenarios.py.
 *  판을 바꾸는 요청(등록·단계·떨어짐·고정·확정·되돌리기)은 화면이 본 판(dataset_version)을 함께 보낸다 -- 다르면 409. */
import { API_BASE } from "../api/client";
import type { AssignEntry, Lifecycle } from "../api/types";

export type Stage = "예정" | "제안" | "수주";
export const STAGE_LABEL: Record<Stage, string> = { 예정: "발주 예정", 제안: "제안 중", 수주: "수주" };
export const GRADES = ["특급", "고급", "중급", "초급"] as const;
export type Grade = (typeof GRADES)[number];
export const SECTORS = ["대내", "대외금융", "대외공공"] as const;

export interface Member { person_id: string; name: string; grade: Grade | null; alloc?: number; locked?: boolean;
                         /** 계획 기간 중 투입할 수 없는 달이 있으면 그 사유(쉬는 인력 목록) */
                         unavailable?: string | null }
export interface Seats { [grade: string]: { need: number; filled: number } }
export interface ProjectItem {
  project_id: string; name: string; start: string | null; end: string | null; monthly_budget: number;
  seats: Seats; team: Member[];
  /** 사업 수명 주기(2026-10-19): 단계·실제 시작·추가 과제 상위·외주(참고) */
  lifecycle?: Lifecycle | null;
}
export interface PipelineItem extends ProjectItem {
  stage: Stage; predecessor: string | null; registered_at?: string | null;
  /** 추가 과제(2026-10-19): 진행 중인 상위 사업과 외주 인원(참고값, 계산에 안 씀) */
  parent?: string | null; external_headcount?: number;
}
export interface OngoingItem extends ProjectItem { locked_all: boolean; locked_count: number; ends_in_horizon: boolean; addons?: string[] }
export interface HistoryEntry { version: string; at: string; kind: string; note: string;
  /** 확정 취소(2026-10-21): 이 확정을 취소한 판이 이력에 있다 · 지금 취소할 수 있다(확정이고 직전 판이 남아 있음) */
  cancelled?: boolean; cancellable?: boolean;
  /** 그 확정이 사람을 새로 넣은 사업(확정만) -- "지금 팀" 옆 확정 취소가 이 사업의 마지막 확정을 찾는다 */
  projects?: string[];
  /** 그 확정이 사업마다 새로 넣은 사람 -- "지금 팀"에 아직 남은 사람을 넣은 확정을 고른다 */
  joined?: Record<string, string[]> }
/** 확정 취소 미리 보기 -- 돌릴 것·건너뛸 것(그 뒤 다른 변경)·위반이라 하지 않을 것(대기로 남음)·남는 새 위반 */
export interface CancelPreview {
  target: { version: string; kind: string; note: string; at: string };
  revert: string[]; skipped: string[]; dropped: string[]; new_violations: string[]; ok: boolean;
  /** 취소할 수 없는 까닭(되돌릴 것이 남지 않음 · 새 위반) -- ok면 null */
  blocked?: string | null;
}
/** 프로젝트 동시 수행 허용 사유(2026-10-19 사용자 결정 -- 사람 단위, 사유 필수). core/ingest/concurrency.REASONS와 같다. */
export const CONCURRENCY_REASONS = ["인력 부족", "기술 지원", "긴급 지원", "기타"] as const;
export type ConcurrencyReason = (typeof CONCURRENCY_REASONS)[number];
export interface ConcurrencyGrant extends Member { max_projects: number; reason: ConcurrencyReason; memo: string; granted_at: string }
export interface Workspace {
  dataset_version: string; dataset_id: string; synthetic: boolean | null; editable: boolean; reason?: string;
  horizon_start?: string | null; horizon_last?: string | null;
  pipeline: PipelineItem[]; ongoing: OngoingItem[]; bench: Member[]; history: HistoryEntry[];
  /** 프로젝트 동시 수행을 허용받은 사람(사유와 함께) */
  concurrency?: ConcurrencyGrant[];
  /** 지금 데이터에 미리 계산된 운영 중 비교 K 목록(없으면 빈 목록) -- 화면 기본 "최대 K"를 맞춘다 */
  operating_ks?: number[];
}

export interface SkillNeed { name: string; min_months: number; headcount: number }
export interface ProjectForm {
  name: string; sector: (typeof SECTORS)[number]; start_month: string; end_month: string; monthly_budget: number;
  stage: Stage; predecessor: string | null; grades: Partial<Record<Grade, number>>; skills: SkillNeed[];
  parent?: string | null; external_headcount?: number;
}

export interface CandidateRow {
  person_id: string; name: string; grade: Grade; alloc: number; source: "bench" | "partly_free" | "pull" | "concurrent";
  pulled_from: string[]; delta_total: number; delta: Record<string, number>; new_violations: unknown[];
  /** 점수 변화 나눔(2026-10-19): 메운 자리 수(생기면 음수)와 빈자리 몫을 뺀 변화 -- components/scoreSplit */
  unfilled_seats_delta?: number; delta_excl_unfilled?: number;
  category: "bench" | "ending" | "pull"; predecessor_team: boolean; parent_team?: boolean; monthly_cost?: number;
  /** 전일 투입: 원래 사업을 유지한 채 들어오려면 프로젝트 동시 수행 허용이 필요하다(확정 때 함께 적는다) */
  needs_grant?: boolean;
  /** 동시 수행 지원 행: 이 사람으로 확정할 때 서버가 계산한 증원(넘친 만큼의 정원·예산 + 더할 요구 기술) */
  apply_hint?: ApplyHint;
  /** 사업별 정원 변화(2026-10-20): change > 0 = 그 사업 빈자리를 채움, < 0 = 빈자리가 생김(빼 온 사람의 원래 사업) */
  seat_effects?: SeatEffect[];
  /** 점수 변화 중 기술 적합 몫 · 협업 몫(협업 시너지 + 익숙한 쌍 감점, 모든 사업) */
  skill_delta?: number; collab_delta?: number;
  /** 이 사업에서 함께하게 될 사람과의 쌍 중 계산에 들어가는 것(협업은 두 사람 사이 값이다) */
  partners?: (PairTerm & { person_id: string; name: string })[];
  /** 이 사람을 넣으면 다른 등급 빈자리를 메우는 등급 대체(2026-10-21) -- 위반이 아니라 확정 때 사유를 받아 정원을 바꾼다 */
  grade_swap?: GradeSwap[];
}
/** 등급 대체(2026-10-21 사용자 결정 "위 아래 모두 허용, 사유는 전부, 예산을 넘으면 증액"): seat 자리를 grade 사람으로 count개 */
export interface GradeSwap { seat: Grade; grade: Grade; count: number }
export interface SeatEffect { project_id: string; name: string; change: number }
/** synergy = 협업 시너지(함께 일한 경력·서로 남긴 평가, 상위 쌍만), familiar = 최근 너무 오래 함께해 익숙한 쌍 감점 */
export interface PairTerm { kind: "synergy" | "familiar"; value: number; c?: number }
/** 빼 온 자리 결정(2026-10-21 사용자 "빼간 자리를 매꿔야한다는게 있어야할거같네. 못메꾼다면 메꾸지 않는다라는 표기를 통해 더이상 그건
 *  컨트롤 안해도 되는걸로"): 이 확정으로 진행 사업에 새로 생긴 빈자리(사업 → 등급·수). 메우거나 "메우지 않음"(사유)이어야 확정된다. */
export type Holes = Record<string, { grade: Grade; count: number }[]>;
/** 빈자리를 메울 수 있는 사람(대기 인력 등 -- 다른 사업에서 또 빼 오지 않는다), 그 등급이 먼저 */
export interface FillOption { person_id: string; name: string; grade: Grade; alloc: number; skill_fit: number; delta_total: number }
/** 같은 확정에서 사람이 빠진 사업에 들어간 사람("J014 자리 ← 전다은") */
export interface FillLine { project_id: string; person_id: string; name: string; grade: Grade; from_bench: boolean }
export interface HoleView { holes?: Holes; fill_options?: Record<string, FillOption[]>; fills?: FillLine[] }
/** 메울 사람(빼 온 사업에 넣는다) */
export interface FillPick { person_id: string; project_id: string; alloc: number }
/** 확정 요청의 "메우지 않음" -- 그 사업 그 등급 정원을 count만큼 줄인다(예산 그대로) */
export interface SeatWaiver { project_id: string; grade: Grade; count: number; reason: string }

/** "넣기"로 고른 사람 전체를 넣었을 때(확정 전 미리 계산, 2026-10-20) */
export interface DraftSummary extends HoleView {
  seats: PipelineItem["seats"]; seat_effects: SeatEffect[]; delta_total: number; skill_delta: number; collab_delta: number;
  unfilled_seats_delta: number; new_violations: string[];
  pairs: (PairTerm & { a: string; a_name: string; b: string; b_name: string })[];
  /** 신규 사업: 고른 사람으로 생기는 등급 대체와, 대체가 있을 때 예산을 넘는 만큼의 월 예산 증액(확정에 그대로 보낸다) */
  grade_swaps?: GradeSwap[]; budget_add?: number;
  /** 빼 온 사업을 다른 등급으로 메운 등급 대체·그로 넘는 예산(사업별, 2026-10-21) -- 확정에 함께 보낸다 */
  side_grade_swaps?: Record<string, GradeSwap[]>; side_budget_add?: Record<string, number>;
}
export interface Pick { person_id: string; alloc: number; pulled_from: string[] }
/** 그 사업 기간에 휴직 등으로 넣을 수 없는 사람(2026-10-19) -- 후보에서 빠지지만 화면은 사유와 함께 비활성으로 보인다. */
export interface UnavailableRow { person_id: string; name: string; grade: Grade; months: number[]; reason: string }
export interface Candidates { project_id: string; predecessor: string | null; parent?: string | null; bench: CandidateRow[]; ending: CandidateRow[];
                              pull: CandidateRow[]; unavailable?: UnavailableRow[]; note: string; draft?: DraftSummary | null }

export interface StandbyRow { person_id: string; name: string; grade: Grade; alloc: number; source: string; delta_total: number;
                             unfilled_seats_delta?: number; delta_excl_unfilled?: number }
export interface ApplyHint {
  headcount_add: Record<string, Partial<Record<Grade, number>>>; budget_add: Record<string, number>;
  skills_add: Record<string, SkillNeed[]>;
  /** 빈자리를 다른 등급으로 메우는 대체(사업별) -- 확정 때 사유를 붙여 grade_swaps로 보낸다. 남은 넘침만 headcount_add(증원) */
  grade_swaps?: Record<string, GradeSwap[]>;
}
export interface EmergencyPlan {
  dataset_version: string; project_id: string; skills: string[]; added_requirements: { name: string }[];
  best: { accepted: boolean; entries?: AssignEntry[]; added?: { person_id: string; name: string }[];
          diff?: { moved?: { person_id: string; name?: string; from: string; to: string[] }[] };
          delta?: Record<string, number>; added_cost?: number; new_violations?: unknown[]; termination?: string | null;
          unfilled_seats_delta?: number; delta_excl_unfilled?: number;
          /** 빼 온 자리: 같은 조합에서 메운 사람 · 못 메운 빈자리(확정 때 "메우지 않음" 사유, 2026-10-21) */
          fills?: FillLine[]; holes?: Holes };
  candidates: { bench: CandidateRow[]; spare: CandidateRow[]; pull: CandidateRow[] };
  standby: StandbyRow[]; unavailable?: UnavailableRow[]; apply_hint: ApplyHint | null; note: string;
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
/** 사업 양식. addon이면 그 진행 사업에 붙일 추가 과제 미리 채우기(start = 시작 달, 없으면 상위 시작). */
export const fetchProjectForm = (id: string, addon?: { start?: string | null }) =>
  call<ProjectForm>(`/api/workspace/projects/${encodeURIComponent(id)}/form`
    + (addon ? `?as=addon${addon.start ? `&start=${encodeURIComponent(addon.start)}` : ""}` : ""));
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
  /** 긴급 투입 "무슨 일인지" 원문 -- 검토 자료 상황 메모의 처음 값(AI에 보내지 않음) */
  situation?: string;
  source: "auto" | "manual" | "emergency" | "plan";
  concurrency_add?: { person_id: string; memo?: string; reason?: ConcurrencyReason }[];
  /** 등급 대체(사유 필수) -- 서버가 같은 규칙으로 다시 계산해 맞춰 본다 */
  grade_swaps?: (GradeSwap & { project_id: string; reason: string })[];
  /** 새로 생긴 빈자리 중 메우지 않을 것(사유 필수, 2026-10-21) */
  seat_waivers?: SeatWaiver[];
  /** 연간 계획 "고정 풀고 다시 배치"로 짠 안의 확정 -- 빠지는 고정을 함께 푼다(source plan만) */
  release_locks?: boolean } & Partial<Omit<ApplyHint, "grade_swaps">>) =>
  call<WithWs & { changes: { added: string[]; removed: string[]; changed: string[] } }>(
    "/api/workspace/apply", json("POST", { dataset_version: v, ...body }));
/** 확정 전 미리 계산(2026-10-21): 지금 배치 → entries를 확정하면 생길 옮김·등급 대체·예산 증액·새 위반·고정 충돌(데이터는 그대로) */
export interface PreviewMove { person_id: string; name: string; from: string[]; to: string[] }
export interface ConfirmPreview extends HoleView {
  dataset_version: string; moved: PreviewMove[]; joined: PreviewMove[]; left: PreviewMove[]; changed: boolean;
  grade_swaps: Record<string, GradeSwap[]>; budget_add: Record<string, number>; new_violations: string[]; locked_dropped: string[];
}
export const postPreview = (v: string, entries: AssignEntry[], signal?: AbortSignal) =>
  call<ConfirmPreview>("/api/workspace/preview", { ...json("POST", { dataset_version: v, entries }), signal });
export const postConcurrency = (v: string, personId: string, allow: boolean, reason?: ConcurrencyReason, memo?: string,
                                maxProjects: 2 | 3 = 2) =>
  call<WithWs>("/api/workspace/concurrency", json("POST", { dataset_version: v, person_id: personId, allow,
                                                            ...(allow ? { reason, memo: memo ?? "", max_projects: maxProjects } : {}) }));

export interface ConcurrencyCandidate { person_id: string; name: string; grade: Grade; skill_fit: number; monthly_cost: number;
                                        busy_with: string[]; already_allowed: boolean; needs_more: boolean }
export interface ShortfallHint { project_id: string; grade: Grade; missing: number; budget_left: number; free_eligible: number;
                                 candidates: ConcurrencyCandidate[] }
/** 빈자리마다 프로젝트 동시 수행 허용 후보(다시 풀지 않는다). */
export const fetchConcurrencyCandidates = (v: string, entries: AssignEntry[], weights: Record<string, number> = {}) =>
  call<{ dataset_version: string; shortfalls: ShortfallHint[] }>("/api/concurrency/candidates",
                                                               json("POST", { dataset_version: v, entries, weights }));
export const fetchCancelPreview = (v: string, h: { version: string; at: string }, signal?: AbortSignal) =>
  call<CancelPreview>(`/api/workspace/cancel?dataset_version=${encodeURIComponent(v)}&version=${encodeURIComponent(h.version)}`
                      + `&at=${encodeURIComponent(h.at)}`, { signal });
export const postCancel = (v: string, h: { version: string; at: string }) =>
  call<WithWs & { cancel: CancelPreview }>("/api/workspace/cancel",
                                          json("POST", { dataset_version: v, target_version: h.version, target_at: h.at }));
export const postUndo = (v: string) => call<WithWs>("/api/workspace/undo", json("POST", { dataset_version: v }));
/** 후보 세 갈래. picks(넣기로 고른 사람)가 있으면 그 사람들을 팀원으로 친 다시 채점 + 고른 사람 전체 미리 계산(draft) -- 데이터는 바뀌지 않는다. */
export const fetchCandidates = (id: string, v: string, picks: Pick[] = [], fills: FillPick[] = []) =>
  picks.length
    ? call<Candidates>(`/api/pipeline/${encodeURIComponent(id)}/candidates`, json("POST", { dataset_version: v, picks, fills }))
    : call<Candidates>(`/api/pipeline/${encodeURIComponent(id)}/candidates?dataset_version=${encodeURIComponent(v)}`);
export const postEmergency = (body: { dataset_version: string; project_id: string; n: number; grade: Grade | null;
  skills: string[]; pull_budget: number; note: string }) => call<EmergencyPlan>("/api/emergency/plan", json("POST", body));
/** 긴급 투입에서 "넣기"로 고른 사람(grant = 동시 수행 지원 -- 확정 때 허용을 함께 적는다) */
export interface EmergencyPick extends Pick { grant?: boolean }
/** 긴급 투입 손 편성 미리 계산(2026-10-20) -- 확정할 배치와 서버가 계산한 증원(apply_hint)까지 */
export interface EmergencyDraft extends DraftSummary { apply_hint: ApplyHint; entries: AssignEntry[] }
export const postEmergencyPreview = (body: { dataset_version: string; project_id: string; skills: string[]; picks: EmergencyPick[];
                                             pull_budget: number; fills?: FillPick[] }, signal?: AbortSignal) =>
  call<{ dataset_version: string; project_id: string; draft: EmergencyDraft; candidates: EmergencyPlan["candidates"]; note: string }>(
    "/api/emergency/preview", { ...json("POST", body), signal });
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
