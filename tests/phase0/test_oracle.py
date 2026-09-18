import pytest

from core.optimize.milp import MilpParams, solve_milp_diagnostic
from experiments.phase0.oracle import solve_tiny_oracle
from tests.phase0.factories import (
    all_terms_fixture,
    budget_shortfall_fixture,
    generated_tiny_fixture,
    one_project_fixture,
)


def test_oracle_selects_the_literal_best_person():
    graph, skill, synergy = one_project_fixture()

    result = solve_tiny_oracle(
        graph, skill, synergy, MilpParams(pair_keep_ratio=0.0)
    )

    assert result.objective == pytest.approx(0.9)
    assert result.z == {(0, 0): 1, (1, 0): 0}
    assert result.a[(0, 0)] == pytest.approx(1.0)


def test_oracle_matches_cbc_on_budget_shortfall_case():
    graph, skill, synergy = budget_shortfall_fixture()
    params = MilpParams(pair_keep_ratio=0.0, time_limit=30)

    oracle = solve_tiny_oracle(graph, skill, synergy, params)
    cbc = solve_milp_diagnostic(graph, skill, synergy, params)

    assert cbc.objective == pytest.approx(oracle.objective, abs=1e-6)
    assert oracle.objective == pytest.approx(-99.685, abs=1e-6)


def test_oracle_matches_cbc_when_all_objective_terms_are_active():
    graph, skill, synergy, params, _ = all_terms_fixture()

    oracle = solve_tiny_oracle(graph, skill, synergy, params)
    cbc = solve_milp_diagnostic(graph, skill, synergy, params)

    assert oracle.objective == pytest.approx(-98.38, abs=1e-6)
    assert cbc.objective == pytest.approx(oracle.objective, abs=1e-6)


def test_oracle_rejects_more_than_twelve_binary_decisions():
    graph, skill, synergy = one_project_fixture()
    graph.projects = graph.projects * 7

    with pytest.raises(ValueError, match="at most 12"):
        solve_tiny_oracle(graph, skill.repeat(7, axis=1), synergy, MilpParams())


@pytest.mark.parametrize("seed", [7, 11, 19])
def test_oracle_matches_cbc_across_generated_tiny_cases(seed):
    graph, skill, synergy = generated_tiny_fixture(seed)
    params = MilpParams(pair_keep_ratio=1.0, time_limit=30)

    oracle = solve_tiny_oracle(graph, skill, synergy, params)
    cbc = solve_milp_diagnostic(graph, skill, synergy, params)

    assert cbc.objective == pytest.approx(oracle.objective, abs=1e-6)
