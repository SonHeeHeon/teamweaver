"""초기해(전일 투입, 2026-10-19 설계 3.3): 탐욕 해를 HiGHS에 먼저 넣어 시간 한도에 걸려도 그보다 나빠지지 않게."""
import numpy as np
import pytest

from core.domain.models import Grade, Project, ProjectPhase, Sector, SkillRequirement
from core.evaluate.plan_eval import evaluate_plan
from core.optimize.greedy import warm_start_full
from core.optimize.milp import MilpParams, solve_milp_assessment, solve_milp_diagnostic
from core.optimize.types import AssignEntry
from tests.phase0.factories import _graph, _person

FULL = MilpParams(pair_keep_ratio=0.0, allocation_mode="full", time_limit=5)


def _org(n_people: int = 24, n_projects: int = 8, seed: int = 3):
    rng = np.random.default_rng(seed)
    grades = [Grade.SENIOR, Grade.MID, Grade.MID, Grade.JUNIOR]
    people = [_person(f"p{k:02d}", grades[k % 4], monthly_rate=int(rng.integers(800, 1400))) for k in range(n_people)]
    people[5] = people[5].model_copy(update={"availability": [1.0, 1.0, 0.0, 0.0, 1.0, 1.0]})
    projects = []
    for k in range(n_projects):
        start = int(rng.integers(0, 4))
        projects.append(Project(id=f"j{k}", name=f"사업 {k}", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                                start_month=start, end_month=min(5, start + int(rng.integers(1, 3))),
                                grade_headcount={Grade.SENIOR: 1, Grade.MID: 2},
                                requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                                monthly_budget=int(rng.integers(2600, 4200))))
    S = rng.uniform(0.1, 1.0, size=(n_people, n_projects))
    return _graph(people, projects), S, np.zeros((n_people, n_people))


def _empty(graph, entries) -> int:
    seats = sum(sum(p.grade_headcount.values()) for p in graph.projects)
    return seats - len(entries)


def test_greedy_start_is_a_valid_full_time_plan():
    graph, S, C = _org()
    start = warm_start_full(graph, S)
    assert start and all(e.alloc == 1.0 for e in start)
    assert not evaluate_plan(graph, S, C, FULL, start).violations
    assert all(e.person_id != "p05" or graph.projects[graph.project_index[e.project_id]].end_month < 2
               or graph.projects[graph.project_index[e.project_id]].start_month > 3 for e in start)


@pytest.mark.parametrize("seeds", [1, 2])
def test_solution_is_never_emptier_than_the_start(seeds):
    graph, S, C = _org()
    start = warm_start_full(graph, S)
    a = solve_milp_assessment(graph, S, C, FULL.model_copy(update={"solver_seeds": seeds, "time_limit": 2}))
    assert a.accepted is not None
    ev = a.accepted.evidence
    assert ev.options["warm_start_unfilled"] == _empty(graph, start)
    assert len(a.accepted.plan.entries) >= len(start)


def test_an_impossible_start_is_ignored():
    graph, S, C = _org()
    over = [AssignEntry(person_id=p.id, project_id="j0", alloc=1.0) for p in graph.people[:8]]     # 정원 초과
    raw = solve_milp_diagnostic(graph, S, C, FULL, start_entries=over)
    assert not evaluate_plan(graph, S, C, FULL, raw.plan.entries).violations


def test_old_modes_get_no_start():
    graph, S, C = _org()
    a = solve_milp_assessment(graph, S, C, MilpParams(pair_keep_ratio=0.0, time_limit=5))
    assert "warm_start_unfilled" not in a.accepted.evidence.options
