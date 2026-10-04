from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.settings import PlacementSettings
from core.optimize.milp import MilpParams


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
    # 이 meta가 나온 활성 데이터셋(K9). 화면은 계산 요청에 이 값을 실어 보내고, 서버는
    # 그사이 데이터셋이 바뀌었으면 409로 거부한다(옛 이름·명단과 새 결과가 섞이지 않게).
    dataset_version: str


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


class MilpParamsIn(BaseModel):
    """/api/optimize·/api/whatif의 milp_params 요청 계약(K8).

    예전에는 형식 없는 dict를 MilpParams(**dict)로 넘겨, 오타 키가 조용히 무시되고
    범위 검사가 없었다(min_alloc=-1도 통과). 모든 필드는 선택이며 빠진 것은
    MilpParams 기본값이다(기본값 변경은 C6 소관). 범위는 관리자 설정 화면보다 넓다 --
    실험·테스트가 쓰는 값(작은 time_limit 등)을 막지 않되 계산이 무의미해지는 값만 거른다."""
    model_config = ConfigDict(extra="forbid")

    lam: float | None = Field(default=None, ge=0.0, le=10.0)
    mu: float | None = Field(default=None, ge=0.0, le=10.0)
    min_alloc: float | None = Field(default=None, gt=0.0, le=1.0)
    clique_threshold_months: int | None = Field(default=None, ge=1, le=120)
    pair_keep_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    slack_penalty: float | None = Field(default=None, ge=0.0)
    time_limit: int | None = Field(default=None, ge=1, le=3600)
    gap: float | None = Field(default=None, ge=0.0, le=1.0)
    max_pairs: int | None = Field(default=None, ge=1)

    def to_milp_params(self) -> MilpParams:
        return MilpParams(**self.model_dump(exclude_none=True))


class SettingsUpdate(BaseModel):
    """PUT /api/settings 본문. based_on은 화면이 읽은 설정의 updated_at(저장한 적
    없으면 null)이다. 그사이 다른 사람이 저장했으면 409로 거부한다 -- 6개 필드를
    통째로 덮어쓰므로, 확인 없이 받으면 남의 변경이 조용히 사라진다."""
    model_config = ConfigDict(extra="forbid")
    settings: PlacementSettings
    based_on: str | None


class SettingsBody(BaseModel):
    """GET/PUT /api/settings 응답. bounds는 PlacementSettings 필드 제약에서 만든다."""
    settings: dict
    defaults: dict
    bounds: dict[str, dict[str, float]]
    updated_at: str | None
    load_error: str | None


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
    # 이 플랜을 계산한 배치 설정(K8). PDF에 "계산 기준"으로 표시한다. 없으면
    # (설정을 못 불러와 모델 기본값으로 계산했거나 구버전 클라이언트) 표시하지 않는다.
    milp_params: PlacementSettings | None = None
    # 명단을 계산한 데이터셋(K9). PDF 페이지는 서버 meta로 이름을 붙이므로, 다르면 거부한다.
    dataset_version: str | None = None

    @model_validator(mode="after")
    def _basis_must_be_complete(self) -> "ReportRequest":
        """일부 필드만 오면 나머지가 설정 기본값(30% 등)으로 채워져, 실제 계산과
        다른 기준이 PDF에 찍힌다 -- 전체를 요구한다."""
        if self.milp_params is not None:
            missing = set(PlacementSettings.model_fields) - self.milp_params.model_fields_set
            if missing:
                raise ValueError(f"milp_params에 빠진 필드: {sorted(missing)}")
        return self
