import pytest

from core.optimize.milp import MilpParams, solve_milp, solve_milp_diagnostic
from tests.phase0.factories import one_project_fixture


def test_diagnostic_solution_preserves_raw_variables_and_public_plan():
    graph, skill, synergy = one_project_fixture()

    raw = solve_milp_diagnostic(
        graph, skill, synergy, MilpParams(time_limit=30, pair_keep_ratio=0.0)
    )

    assert raw.status == "Optimal"
    assert raw.plan.entries
    assert raw.objective == pytest.approx(raw.plan.objective)
    assert set(raw.a) == {(0, 0), (1, 0)}
    assert set(raw.z) == {(0, 0), (1, 0)}
    assert raw.variable_count > 0
    assert raw.constraint_count > 0


def test_public_solver_keeps_returning_only_the_plan():
    graph, skill, synergy = one_project_fixture()

    plan = solve_milp(
        graph, skill, synergy, MilpParams(time_limit=30, pair_keep_ratio=0.0)
    )

    assert plan.label == "A"
    assert {entry.person_id for entry in plan.entries} == {"p0"}
