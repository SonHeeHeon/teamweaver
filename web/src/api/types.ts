// api/schemas.py 의 pydantic 모델을 그대로 미러한다.
// 서버 계약이 바뀌면 여기도 바꿔야 하고, 안 바꾸면 컴파일이 깨지는 것이 목적이다.

export interface Person {
  id: string;
  name: string;
  grade: string;
  skills: Record<string, number>;
}

export interface Project {
  id: string;
  name: string;
  sector: string;
  phase: string;
  start_month: number;
  end_month: number;
  grade_headcount: Record<string, number>;
  monthly_budget: number;
}

export interface Cowork {
  a_id: string;
  b_id: string;
  co_months: number;
  project_count: number;
}

export interface Meta {
  people: Person[];
  projects: Project[];
  skills: string[];
  review_items: string[];
  coworks: Cowork[];
  /** 이 meta가 나온 활성 데이터셋(K9). 계산 요청에 실어 보내 서버가 불일치를 409로 막는다. */
  dataset_version: string;
}

export interface AssignEntry {
  person_id: string;
  project_id: string;
  /** 월별 투입률이면 진행 달 평균. */
  alloc: number;
  /** 월별 투입률(키 = 진행 달 0~5). 없으면 모든 진행 달이 alloc. */
  monthly_alloc?: Record<string, number>;
}

/** POST /api/optimize 의 `event: plan` 프레임 payload */
export interface PlanEvent {
  label: string;
  entries: AssignEntry[];
  objective: number;
  unfilled: string[];
  fulfillment: number;
  /** 교체를 적용한 명단에 제약 위반이 있으면 null(산정 불가 -- 상한은 제약을 지킨 배치에만 의미). */
  optimization_ratio: number | null;
  index: number;
  cached: boolean;
  dataset_version?: string;
  /** 서버가 이 플랜을 계산했다는 서명(K10). PDF가 원 플랜을 검증하는 데 쓴다. */
  plan_token?: string;
  /** 솔버가 시간 한도에서 멈춘 해(최선임을 증명하기 전). */
  time_limited?: boolean;
}

/** 계산에 실제로 쓸 설정: 자동 계산 시간이면 서버가 정한 값으로 바꿔 넣는다. */
export function effectiveSettings(resp: SettingsResponse): PlacementSettings {
  const s = resp.settings;
  return s.time_limit_auto !== false && resp.effective_time_limit
    ? { ...s, time_limit: resp.effective_time_limit } : s;
}

/** 브리핑 근거 하나(K5). quote = 원문 그대로(검증됨), summary = 파서가 바꿔 쓴 문장,
 *  label = 실데이터라 원문 비공개(항목 라벨만). */
export interface Evidence {
  source_id: string;
  reviewer_id: string;
  kind: "quote" | "summary" | "label";
  text: string;
}

export interface Briefing {
  rationale: string;
  risks: string[];
  alternatives: string[];
  /** 구버전 서버 응답에는 없을 수 있다. */
  evidence?: Evidence[];
}

export interface ObjectiveBreakdown {
  skill: number;
  synergy: number;
  overfamiliarity: number;
  unfilled: number;
  total: number;
}

export interface Violation {
  code: string;
  location: string;
  actual: number;
  limit: number;
  message: string;
}

export interface Shortfall {
  project_id: string;
  grade: string;
  missing: number;
}

/** objective_delta는 교체 전후를 현행 MILP 전체 목적으로 재평가한 차이(참고값, 재최적화 아님).
 *  new_*는 교체로 새로 생긴 것만, feasible은 교체 후 배치에 제약 위반이 없는지. */
export interface WhatifResponse {
  objective_delta: number;
  before: ObjectiveBreakdown;
  after: ObjectiveBreakdown;
  new_violations: Violation[];
  new_shortfalls: Shortfall[];
  feasible: boolean;
  briefing: Briefing;
  fallback_used: boolean;
}

export interface Swap {
  out_person_id: string;
  in_person_id: string;
  project_id: string;
}

