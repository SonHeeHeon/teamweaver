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
}

export interface AssignEntry {
  person_id: string;
  project_id: string;
  alloc: number;
}

/** POST /api/optimize 의 `event: plan` 프레임 payload */
export interface PlanEvent {
  label: string;
  entries: AssignEntry[];
  objective: number;
  unfilled: string[];
  fulfillment: number;
  optimization_ratio: number;
  index: number;
  cached: boolean;
}

export interface Briefing {
  rationale: string;
  risks: string[];
  alternatives: string[];
}

export interface WhatifResponse {
  objective_delta: number;
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
  optimization_ratio: number;
  unfilled: string[];
  briefing: Briefing | null;
  fallback_used: boolean;
  swap: Swap | null;
  objective_delta: number | null;
}
