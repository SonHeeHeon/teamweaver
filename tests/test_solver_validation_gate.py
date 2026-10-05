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
    # 거절된 대안은 내보내지 않고, 묶음은 앞선 유효 플랜(A)까지로 끝난다(예외로 A까지 잃지 않는다).
    assert list(stream) == []


def test_service_rejects_tiny_budget_residual_without_widening_tolerance(monkeypatch):
    graph, skill, synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(
        monkeypatch, status=pulp.LpStatusOptimal, allocation=0.3500000025
    )
    # Integer selection, but 1000 * 0.3500000025 exceeds 350 by 2.5e-6.
    from core.optimize.validation import validate_raw_solution
    from core.optimize import milp
    assert hasattr(milp,"solve_milp_assessment")
    params = MilpParams(pair_keep_ratio=0.0)
    assessment = milp.solve_milp_assessment(graph,skill,synergy,params)
    assert assessment.initial_validation.valid is False
    assert {i.code for i in assessment.initial_validation.issues} == {"budget"}
    assert assessment.native_capture.a[(0,0)] == 0.3500000025
    assert assessment.accepted.z == assessment.native_capture.z
    assert assessment.refinement.attempted is True
    assert assessment.final_validation.valid is True
    assert validate_raw_solution(graph,skill,synergy,params,assessment.accepted).valid


def test_service_extra_constraints_survive_refinement(monkeypatch):
    from core.optimize import milp
    graph,skill,synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(monkeypatch,status=pulp.LpStatusOptimal,allocation=.3500000025)
    def cut(prob,z):
        prob += z[1][0] == 0
        allocation = next(v for v in prob.variables() if v.name == "a_0_0")
        prob += 1000*allocation <= 349.999998
    assert hasattr(milp,"solve_milp_assessment")
    assessment = milp.solve_milp_assessment(graph,skill,synergy,MilpParams(pair_keep_ratio=0.),extra_constraints=cut)
    assert assessment.accepted is not None
    assert 1000*assessment.accepted.a[(0,0)] <= 349.999999
    assert assessment.accepted.z[(1,0)] == 0


def test_service_rejects_refinement_violating_constant_z_cut(monkeypatch):
    from core.optimize import milp
    graph,skill,synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(monkeypatch,status=pulp.LpStatusOptimal,allocation=.3500000025)
    def cut(prob,z):
        prob += z[0][0] <= 0
    assessment = milp.solve_milp_assessment(graph,skill,synergy,MilpParams(pair_keep_ratio=0.),extra_constraints=cut)
    assert assessment.accepted is None
    assert assessment.refinement.attempted is True


def test_alternative_quality_uses_final_objective(monkeypatch):
    from core.optimize import alternatives,milp
    graph,skill,synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(monkeypatch,status=pulp.LpStatusOptimal,allocation=.3500000025)
    params = MilpParams(pair_keep_ratio=0.)
    assessment = milp.solve_milp_assessment(graph,skill,synergy,params)
    plan = milp.solve_milp(graph,skill,synergy,params)
    assert plan.objective == pytest.approx(assessment.accepted.objective,abs=1e-12)
    assert abs(plan.objective-assessment.native_capture.objective) > 1e-12
    # The real sign-symmetric quality gate includes an absolute1e-6 guard.
    # Construct Plan A so the native and final objectives straddle that boundary.
    midpoint = (plan.objective+assessment.native_capture.objective)/2
    base = plan.model_copy(update={"objective":(midpoint+1e-6)/1.05},deep=True)
    assert alternatives.meets_quality_floor(assessment.native_capture.objective,base.objective)
    assert not alternatives.meets_quality_floor(plan.objective,base.objective)
    plans = iter((base,plan.model_copy(deep=True)))
    monkeypatch.setattr(alternatives,"solve_milp",lambda *a,**kw:next(plans))
    assert len(alternatives.generate_plans(graph,skill,synergy,params,n_alternatives=1)) == 1


def test_service_unsupported_callback_does_not_refine(monkeypatch):
    from core.optimize import milp
    graph,skill,synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(monkeypatch,status=pulp.LpStatusOptimal,allocation=.3500000025)
    def mutation(prob,z):
        z[1][0].upBound = 0
    assert hasattr(milp,"solve_milp_assessment")
    assessment = milp.solve_milp_assessment(graph,skill,synergy,MilpParams(pair_keep_ratio=0.),extra_constraints=mutation)
    assert assessment.accepted is None
    assert "UNSUPPORTED_MODEL_MUTATION" in assessment.refinement.reason


def test_service_never_bypasses_final_gate(monkeypatch):
    from core.optimize import milp,numerics,validation
    from dataclasses import replace
    graph,skill,synergy = budget_shortfall_fixture()
    _inject_cbc_candidate(monkeypatch,status=pulp.LpStatusOptimal)
    assert hasattr(milp,"solve_milp_assessment")
    assessment = milp.solve_milp_assessment(graph,skill,synergy,MilpParams(pair_keep_ratio=0.))
    bad = replace(assessment.accepted,a={(0,0):.4,(1,0):0.})
    monkeypatch.setattr(milp,"solve_milp_assessment",lambda *a,**kw:replace(assessment,accepted=bad))
    with pytest.raises(RuntimeError,match="budget"):
        solve_milp_diagnostic(graph,skill,synergy,MilpParams(pair_keep_ratio=0.))


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
