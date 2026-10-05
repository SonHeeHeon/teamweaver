"""C6: 한 사람이 같은 달에 맡는 프로젝트 수 상한(사용자 답변: 최대 3개, 보통 1개).

같은 달에 겹치는 네 프로젝트가 모두 같은 등급 1명을 원하고, 그 등급은 한 사람뿐이다. 예산·가용률은
넉넉하다(가용률 1.0 = 0.25씩 네 곳도 가능). 상한이 없으면 네 곳 모두에 들어간다."""
import numpy as np
import pulp
import pytest

from core.domain.models import Grade, Project, ProjectPhase, Sector, SkillRequirement
from core.optimize.greedy import solve_greedy
from core.optimize.milp import MilpParams, solve_milp, solve_milp_diagnostic
from core.optimize.validation import validate_raw_solution
from experiments.phase1.solvers import _PulpFactory, _build_model
from experiments.phase1.types import BenchmarkProblem
from tests.phase0.factories import _graph, _person


def _four_overlapping_projects():
    people = [_person("p0", Grade.MID)]
    projects = [Project(id=f"j{k}", name=f"동시 {k}", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                        start_month=0, end_month=1, grade_headcount={Grade.MID: 1},
                        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                        monthly_budget=10_000) for k in range(4)]
    return _graph(people, projects), np.full((1, 4), 0.9), np.zeros((1, 1))


def _max_concurrent(graph, entries):
    months = {p.id: p.months for p in graph.projects}
    load = {}
    for e in entries:
        for m in months[e.project_id]:
            load[(e.person_id, m)] = load.get((e.person_id, m), 0) + 1
    return max(load.values(), default=0)


@pytest.mark.parametrize("limit", [1, 3])
def test_milp_respects_max_concurrent_projects(limit):
    graph, S, C = _four_overlapping_projects()
    plan = solve_milp(graph, S, C, MilpParams(pair_keep_ratio=0.0, max_concurrent_projects=limit))
    assert _max_concurrent(graph, plan.entries) == limit
    assert len(plan.unfilled) == 4 - limit


def test_default_limit_is_three():
    assert MilpParams().max_concurrent_projects == 3


def test_validator_flags_too_many_concurrent_projects():
    graph, S, C = _four_overlapping_projects()
    loose = MilpParams(pair_keep_ratio=0.0, max_concurrent_projects=4)
    raw = solve_milp_diagnostic(graph, S, C, loose)                 # 네 곳 모두 배치된 해
    assert _max_concurrent(graph, raw.plan.entries) == 4
    report = validate_raw_solution(graph, S, C, loose.model_copy(update={"max_concurrent_projects": 3}), raw)
    assert "concurrent_projects" in {i.code for i in report.issues}


def test_greedy_respects_max_concurrent_projects():
    graph, S, _ = _four_overlapping_projects()
    plan = solve_greedy(graph, S, max_concurrent_projects=3)
    assert _max_concurrent(graph, plan.entries) <= 3


def test_benchmark_formulation_has_the_same_limit():
    """MILP 정식 두 벌(서비스·벤치)은 같이 움직여야 한다(CLAUDE.md 함정)."""
    graph, S, C = _four_overlapping_projects()
    built = _build_model(BenchmarkProblem(graph, S, C, MilpParams(pair_keep_ratio=0.0, max_concurrent_projects=3)),
                         _PulpFactory())
    built.problem.solve(pulp.PULP_CBC_CMD(msg=0))
    assert sum(round(v.value()) for v in built.z.values()) == 3


def test_rows_are_skipped_only_when_availability_already_prevents_the_violation():
    """상한+1곳에 최소 투입률로 못 들어가는 사람은 가용률 제약이 막는다 -- 행을 넣지 않아도 같은 해(리뷰 S3).
    가용률 1.0·최소 0.3이면 4곳 = 1.2 > 1.0이라 K=3 행은 묶이지 않는다."""
    graph, S, C = _four_overlapping_projects()
    skipped = solve_milp_diagnostic(graph, S, C, MilpParams(pair_keep_ratio=0.0, min_alloc=0.3, max_concurrent_projects=3))
    binding = solve_milp_diagnostic(graph, S, C, MilpParams(pair_keep_ratio=0.0, min_alloc=0.2, max_concurrent_projects=3))
    assert _max_concurrent(graph, skipped.plan.entries) == 3
    # 같은 규모인데 min_alloc 0.3이면 상한 행(2개월)이 빠지고, 0.2면(4×0.2 ≤ 1.0) 들어간다.
    assert binding.constraint_count - skipped.constraint_count == 2


def test_phase0_oracle_knows_the_limit():
    """독립 오라클(전수 탐색)도 같은 규칙을 지킨다 -- 아니면 MILP와 목적값이 어긋난다(리뷰 S5)."""
    from experiments.phase0.oracle import solve_tiny_oracle
    graph, S, C = _four_overlapping_projects()
    params = MilpParams(pair_keep_ratio=0.0, max_concurrent_projects=2, gap=0.0)
    oracle = solve_tiny_oracle(graph, S, C, params)
    milp = solve_milp_diagnostic(graph, S, C, params)
    assert abs(oracle.objective - milp.objective) < 1e-6
