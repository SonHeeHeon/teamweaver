import time
from types import SimpleNamespace

import pytest

from core.optimize.milp import MilpParams, solve_milp_diagnostic
from experiments.phase0 import oracle as oracle_module
from experiments.phase0.oracle import OracleDeadlineExceeded, solve_tiny_oracle
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


def test_oracle_matches_cbc_when_partial_pair_pruning_keeps_one_of_three_pairs():
    graph, skill, synergy = generated_tiny_fixture(11)
    params = MilpParams(pair_keep_ratio=1 / 3, time_limit=30)

    oracle = solve_tiny_oracle(graph, skill, synergy, params)
    cbc = solve_milp_diagnostic(graph, skill, synergy, params)

    assert cbc.reward_pairs == ((1, 2),)
    assert cbc.objective == pytest.approx(oracle.objective, abs=1e-6)


def test_oracle_refuses_to_start_when_its_deadline_has_expired():
    graph, skill, synergy = one_project_fixture()

    with pytest.raises(TimeoutError, match="deadline"):
        solve_tiny_oracle(
            graph,
            skill,
            synergy,
            MilpParams(pair_keep_ratio=0.0),
            deadline=time.perf_counter(),
        )


def test_oracle_binds_each_linprog_call_to_its_remaining_deadline(monkeypatch):
    graph, skill, synergy = one_project_fixture()
    observed_options = []
    real_linprog = oracle_module.linprog

    def recording_linprog(*args, **kwargs):
        observed_options.append(kwargs["options"])
        return real_linprog(*args, **kwargs)

    monkeypatch.setattr(oracle_module, "linprog", recording_linprog)
    solve_tiny_oracle(
        graph,
        skill,
        synergy,
        MilpParams(pair_keep_ratio=0.0),
        deadline=time.perf_counter() + 5.0,
    )

    assert observed_options
    assert all(0 < options["time_limit"] < 5.0 for options in observed_options)


def test_oracle_raises_immediately_when_highs_hits_its_time_limit(monkeypatch):
    graph, skill, synergy = one_project_fixture()
    calls = []

    def timed_out_linprog(*_args, **_kwargs):
        calls.append(1)
        return SimpleNamespace(success=False, status=1, message="Time limit reached")

    monkeypatch.setattr(oracle_module, "linprog", timed_out_linprog)

    with pytest.raises(OracleDeadlineExceeded, match="time limit"):
        solve_tiny_oracle(
            graph,
            skill,
            synergy,
            MilpParams(pair_keep_ratio=0.0),
            deadline=time.perf_counter() + 5.0,
        )

    assert calls == [1]
