from enum import StrEnum
from pydantic import BaseModel, Field, field_validator, model_validator

HORIZON_MONTHS = 6

class Grade(StrEnum):
    SPECIAL = "특급"; SENIOR = "고급"; MID = "중급"; JUNIOR = "초급"

class Sector(StrEnum):
    INTERNAL = "대내"; FINANCE = "대외금융"; PUBLIC = "대외공공"

class ProjectPhase(StrEnum):
    EXECUTION = "실행"; PROPOSAL = "제안"

class SkillCredit(BaseModel):
    """하위 기술 경력에서 인정된 경력(2026-10-11 사용자 결정 "구분해서 표시"). 그 기술의 경력을 인정분이 정했을 때만 있다
    (인정 개월 > 본인 개월). 예: Java {source: "Spring", months: 12, depth: 1, own_months: 0} → "Java 12개월(Spring 경력에서 인정)"."""
    source: str                                   # 인정 출처: 하위 기술의 대표 이름
    months: int = Field(gt=0)                     # 인정된 개월(레벨 계산에 쓴 값) = floor(출처 개월 × 계수^깊이)
    depth: int = Field(ge=1)                      # 출처에서 이 기술까지의 상하위 거리
    own_months: int = Field(default=0, ge=0)      # 본인 행의 경력(인정분보다 짧다, 없으면 0)


class Person(BaseModel):
    id: str; name: str; grade: Grade
    monthly_rate: int = Field(gt=0)
    skills: dict[str, int]
    availability: list[float]
    # 선택(2026-10-11): 레벨을 하위 기술 인정분이 정한 기술만. 비어 있으면 모든 기술이 본인 경력(예전 동작 그대로)
    skill_credit: dict[str, SkillCredit] = Field(default_factory=dict)

    @field_validator("skills")
    @classmethod
    def _levels(cls, v):
        if any(not 1 <= lv <= 5 for lv in v.values()):
            raise ValueError("skill level must be 1..5")
        return v

    @model_validator(mode="after")
    def _credit_keys(self):
        missing = sorted(set(self.skill_credit) - set(self.skills))
        if missing:
            raise ValueError(f"skill_credit의 기술이 skills에 없다: {missing}")
        return self

    @field_validator("availability")
    @classmethod
    def _avail(cls, v):
        if len(v) != HORIZON_MONTHS or any(not 0.0 <= a <= 1.0 for a in v):
            raise ValueError(f"availability must be {HORIZON_MONTHS} values in [0,1]")
        return v

class SkillRequirement(BaseModel):
    skill: str
    min_level: int = Field(ge=1, le=5)
    headcount: int = Field(ge=1)

class Project(BaseModel):
    id: str; name: str; sector: Sector; phase: ProjectPhase
    start_month: int = Field(ge=0, lt=HORIZON_MONTHS)
    end_month: int = Field(ge=0, lt=HORIZON_MONTHS)
    grade_headcount: dict[Grade, int]
    requirements: list[SkillRequirement]
    monthly_budget: int = Field(gt=0)

    @model_validator(mode="after")
    def _month_order(self):
        if self.end_month < self.start_month:
            raise ValueError("end_month must be >= start_month")
        return self

    @property
    def months(self) -> list[int]:
        return list(range(self.start_month, self.end_month + 1))

class ReviewSection(BaseModel):
    items: list[str] = Field(min_length=1, max_length=5)
    text: str

class PeerReview(BaseModel):
    reviewer_id: str; reviewee_id: str
    positive: ReviewSection; negative: ReviewSection

class ParsedReview(BaseModel):
    reviewer_id: str; reviewee_id: str
    text_polarity: float = Field(ge=-1.0, le=1.0)
    evidence: list[str] = []

class CoworkRecord(BaseModel):
    a_id: str; b_id: str
    co_months: int = Field(ge=1)
    project_count: int = Field(ge=1)
    # 함께 일한 각 달이 계획 시작 몇 달 전인지(1 = 계획 직전 달). 있으면 "최근 N개월 중 M개월 이상"으로 익숙한 쌍을
    # 가린다(2026-10-06 사용자 결정: 최근 3년 중 12개월, `rehearsal/results/rule-compare.html`). 원천이 합계만
    # 주면(예전 fixture·datagen) None -- 그때는 전체 개월로 판단한다.
    months_ago: list[int] | None = None

    @model_validator(mode="after")
    def _months_match(self):
        if self.months_ago is not None and (len(self.months_ago) != self.co_months
                                            or len(set(self.months_ago)) != self.co_months
                                            or any(m < 1 for m in self.months_ago)):
            raise ValueError("months_ago must list co_months distinct months, each >= 1")
        return self

class CurrentAssignment(BaseModel):
    """Someone already on a planned project when the plan is made (continuity). locked = must be kept."""
    person_id: str; project_id: str
    alloc: float = Field(ge=0.0, le=1.0)
    locked: bool = False


class Dataset(BaseModel):
    people: list[Person]
    projects: list[Project]
    coworks: list[CoworkRecord]
    reviews: list[PeerReview]
    current: list[CurrentAssignment] = []       # optional current roster; empty = plan every seat from scratch
