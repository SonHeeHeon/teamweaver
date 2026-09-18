import numpy as np

from core.domain.models import (
    CoworkRecord,
    Dataset,
    Grade,
    Person,
    Project,
    ProjectPhase,
    Sector,
    SkillRequirement,
)
from core.graph.memory_graph import MemoryGraph
from core.optimize.audit_types import RawMilpSolution
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry, PlanAssignment


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


def all_terms_fixture() -> tuple[
    MemoryGraph, np.ndarray, np.ndarray, MilpParams, RawMilpSolution
]:
    people = [_person("p0", Grade.MID), _person("p1", Grade.MID)]
    projects = [
        Project(
            id="j0",
            name="목적함수 전체 항 프로젝트",
            sector=Sector.INTERNAL,
            phase=ProjectPhase.EXECUTION,
            start_month=0,
            end_month=0,
            grade_headcount={Grade.MID: 3},
            requirements=[SkillRequirement(skill="Python", min_level=1, headcount=2)],
            monthly_budget=5_000,
        )
    ]
    dataset = Dataset(
        people=people,
        projects=projects,
        coworks=[CoworkRecord(a_id="p0", b_id="p1", co_months=10, project_count=2)],
        reviews=[],
    )
    graph = MemoryGraph.build(dataset, parsed=[])
    skill = np.array([[0.9], [0.8]])
    synergy = np.array([[0.0, 0.4], [0.4, 0.0]])
    params = MilpParams(
        lam=0.3,
        mu=0.2,
        pair_keep_ratio=1.0,
        clique_threshold_months=6,
        slack_penalty=100.0,
    )
    objective = -98.38
    plan = PlanAssignment(
        entries=[
            AssignEntry(person_id="p0", project_id="j0", alloc=1.0),
            AssignEntry(person_id="p1", project_id="j0", alloc=1.0),
        ],
        objective=objective,
        unfilled=["j0:중급:1명 미충원"],
        violations=[],
        label="A",
    )
    raw = RawMilpSolution(
        plan=plan,
        status="Optimal",
        objective=objective,
        z={(0, 0): 1.0, (1, 0): 1.0},
        a={(0, 0): 1.0, (1, 0): 1.0},
        y={(0, 1, 0): 1.0},
        slack={(0, Grade.MID): 1.0},
        reward_pairs=((0, 1),),
        penalty_pairs=((0, 1),),
        variable_count=6,
        constraint_count=9,
    )
    return graph, skill, synergy, params, raw
