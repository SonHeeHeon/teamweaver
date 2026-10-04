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


class ObjectiveBreakdownOut(BaseModel):
    skill: float
    synergy: float
    overfamiliarity: float
    unfilled: float
    total: float


class ViolationOut(BaseModel):
    code: str
    location: str
    actual: float
    limit: float
    message: str


class ShortfallOut(BaseModel):
    project_id: str
    grade: str
    missing: int


class WhatifResponse(BaseModel):
    """objective_delta는 교체 전후 배치를 현행 MILP 전체 목적(4항)으로 재평가한 차이다.
    재최적화 결과가 아니라 검토용 참고값이다. new_violations/new_shortfalls는 교체로
    새로 생긴 것만 담고, feasible은 교체 후 배치에 제약 위반이 하나도 없는지를 뜻한다."""
    objective_delta: float
    before: ObjectiveBreakdownOut
    after: ObjectiveBreakdownOut
    new_violations: list[ViolationOut]
    new_shortfalls: list[ShortfallOut]
    feasible: bool
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
    swap_violations: list[str] = []
