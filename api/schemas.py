from pydantic import BaseModel, Field


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


class EntryIn(BaseModel):
    """api.routes.whatif과 api.routes.report이 공유하는 배치 항목 모델.
    여기 두는 이유: schemas.py -> routes 방향으로만 import가 흐르게 해서
    순환 import를 피한다(routes.whatif과 routes.report이 둘 다 이 모듈을
    가져다 쓴다)."""
    person_id: str
    project_id: str
    alloc: float = Field(ge=0.0, le=1.0)


class SwapIn(BaseModel):
    out_person_id: str
    in_person_id: str
    project_id: str


class ReportRequest(BaseModel):
    """확정 배치 + 그때의 지표·브리핑. 서버는 이걸 저장하지 않는다 --
    Playwright가 리포트 페이지에 주입할 뿐이다(stateless 유지).

    fallback_used/swap/objective_delta는 화면의 What-if 결과를 PDF까지
    그대로 실어 나르기 위한 필드다: 화면 배지("규칙 기반(LLM 미사용)")는
    PDF에 자동으로 따라오지 않으므로, PDF가 스스로 판단할 수 있게 원본
    출처 정보를 함께 보낸다."""
    plan_label: str
    entries: list[EntryIn]
    objective: float
    fulfillment: float
    optimization_ratio: float
    unfilled: list[str] = []
    briefing: BriefingOut | None = None
    fallback_used: bool = False
    swap: SwapIn | None = None
    objective_delta: float | None = None
