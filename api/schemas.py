from typing import Annotated, Literal, Union

from pydantic import (BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator, model_serializer,
                      model_validator)

from api.settings import PlacementSettings
from core.optimize.milp import MilpParams



# PDF 재계산 상한(Codex 2라운드): 교체마다 명단 전체를 두 번 평가하므로 무제한이면 요청 하나가
# 서버를 오래 붙잡는다. 화면의 정상 사용(교체 수십 건)보다 넉넉하다.
MAX_APPLIED_SWAPS = 50
MAX_PLAN_ENTRIES = 5000
# 브리핑 근거(K5) 상한. 응답에 싣기 전에 서버가 이 값으로 자른다(api/briefing_evidence.clamp_briefing).
MAX_EVIDENCE = 50
MAX_EVIDENCE_ID_CHARS = 200
MAX_EVIDENCE_TEXT_CHARS = 2000

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


class EvidenceOut(BaseModel):
    """브리핑 근거 하나(K5). kind: quote = 원문 그대로(검증됨), summary = 파서가 바꿔 쓴 문장,
    label = 실데이터라 원문 비공개(리뷰 항목 라벨만)."""
    source_id: str = Field(max_length=MAX_EVIDENCE_ID_CHARS)
    reviewer_id: str = Field(max_length=MAX_EVIDENCE_ID_CHARS)
    kind: Literal["quote", "summary", "label"]
    text: str = Field(max_length=MAX_EVIDENCE_TEXT_CHARS)


class BriefingOut(BaseModel):
    rationale: str
    risks: list[str]
    alternatives: list[str]
    evidence: list[EvidenceOut] = Field(default=[], max_length=MAX_EVIDENCE)


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
    # 규칙 기반으로 쓴 이유: no_client(키 없음) · external_blocked(실데이터를 외부로 보내지 않음) · llm_error
    fallback_reason: str | None = None


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
    # 익숙한 쌍을 셀 최근 기간(개월). 빠지면 모델 기본값(None = 전체 기간). 서비스 기본은 관리자 설정(36개월).
    clique_window_months: int | None = Field(default=None, ge=1, le=120)
    pair_keep_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    slack_penalty: float | None = Field(default=None, ge=0.0)
    time_limit: int | None = Field(default=None, ge=1, le=3600)
    gap: float | None = Field(default=None, ge=0.0, le=1.0)
    max_pairs: int | None = Field(default=None, ge=1)
    max_concurrent_projects: int | None = Field(default=None, ge=1, le=6)
    allocation_mode: Literal["fixed", "monthly"] | None = None
    # 관리자 설정의 "자동(인원 기준)" 표시. 계산에는 쓰지 않는다(화면이 실제 시간을 time_limit으로 보낸다).
    # 받기만 하고 계산에는 쓰지 않는다. 자동 시간은 GET /api/settings의 effective_time_limit을
    # time_limit으로 보내야 적용된다 -- 이 칸만 true로 보내면 time_limit 기본값(120초)이 쓰인다.
    time_limit_auto: bool | None = None
    # 없앤 칸(리뷰 판정 방식 선택, 2026-10-06). 이전 화면이 보내도 거절하지 않고 무시한다.
    review_judge: str | None = None
    # 동시 탐색 수(관리자 설정, 서버 자원). 화면이 설정 전체를 보내므로 받되 계산에는 쓰지 않는다 -- 요청이 서버의
    # 코어 사용량을 정하지 못하게(서버가 저장한 설정만 TEAMWEAVER_SOLVER_SEEDS로 반영한다).
    solver_seeds: int | None = Field(default=None, ge=1, le=8)

    def to_milp_params(self) -> MilpParams:
        return MilpParams(**self.model_dump(exclude_none=True,
                                            exclude={"time_limit_auto", "review_judge", "solver_seeds"}))


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
    recommended_time: dict | None = None
    recommended_time_monthly: dict | None = None
    effective_time_limit: int | None = None
    # 지금 계산이 쓰는 동시 탐색 수(설정, 없으면 서버 환경 변수, 없으면 1)
    effective_solver_seeds: int | None = None


class PlanEvaluationOut(BaseModel):
    objective: ObjectiveBreakdownOut
    violations: list[ViolationOut]
    shortfalls: list[ShortfallOut]


class ApplySwapResponse(BaseModel):
    """교체를 적용한 명단과, 그 명단 전체를 현행 MILP 목적·제약으로 다시 평가한 결과(K10).
    objective·fulfillment·optimization_ratio·unfilled는 플랜 카드·PDF 지표를 이 명단 기준으로
    갈아 끼우기 위한 값이다(솔버 결과가 아니라 재평가 값)."""
    entries: list["EntryIn"]
    evaluation: PlanEvaluationOut
    objective_delta: float
    feasible: bool
    objective: float
    fulfillment: float
    # 제약 위반이 있는 명단은 None(산정 불가) -- 상한이 제약을 지키는 배치에만 의미가 있다.
    optimization_ratio: float | None
    unfilled: list[str]
    warnings: list[str]           # 이 교체로 새로 생긴 위반·미충원 문장


