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

    assert raw.evidence.solver_name == "CBC"
    assert raw.evidence.best_bound is None

    diagnostic = solve_milp_diagnostic(graph, skill, synergy, params)

    assert diagnostic.evidence.solver_name == "CBC"
    assert diagnostic.evidence.best_bound is None
