from enum import StrEnum
from pydantic import BaseModel, Field, field_validator, model_validator

HORIZON_MONTHS = 6

class Grade(StrEnum):
    SPECIAL = "특급"; SENIOR = "고급"; MID = "중급"; JUNIOR = "초급"

class Sector(StrEnum):
    INTERNAL = "대내"; FINANCE = "대외금융"; PUBLIC = "대외공공"

class ProjectPhase(StrEnum):
    EXECUTION = "실행"; PROPOSAL = "제안"

class Person(BaseModel):
    id: str; name: str; grade: Grade
    monthly_rate: int = Field(gt=0)
    skills: dict[str, int]
    availability: list[float]

    @field_validator("skills")
    @classmethod
    def _levels(cls, v):
        if any(not 1 <= lv <= 5 for lv in v.values()):
            raise ValueError("skill level must be 1..5")
        return v

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

class Dataset(BaseModel):
    people: list[Person]
    projects: list[Project]
    coworks: list[CoworkRecord]
    reviews: list[PeerReview]
