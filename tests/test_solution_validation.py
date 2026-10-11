from dataclasses import replace

import pytest

from core.optimize.milp import MilpParams, solve_milp_diagnostic
from core.optimize.validation import validate_raw_solution
from tests.phase0.factories import all_terms_fixture, budget_shortfall_fixture


def test_recomputed_objective_uses_all_four_terms():
    graph, skill, synergy, params, raw = all_terms_fixture()

    result = validate_raw_solution(graph, skill, synergy, params, raw)

    assert result.valid
    assert result.objective.skill == pytest.approx(1.7)
    assert result.objective.synergy == pytest.approx(0.12)
    assert result.objective.overfamiliarity == pytest.approx(-0.2)
    assert result.objective.unfilled == pytest.approx(-100.0)
    assert result.objective.total == pytest.approx(-98.38)
    assert result.solver_objective_error == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("field", "replacement", "expected_code"),
    [
        ("a", {(0, 0): 1.1, (1, 0): 1.0}, "unit_interval"),
        ("slack", {}, "missing_key"),
        ("y", {(0, 1, 0): 0.0}, "pair_product"),
    ],
)
def test_validator_detects_independently_broken_raw_values(field, replacement, expected_code):
    graph, skill, synergy, params, raw = all_terms_fixture()
    broken = replace(raw, **{field: replacement})

    result = validate_raw_solution(graph, skill, synergy, params, broken)

    assert not result.valid
    assert expected_code in {issue.code for issue in result.issues}


def test_validator_detects_budget_violation_from_domain_values():
    graph, skill, synergy, params, raw = all_terms_fixture()
    graph.projects[0].monthly_budget = 1_500

    result = validate_raw_solution(graph, skill, synergy, params, raw)

    assert "budget" in {issue.code for issue in result.issues}


def test_validator_rederives_reward_pair_scope_instead_of_trusting_diagnostics():
    graph, skill, synergy, params, raw = all_terms_fixture()
    missing_reward_scope = replace(raw, reward_pairs=())

    result = validate_raw_solution(graph, skill, synergy, params, missing_reward_scope)

    assert not result.valid
    assert "reward_pair_scope" in {issue.code for issue in result.issues}


def test_real_cbc_shortfall_solution_passes_independent_validation():
    graph, skill, synergy = budget_shortfall_fixture()
    params = MilpParams(time_limit=30, pair_keep_ratio=0.0)
    raw = solve_milp_diagnostic(graph, skill, synergy, params)

    result = validate_raw_solution(graph, skill, synergy, params, raw)

    assert result.valid, result.issues



def test_validator_checks_the_returned_plan_against_budget_and_availability():
    # Codex 사후 리뷰 MUST(2026-10-11): 원해가 예산 안이어도 반환 명단(표시 규칙으로 올린 투입률)이 넘으면 거절한다
    from core.optimize.types import AssignEntry
    graph, skill, synergy, params, raw = all_terms_fixture()
    entries = [AssignEntry(**{**e.model_dump(), "alloc": 1.5}) for e in raw.plan.entries]   # 반환 투입률만 부풀림
    broken = replace(raw, plan=raw.plan.model_copy(update={"entries": entries}))
    codes = {issue.code for issue in validate_raw_solution(graph, skill, synergy, params, broken).issues}
    assert "plan_availability" in codes
    assert validate_raw_solution(graph, skill, synergy, params, raw).valid           # 원래 명단은 그대로 통과


def test_validator_rejects_non_finite_returned_allocations():
    from core.optimize.types import AssignEntry
    graph, skill, synergy, params, raw = all_terms_fixture()
    first = raw.plan.entries[0]
    entries = [AssignEntry(**{**first.model_dump(), "monthly_alloc": {0: float("nan")}})] + list(raw.plan.entries[1:])
    broken = replace(raw, plan=raw.plan.model_copy(update={"entries": entries}))
    result = validate_raw_solution(graph, skill, synergy, params, broken)
    assert not result.valid and "plan_allocation_finite" in {i.code for i in result.issues}
