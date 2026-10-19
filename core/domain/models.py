from enum import StrEnum
from pydantic import BaseModel, Field, field_validator, model_validator

HORIZON_MONTHS = 6                    # 기본 계획 기간(예전 고정값). 기존 묶음·fixture는 모두 6개월
# 데이터마다 고를 수 있는 계획 기간(2026-10-19 사용자 지시 "계획기간 1년 확장"). 한 데이터 안에서는 모두 같은 길이 --
# Dataset이 확인한다. 계산 모델은 달 수를 len(person.availability)·project.months에서 읽는다.
HORIZON_CHOICES = (6, 12)
MAX_HORIZON_MONTHS = max(HORIZON_CHOICES)

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
    # 선택(2026-10-19 사용자 결정 "동시 수행은 사람 단위로"): 전일 투입(allocation_mode="full")에서 같은 달에 맡을 수 있는 사업 수.
    # 1 = 동시 수행 불가(기본), 2~3 = 부서장이 사유와 함께 허용(원천 manifest `concurrency`). 예전 투입률 방식은 이 값을 보지 않는다.
    max_concurrent: int = Field(default=1, ge=1, le=3)

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
        if len(v) not in HORIZON_CHOICES or any(not 0.0 <= a <= 1.0 for a in v):
            raise ValueError(f"availability must be {' or '.join(map(str, HORIZON_CHOICES))} values in [0,1]")
        return v

class SkillRequirement(BaseModel):
    skill: str
    min_level: int = Field(ge=1, le=5)
    headcount: int = Field(ge=1)

class Project(BaseModel):
    id: str; name: str; sector: Sector; phase: ProjectPhase
    start_month: int = Field(ge=0, lt=MAX_HORIZON_MONTHS)   # 데이터 기간 안인지는 Dataset이 본다
    end_month: int = Field(ge=0, lt=MAX_HORIZON_MONTHS)
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

    @model_validator(mode="after")
    def _one_horizon(self):
        """모든 사람의 가용률이 같은 길이(= 계획 기간)이고 사업이 그 안에 있어야 한다. 기간은 저장 필드가 아니라
        가용률 길이에서 읽는다 -- 직렬화 모양이 그대로라 기존 데이터 버전·캐시 키가 바뀌지 않는다."""
        lengths = {len(p.availability) for p in self.people}
        if len(lengths) > 1:
            raise ValueError(f"people must share one planning horizon, got availability lengths {sorted(lengths)}")
        n = self.horizon_months
        late = [j.id for j in self.projects if j.end_month >= n]
        if late:
            raise ValueError(f"projects end after the {n}-month horizon: {late[:5]}")
        return self

    @property
    def horizon_months(self) -> int:
        return len(self.people[0].availability) if self.people else HORIZON_MONTHS
