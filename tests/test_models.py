import pytest
from pydantic import ValidationError
from core.domain.models import (
    HORIZON_MONTHS, Grade, Sector, ProjectPhase, Person, Project,
    SkillRequirement, ReviewSection, PeerReview, Dataset,
)

def _person(**over):
    base = dict(id="p1", name="김철수", grade=Grade.SENIOR, monthly_rate=1300,
                skills={"Java": 4, "금융도메인": 3}, availability=[1.0] * HORIZON_MONTHS)
    base.update(over)
    return Person(**base)

def test_person_valid():
    p = _person()
    assert p.grade == Grade.SENIOR and p.skills["Java"] == 4

def test_person_rejects_bad_level_and_availability():
    with pytest.raises(ValidationError):
        _person(skills={"Java": 6})
    with pytest.raises(ValidationError):
        _person(availability=[1.5] * HORIZON_MONTHS)
    with pytest.raises(ValidationError):
        _person(availability=[1.0] * 3)  # 길이 != HORIZON_MONTHS

def test_project_months():
    pj = Project(id="j1", name="그룹사 ERP", sector=Sector.INTERNAL,
                 phase=ProjectPhase.EXECUTION, start_month=1, end_month=4,
                 grade_headcount={Grade.SENIOR: 2}, monthly_budget=5000,
                 requirements=[SkillRequirement(skill="Java", min_level=3, headcount=2)])
    assert pj.months == [1, 2, 3, 4]

def test_review_section_item_count_bounds():
    with pytest.raises(ValidationError):
        ReviewSection(items=[], text="x")
    with pytest.raises(ValidationError):
        ReviewSection(items=["a", "b", "c", "d", "e", "f"], text="x")
