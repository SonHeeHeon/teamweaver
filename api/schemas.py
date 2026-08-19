from pydantic import BaseModel


class PersonOut(BaseModel):
    id: str
    name: str
    grade: str
    skills: dict[str, int]


class ProjectOut(BaseModel):
    id: str
    name: str
    sector: str
    phase: str
    start_month: int
    end_month: int
    grade_headcount: dict[str, int]
    monthly_budget: int


class CoworkOut(BaseModel):
    a_id: str
    b_id: str
    co_months: int
    project_count: int


class MetaResponse(BaseModel):
    people: list[PersonOut]
    projects: list[ProjectOut]
    skills: list[str]
    review_items: list[str]
    coworks: list[CoworkOut]


class BriefingOut(BaseModel):
    rationale: str
    risks: list[str]
    alternatives: list[str]


class WhatifResponse(BaseModel):
    objective_delta: float
    briefing: BriefingOut
    fallback_used: bool


class ReportRequest(BaseModel):
    """확정 배치 + 그때의 지표·브리핑. 서버는 이걸 저장하지 않는다 --
    Playwright가 리포트 페이지에 주입할 뿐이다(stateless 유지)."""
    plan_label: str
    entries: list[dict]
    objective: float
    fulfillment: float
    optimization_ratio: float
    unfilled: list[str] = []
    briefing: BriefingOut | None = None
