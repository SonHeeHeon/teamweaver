"""Invalid native solver candidates must never escape as recommendations."""

import pulp
import pytest

from core.optimize.alternatives import generate_plans_streaming
from core.optimize.milp import MilpParams, solve_milp, solve_milp_diagnostic
from tests.phase0.factories import budget_shortfall_fixture


def _inject_cbc_candidate(monkeypatch, *, status, fractional=False, over_budget=False,
                          allocation=None):
    # Replace only native solving; model construction, extraction, objective and
    # independent verification remain real. CBC's relaxation is not an incumbent.
    values = {
        "z_0_0": 1.0, "z_1_0": 0.75 if fractional else 0.0,
        "a_0_0": 0.2 if fractional else (0.4 if over_budget else 0.35),
        "a_1_0": 0.15 if fractional else 0.0,
        "s_0_고급": 0.0 if fractional else 1.0,
    }
    if allocation is not None:
        values["a_0_0"] = allocation

    def solve(problem, solver=None, **kwargs):
        problem.status = status
        for variable in problem.variables():
            variable.varValue = values[variable.name]
        return status

    monkeypatch.setattr(pulp.LpProblem, "solve", solve)
    return solve


@pytest.mark.parametrize("entrypoint", ["diagnostic", "plan", "stream"])
@pytest.mark.parametrize("failure", ["fractional", "budget"])
def test_service_entrypoints_reject_invalid_native_candidate(monkeypatch, entrypoint, failure):
    graph, skill, synergy = budget_shortfall_fixture()
    params = MilpParams(time_limit=0, pair_keep_ratio=0.0)
    _inject_cbc_candidate(
        monkeypatch,
        status=pulp.LpStatusNotSolved if failure == "fractional" else pulp.LpStatusOptimal,
        fractional=failure == "fractional",
        over_budget=failure == "budget",
    )
    code = "binary_domain" if failure == "fractional" else "budget"
    with pytest.raises(RuntimeError, match=code):
        if entrypoint == "diagnostic":
            solve_milp_diagnostic(graph, skill, synergy, params)
        elif entrypoint == "plan":
            solve_milp(graph, skill, synergy, params)
        else:
            next(generate_plans_streaming(graph, skill, synergy, params))


def test_service_preserves_valid_time_limited_incumbent(monkeypatch):
    graph, skill, synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(monkeypatch, status=pulp.LpStatusNotSolved)
    raw = solve_milp_diagnostic(graph, skill, synergy, MilpParams(pair_keep_ratio=0.0))
    assert raw.status == "Not Solved"
    assert raw.evidence.has_incumbent is True
    assert [(e.person_id, e.alloc) for e in raw.plan.entries] == [("p0", 0.35)]
    assert raw.plan.unfilled == ["j0:고급:1명 미충원"]
    assert raw.objective == pytest.approx(-99.685)


def test_stream_does_not_emit_invalid_alternative_after_valid_plan_a(monkeypatch):
    graph, skill, synergy = budget_shortfall_fixture()
    valid = _inject_cbc_candidate(monkeypatch, status=pulp.LpStatusOptimal)
    invalid = _inject_cbc_candidate(
        monkeypatch, status=pulp.LpStatusNotSolved, fractional=True
    )
    native_solves = iter([valid, invalid])
    monkeypatch.setattr(
        pulp.LpProblem, "solve",
        lambda problem, solver=None, **kwargs: next(native_solves)(problem, solver, **kwargs),
    )
    stream = generate_plans_streaming(
        graph, skill, synergy, MilpParams(pair_keep_ratio=0.0), n_alternatives=1
    )
    plan_a = next(stream)
    assert plan_a.label == "A"
    assert [(e.person_id, e.alloc) for e in plan_a.entries] == [("p0", 0.35)]
    with pytest.raises(RuntimeError, match="binary_domain"):
        next(stream)


def test_service_rejects_tiny_budget_residual_without_widening_tolerance(monkeypatch):
    graph, skill, synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(
        monkeypatch, status=pulp.LpStatusOptimal, allocation=0.3500000025
    )
    # Integer selection, but 1000 * 0.3500000025 exceeds 350 by 2.5e-6.
    with pytest.raises(RuntimeError, match="budget"):
        solve_milp_diagnostic(graph, skill, synergy, MilpParams(pair_keep_ratio=0.0))


@pytest.mark.parametrize("seconds", [0, 1])
def test_real_cbc_short_limit_returns_only_integer_feasible_candidates(seconds):
    graph, skill, synergy = budget_shortfall_fixture()
    try:
        raw = solve_milp_diagnostic(
            graph, skill, synergy, MilpParams(time_limit=seconds, pair_keep_ratio=0.0)
        )
    except RuntimeError as exc:
        assert "incumbent" in str(exc) or "validation" in str(exc)
        return
    assert all(abs(z - round(z)) <= 1e-6 for z in raw.z.values())
    assert sum(raw.z.values()) + sum(raw.slack.values()) == pytest.approx(2.0)
    assert sum(p.monthly_rate * raw.a[(i, 0)] for i, p in enumerate(graph.people)) <= 350 + 1e-6