/** POST /api/report 의 요청 본문. fallback_used/swap/objective_delta는
 *  화면의 What-if 결과를 PDF에도 그대로 실어 나르기 위한 필드다 -- 화면의
 *  "규칙 기반(LLM 미사용)" 배지는 PDF에 자동으로 따라오지 않는다. */
export interface ReportRequest {
  plan_label: string;
  entries: AssignEntry[];
  objective: number;
  fulfillment: number;
  optimization_ratio: number | null;
  unfilled: string[];
  briefing: Briefing | null;
  fallback_used: boolean;
  swap: Swap | null;
  objective_delta: number | null;
  swap_violations: string[];
  /** 이 플랜을 계산한 배치 설정(K8). PDF에 "계산 기준"으로 표시한다. */
  milp_params: PlacementSettings | null;
  /** 명단을 계산한 데이터셋. 서버 활성 데이터셋과 다르면 PDF를 거부한다(K9). */
  dataset_version: string | null;
  /** 원 플랜에 적용한 교체(K10, 적용 순서)와 원 플랜 명단. 서버가 base_entries에서 다시
   *  적용해 명단·지표·Δ·경고·위반을 계산한다 -- 화면이 계산한 수치는 보내지 않는다. */
  applied_swaps: Step[];
  base_entries: AssignEntry[] | null;
  weights: Record<string, number>;
  /** 원 플랜의 서버 서명. 없으면 PDF에 "미검증"으로 표시된다. */
  plan_token: string | null;
}

/** 적용한 교체 한 건. warnings는 그 교체로 새로 생긴 위반·미충원 문장(swapWarnings). */
export interface AppliedSwap {
  out_person_id: string;
  in_person_id: string;
  project_id: string;
  objective_delta: number;
  feasible: boolean;
  warnings: string[];
}

/** 적용 단계: 한 사람의 한 프로젝트 투입률을 달별로 바꾼다(사람별 달별 조정). 키 = 진행 달(0~5). */
export interface AllocChange {
  kind: "alloc";
  person_id: string;
  project_id: string;
  monthly_alloc: Record<string, number>;
}

/** 적용 이력의 한 단계(교체 또는 달별 조정). kind가 없으면 교체다(예전 기록). */
export type Step = (Swap & { kind?: "swap" }) | AllocChange;
export type AppliedStep = Step & { objective_delta: number; feasible: boolean; warnings: string[] };

export function isAlloc(s: Step): s is AllocChange {
  return (s as AllocChange).kind === "alloc";
}

/** 결과 칸을 뺀 요청용 단계(저장·PDF 본문). */
export function stepBody(s: Step): Step {
  return isAlloc(s)
    ? { kind: "alloc", person_id: s.person_id, project_id: s.project_id, monthly_alloc: s.monthly_alloc }
    : { out_person_id: s.out_person_id, in_person_id: s.in_person_id, project_id: s.project_id };
}

/** POST /api/plans/apply-swap 응답 -- 교체 후 명단과 그 명단 전체의 재평가(K10). */
export interface ApplySwapResponse {
  entries: AssignEntry[];
  evaluation: { objective: ObjectiveBreakdown; violations: Violation[]; shortfalls: Shortfall[] };
  objective_delta: number;
  feasible: boolean;
  objective: number;
  fulfillment: number;
  optimization_ratio: number | null;
  unfilled: string[];
  warnings: string[];
}

/** 관리자 배치 설정(K8) -- api/settings.py PlacementSettings 미러. */
export interface PlacementSettings {
  min_alloc: number;
  clique_threshold_months: number;
  /** 익숙한 쌍을 셀 최근 기간(개월). null = 전체 이력(예전 의미). 서버 기본 36(2026-10-06 사용자 결정). */
  clique_window_months?: number | null;
  lam: number;
  mu: number;
  time_limit: number;
  gap: number;
  /** 한 사람이 같은 달에 맡는 프로젝트 수 상한(C6). */
  max_concurrent_projects: number;
  /** 투입률 방식: fixed = 기간 내내 한 비율, monthly = 달마다 따로. */
  allocation_mode: "fixed" | "monthly";
  /** 계산 시간 자동(인원 기준). 켜져 있으면 서버가 정한 effective_time_limit을 쓴다. */
  time_limit_auto?: boolean;
}