class PlanEditIn(BaseModel):
    """PUT /api/plans/edits/{plan_token} 본문(K13). 원 플랜(서명 검증용)과 적용한 교체 순서."""
    model_config = ConfigDict(extra="forbid")
    plan_label: str
    base_entries: list["EntryIn"] = Field(max_length=MAX_PLAN_ENTRIES)
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: MilpParamsIn | None = None
    dataset_version: str
    swaps: list["StepIn"] = Field(default=[], max_length=MAX_APPLIED_SWAPS)
    # 화면이 마지막으로 본 서버 revision(저장분이 없으면 0). 다르면 409와 최신 상태.
    expected_revision: int = Field(ge=0)


class EntryIn(BaseModel):
    """api.routes.whatif과 api.routes.report이 공유하는 배치 항목 모델.
    여기 두는 이유: schemas.py -> routes 방향으로만 import가 흐르게 해서
    순환 import를 피한다(routes.whatif과 routes.report이 둘 다 이 모듈을
    가져다 쓴다)."""
    person_id: str
    project_id: str
    alloc: float = Field(ge=0.0, le=1.0)
    # 월별 투입률(키 = 프로젝트 진행 달 0~5). 없으면 모든 진행 달이 alloc(core/optimize/types.AssignEntry와 같은 뜻).
    monthly_alloc: dict[Annotated[int, Field(ge=0, le=5)], Annotated[float, Field(ge=0.0, le=1.0)]] | None = None

    @field_validator("monthly_alloc")
    @classmethod
    def _empty_is_none(cls, v):
        return v or None                         # {}는 "월별 값 없음"과 같다

    @model_serializer(mode="wrap")
    def _omit_empty_monthly(self, handler):
        data = handler(self)                     # 없으면 칸을 내보내지 않는다(서명·저장 바이트 유지)
        if isinstance(data, dict) and data.get("monthly_alloc") is None:
            data.pop("monthly_alloc", None)
        return data


class SwapIn(BaseModel):
    kind: Literal["swap"] = "swap"
    out_person_id: str
    in_person_id: str
    project_id: str

    @model_serializer(mode="wrap")
    def _omit_default_kind(self, handler):
        # 교체는 kind를 내보내지 않는다 -- 저장된 교체·응답·PDF 기록이 이전과 같은 모양(kind 없음 = 교체).
        data = handler(self)
        if isinstance(data, dict) and data.get("kind") == "swap":
            data.pop("kind", None)
        return data


class AllocChangeIn(BaseModel):
    """적용 단계: 한 사람의 한 프로젝트 투입률을 달별로 바꾼다(사람별 달별 조정, 2026-10-05).
    monthly_alloc은 그 프로젝트의 진행 달 전부(키 0~5). 모든 달이 같으면 일반 항목으로 정리된다."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["alloc"] = "alloc"
    person_id: str
    project_id: str
    monthly_alloc: dict[Annotated[int, Field(ge=0, le=5)], Annotated[float, Field(ge=0.0, le=1.0)]] = Field(
        min_length=1)


def _step_kind(v) -> str:
    return (v.get("kind", "swap") if isinstance(v, dict) else getattr(v, "kind", "swap")) or "swap"


# 적용 이력의 한 단계. kind가 없는 예전 기록은 교체로 읽는다(저장된 교체·PDF 요청 호환).
StepIn = Annotated[Union[Annotated[SwapIn, Tag("swap")], Annotated[AllocChangeIn, Tag("alloc")]],
                   Discriminator(_step_kind)]




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
    optimization_ratio: float | None      # 적용 후 명단에 위반이 있으면 None(산정 불가)
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
    # 원 플랜에 적용한 교체(K10, 적용 순서)와 그 출발점인 원 플랜 명단. 교체가 있으면 서버가
    # base_entries에서 순서대로 다시 적용해 명단·지표·교체별 Δ·경고·최종 위반을 계산하고,
    # 클라이언트가 보낸 entries·지표는 쓰지 않는다 -- PDF의 적용 이력을 조작할 수 없게
    # (Codex 지적). weights·milp_params는 그 재계산의 기준(계산 당시 스냅숏)이다.
    applied_swaps: list[StepIn] = Field(default=[], max_length=MAX_APPLIED_SWAPS)
    base_entries: list[EntryIn] | None = Field(default=None, max_length=MAX_PLAN_ENTRIES)
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    # /api/optimize가 원 플랜에 붙인 서명(api/plan_token). 있으면 서버가 검증한다: 맞으면
    # PDF에 "서버 계산 원 플랜 확인", 틀리면 422, 없으면 "클라이언트 제공·미검증"으로 표시.
    plan_token: str | None = None

    @model_validator(mode="after")
    def _basis_must_be_complete(self) -> "ReportRequest":
        """일부 필드만 오면 나머지가 설정 기본값(30% 등)으로 채워져, 실제 계산과
        다른 기준이 PDF에 찍힌다 -- 전체를 요구한다."""
        if self.applied_swaps and self.base_entries is None:
            raise ValueError("applied_swaps가 있으면 base_entries(원 플랜 명단)가 필요하다")
        if self.milp_params is not None:
            # time_limit_auto는 계산에 쓰지 않는 표시용 칸이라 요구하지 않는다(이전 화면 호환, 리뷰 S4).
            missing = (set(PlacementSettings.model_fields) - {"time_limit_auto"}
                       - self.milp_params.model_fields_set)
            if missing:
                raise ValueError(f"milp_params에 빠진 필드: {sorted(missing)}")
        return self
