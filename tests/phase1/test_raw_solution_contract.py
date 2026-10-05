from copy import deepcopy
from dataclasses import replace

import pytest

from core.domain.models import Grade
from core.optimize.milp import solve_milp_diagnostic
from core.optimize.validation import validate_raw_solution
from tests.phase0.factories import all_terms_fixture


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("z", {(0, 0): float("nan"), (1, 0): 1.0}, "nonfinite_value"),
        ("a", {(0, 0): None, (1, 0): 1.0}, "missing_value"),
        ("z", {(0, 0): 0.5, (1, 0): 1.0}, "binary_domain"),
        ("z", {(0, 0): 1.0}, "missing_key"),
        ("y", {(0, 1, 0): 1.0, (9, 9, 0): 0.0}, "unexpected_key"),
    ],
)
def test_raw_contract_rejects_untrustworthy_values(field, replacement, code):
    graph, skill, synergy, params, raw = all_terms_fixture()

    report = validate_raw_solution(
        graph, skill, synergy, params, replace(raw, **{field: replacement})
    )

    assert not report.valid
    assert code in {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    ("field", "key", "value", "code"),
    [
        ("z", (0, 0), 1.0 + 1e-6 / 2, "binary_domain"),
        ("a", (0, 0), -1e-6 / 2, "unit_interval"),
        ("y", (0, 1, 0), 1.0 + 1e-6 / 2, "unit_interval"),
        ("slack", (0, Grade.MID), -1e-6 / 2, "slack_nonnegative"),
    ],
)
def test_raw_contract_rejects_values_just_outside_their_domains(field, key, value, code):
    graph, skill, synergy, params, raw = all_terms_fixture()
    replacement = dict(getattr(raw, field))
    replacement[key] = value

    report = validate_raw_solution(
        graph, skill, synergy, params, replace(raw, **{field: replacement})
    )

    assert not report.valid
    assert code in {issue.code for issue in report.issues}


def test_cbc_diagnostics_include_evidence_without_an_invented_bound():
    graph, skill, synergy, params, raw = all_terms_fixture()

    assert raw.evidence.solver_name == "CBC"          # hand-built fixture value
    assert raw.evidence.best_bound is None

    # the service solver is HiGHS since 2026-10-05; CBC stays selectable for comparison runs
    assert solve_milp_diagnostic(graph, skill, synergy, params).evidence.solver_name == "HiGHS"

    diagnostic = solve_milp_diagnostic(graph, skill, synergy, params.model_copy(update={"solver": "cbc"}))

    assert diagnostic.evidence.solver_name == "CBC"
    assert diagnostic.evidence.best_bound is None


@pytest.mark.parametrize("field", ["objective", "plan_objective", "plan_allocation"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_raw_contract_rejects_nonfinite_public_values(field, value):
    graph, skill, synergy, params, raw = all_terms_fixture()
    bad = deepcopy(raw)
    if field == "objective":
        bad = replace(bad, objective=value)
    elif field == "plan_objective":
        bad.plan.objective = value
    else:
        bad.plan.entries[0].alloc = value
    report = validate_raw_solution(graph, skill, synergy, params, bad)
    assert not report.valid
    assert "nonfinite_value" in {issue.code for issue in report.issues}


def test_raw_contract_rejects_duplicate_display_assignments():
    graph, skill, synergy, params, raw = all_terms_fixture()
    raw.plan.entries.append(deepcopy(raw.plan.entries[0]))
    report = validate_raw_solution(graph, skill, synergy, params, raw)
    assert not report.valid
    assert "duplicate_plan_entry" in {issue.code for issue in report.issues}


def test_raw_contract_rejects_nonfinite_recomputed_objective():
    graph, skill, synergy, params, raw = all_terms_fixture()
    skill[0, 0] = float("nan")
    report = validate_raw_solution(graph, skill, synergy, params, raw)
    assert not report.valid
    assert "objective" in {issue.code for issue in report.issues}


@pytest.mark.parametrize("constraint", ["availability", "allocation_lower"])
def test_raw_contract_does_not_accept_nonfinite_constraint_limits(constraint):
    graph, skill, synergy, params, raw = all_terms_fixture()
    if constraint == "availability":
        graph.people[0].availability[0] = float("nan")
    else:
        params.min_alloc = float("nan")
    report = validate_raw_solution(graph, skill, synergy, params, raw)
    assert not report.valid
    assert constraint in {issue.code for issue in report.issues}


def test_highs_diagnostics_carry_the_proven_bound_in_the_maximize_direction():
    """2026-10-06: HiGHS's dual bound is reported so time-limited plans can say how far from proven best they
    may be. PuLP negates maximization for HiGHS, so the bound must come back flipped (>= the objective)."""
    graph, skill, synergy, params, _ = all_terms_fixture()
    exact = solve_milp_diagnostic(graph, skill, synergy, params.model_copy(update={"solver": "highs", "gap": 0.0}))
    assert exact.evidence.best_bound is not None
    assert exact.evidence.best_bound >= exact.objective - 1e-6
    assert exact.evidence.best_bound == pytest.approx(exact.objective, abs=1e-5)
