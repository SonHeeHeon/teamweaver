import numpy as np

from core.domain.models import (
    Dataset,
    Grade,
    Person,
    Project,
    ProjectPhase,
    Sector,
    SkillRequirement,
)
from core.graph.memory_graph import MemoryGraph


def _person(person_id: str, grade: Grade, monthly_rate: int = 1_000) -> Person:
    return Person(
        id=person_id,
        name=person_id.upper(),
        grade=grade,
        monthly_rate=monthly_rate,
        skills={"Python": 3},
        availability=[1.0] * 6,
    )


def _graph(people: list[Person], projects: list[Project]) -> MemoryGraph:
    dataset = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    return MemoryGraph.build(dataset, parsed=[])


def one_project_fixture() -> tuple[MemoryGraph, np.ndarray, np.ndarray]:
    people = [_person("p0", Grade.MID), _person("p1", Grade.MID)]
    projects = [
        Project(
            id="j0",
            name="한 자리 프로젝트",
            sector=Sector.INTERNAL,
            phase=ProjectPhase.EXECUTION,
            start_month=0,
            end_month=0,
            grade_headcount={Grade.MID: 1},
            requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
            monthly_budget=5_000,
        )
    ]
    return _graph(people, projects), np.array([[0.9], [0.4]]), np.zeros((2, 2))


def budget_shortfall_fixture() -> tuple[MemoryGraph, np.ndarray, np.ndarray]:
    people = [_person("p0", Grade.SENIOR), _person("p1", Grade.SENIOR)]
    projects = [
        Project(
            id="j0",
            name="예산 부족 프로젝트",
            sector=Sector.INTERNAL,
            phase=ProjectPhase.EXECUTION,
            start_month=0,
            end_month=0,
            grade_headcount={Grade.SENIOR: 2},
            requirements=[SkillRequirement(skill="Python", min_level=1, headcount=2)],
            monthly_budget=350,
        )
    ]
    return _graph(people, projects), np.array([[0.9], [0.7]]), np.zeros((2, 2))