export interface TimeHint {
  n_people: number; per_solve_s: number; worst_case_total_s: number; measured: boolean; basis: string;
}

/** GET/PUT /api/settings 응답. bounds는 서버 pydantic 제약에서 만든 값이다 --
 *  화면 검사 범위를 서버와 이중으로 정의하지 않는다. */
export interface SettingsResponse {
  settings: PlacementSettings;
  defaults: PlacementSettings;
  bounds: Partial<Record<keyof PlacementSettings, { min: number; max: number }>>;
  updated_at: string | null;
  load_error: string | null;
  /** 지금 데이터 규모의 권장 계산 시간(리허설 측정). 화면 안내용. */
  recommended_time?: TimeHint | null;
  /** 월별 투입률 기준 권장 시간(더 길다). */
  recommended_time_monthly?: TimeHint | null;
  /** 지금 설정으로 실제로 쓸 계산 시간(자동이면 인원 기준 권장값). 계산 요청에는 이 값을 보낸다. */
  effective_time_limit?: number | null;
}

/** GET /api/datasets/active -- 지금 서버가 계산에 쓰는 데이터셋(K9). */
export interface DatasetInfo {
  dataset_id: string;
  version: string;
  source: "fixture" | "upload" | "demo-bundle";
  synthetic: boolean | null;
  people: number;
  projects: number;
  activated_at: string;
  /** 부팅 때 저장된 업로드 데이터를 복원하지 못해 기본 데이터로 떴으면 그 이유(K13). */
  restore_error?: string | null;
  /** 원천 파일만의 해시(다시 판정해도 같다). version은 LLM 판정값까지 담은 계산 버전. */
  content_version?: string;
  /** 평가 사유(글) 판정: llm = 지금 LLM이 매김, fixture = 가상 데이터 생성 때 LLM 값, items = LLM 실패로 항목 점수. */
  review_judge?: "llm" | "fixture" | "items" | "blocked";
  judge_model?: string | null;
  judge_host?: string | null;
  /** 보낸 곳: openai(회사 밖) | onprem(사내로 확인) | unknown(사내인지 확인 안 됨). */
  judge_location?: "openai" | "onprem" | "unknown" | null;
  /** 회사 밖일 수 있는 곳(openai·unknown)으로 보냈는가. */
  judge_external?: boolean | null;
  judge_error?: string | null;
  /** 다음 업로드의 글을 보낼 곳(GET /api/datasets/active). */
  judge_endpoint?: { host: string; location: "openai" | "onprem" | "unknown"; external: boolean; model: string;
                     /** 실데이터도 회사 밖일 수 있는 곳으로 보내도록 서버가 허용했는가. */
                     external_allowed?: boolean };
}

/** core.ingest IngestReport의 Issue 그대로. row는 헤더를 뺀 1부터, 파일 단위 문제면 null. */
export interface IngestIssue {
  level: "error" | "warning";
  file: string;
  row: number | null;
  column: string | null;
  message: string;
}

export interface IngestReportOut {
  errors: IngestIssue[];
  warnings: IngestIssue[];
  notes: string[];
  row_counts: Record<string, number>;
}

/** POST /api/datasets 응답(200 전환됨 / 422 전환 안 됨). report가 null이면 zip 자체가 문제다. */
export interface UploadResult {
  activated: boolean;
  dataset?: DatasetInfo;
  report: IngestReportOut | null;
  detail?: string;
  /** 전환은 됐지만 서버 저장에 실패했으면 false -- 재기동하면 기본 데이터로 돌아간다(K13). */
  persisted?: boolean;
  persist_error?: string | null;
}

/** GET /api/plans/edits/{token} -- 저장된 적용 교체를 서버가 원 플랜에서 다시 적용한 단계(K13). */
export interface SavedPlanEdits {
  swaps: Step[];
  steps: (ApplySwapResponse & { swap: Step })[];
  updated_at: string | null;
  /** 서버가 매긴 저장 번호(0 = 저장 없음). 다음 저장 때 expected_revision으로 보낸다. */
  revision: number;
}
