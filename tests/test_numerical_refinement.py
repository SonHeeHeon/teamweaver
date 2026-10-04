"""Strict final feasibility, native-based guards and additive model preservation."""
import importlib
import importlib.util
from dataclasses import replace
import time
import pulp
import pytest
from core.optimize.audit_types import RawMilpSolution, SolverEvidence
from core.optimize.candidate import rebuild_plan
from core.optimize.milp import MilpParams
from core.optimize.types import PlanAssignment
from core.optimize.validation import validate_raw_solution
from tests.phase0.factories import budget_shortfall_fixture


def _module():
    assert importlib.util.find_spec("core.optimize.numerics") is not None, "refinement is missing"
    return importlib.import_module("core.optimize.numerics")


def _fixture(allocation=.3500000025):
    graph, S, C = budget_shortfall_fixture()
    params = MilpParams(pair_keep_ratio=0)
    raw = RawMilpSolution(PlanAssignment(entries=[], objective=.9*allocation-100, unfilled=[], violations=[], label="A"),
        "Optimal", .9*allocation-100, {(0,0):1.,(1,0):0.}, {(0,0):allocation,(1,0):0.}, {},
        {(0,graph.people[0].grade):1.}, (), (), 5, 6,
        SolverEvidence("CBC","Optimal","Optimal",True,None,{}))
    return graph,S,C,params,rebuild_plan(graph,params,raw)


def _assess(raw=None, *, policy=None, deadline=None, extra=(), native=None):
    module = _module()
    graph,S,C,params,base = _fixture()
    raw = base if raw is None else raw
    return module.assess_candidate(graph,S,C,params,raw,native_capture=raw if native is None else native,
        policy=policy or module.NumericalPolicy(enabled=True),deadline=deadline,extra_linear_constraints=extra)


def test_budget_only_refinement_keeps_selection_and_strictly_validates():
    graph,S,C,params,raw = _fixture()
    assert {i.code for i in validate_raw_solution(graph,S,C,params,raw).issues} == {"budget"}
    result = _assess(raw)
    assert result.accepted is not None
    assert result.accepted.z == raw.z
    assert result.final_validation.valid is True
    assert 1000*result.accepted.a[(0,0)] <= 350+1e-6
    assert raw.a[(0,0)] == .3500000025


def test_valid_candidate_is_unchanged_without_lp():
    *_, raw = _fixture(.35)
    result = _assess(raw)
    assert result.accepted is raw
    assert result.refinement.attempted is False


def test_allocation_delta_is_bounded_from_native():
    result = _assess()
    assert result.accepted is not None
    assert 0 < result.refinement.max_allocation_delta <= 1e-7


@pytest.mark.parametrize("z", [.75,.9999995])
def test_fractional_z_is_not_repaired(z):
    graph,S,C,params,raw = _fixture()
    native = replace(raw,z={(0,0):z,(1,0):0.})
    result = _assess(raw, native=native)
    assert result.accepted is None
    assert result.refinement.attempted is False


def test_native_invalid_normalized_valid_is_not_native_pass():
    *_,raw = _fixture(.35)
    native = replace(raw,a={(0,0):.35,(1,0):-5e-7})
    result = _assess(raw,native=native)
    assert result.accepted is raw
    assert result.native_validation.valid is False
    assert result.initial_validation.valid is True


@pytest.mark.parametrize("value", [float("nan"),float("inf"),None])
def test_nonfinite_and_missing_are_not_repaired(value):
    *_,raw = _fixture()
    result = _assess(replace(raw,a={(0,0):value,(1,0):0.}))
    assert result.accepted is None and not result.refinement.attempted


def test_large_or_multiple_constraint_failures_are_rejected():
    *_,raw = _fixture(.4)
    assert _assess(raw).accepted is None


def test_disabled_policy_retains_rejection():
    assert _assess(policy=_module().NumericalPolicy()).accepted is None


def test_deadline_leaves_no_time_for_refinement():
    result = _assess(deadline=time.monotonic()-1)
    assert result.accepted is None
    assert result.refinement.reason == "REFINEMENT_BUDGET_EXCEEDED"


def test_infeasible_lp_is_rejected():
    module = _module()
    constraint = module.LinearAllocationConstraint({(0,0):1.},"GE",.35000002)
    assert _assess(extra=(constraint,)).accepted is None


def test_extra_a_constraint_is_preserved():
    module = _module()
    result = _assess(extra=(module.LinearAllocationConstraint({(0,0):1.},"LE",.34999995),))
    assert result.accepted is not None
    assert result.accepted.a[(0,0)] <= .34999995+1e-10


def test_constant_only_cut_is_checked():
    module = _module()
    assert _assess(extra=(module.LinearAllocationConstraint({},"LE",-1),)).accepted is None


def test_lp_success_with_invalid_result_is_rejected(monkeypatch):
    module = _module()
    from types import SimpleNamespace
    monkeypatch.setattr(module,"linprog",lambda *a,**kw:SimpleNamespace(success=True,x=[.35000002,0.],message="injected"))
    result = _assess()
    assert result.accepted is None
    assert result.final_validation.valid is False


def test_success_after_budget_is_rejected(monkeypatch):
    module = _module()
    original = module.linprog
    def slow(*args,**kwargs):
        value = original(*args,**kwargs)
        time.sleep(.015)
        return value
    monkeypatch.setattr(module,"linprog",slow)
    result = _assess(policy=module.NumericalPolicy(enabled=True,max_refinement_seconds=.005))
    assert result.accepted is None
    assert result.refinement.reason == "REFINEMENT_BUDGET_EXCEEDED"


def test_extra_row_verification_after_budget_is_rejected(monkeypatch):
    module = _module()
    original = module._linear_feasible
    def slow(*args):
        time.sleep(.08)
        return original(*args)
    monkeypatch.setattr(module,"_linear_feasible",slow)
    result = _assess(policy=module.NumericalPolicy(enabled=True,max_refinement_seconds=.05))
    assert result.accepted is None
    assert result.refinement.reason == "REFINEMENT_BUDGET_EXCEEDED"


def test_success_evidence_computation_after_budget_is_rejected(monkeypatch):
    module = _module()
    original = module._residuals
    calls = 0
    def slow(*args):
        nonlocal calls
        calls += 1
        if calls == 2: time.sleep(.08)
        return original(*args)
    monkeypatch.setattr(module,"_residuals",slow)
    result = _assess(policy=module.NumericalPolicy(enabled=True,max_refinement_seconds=.05))
    assert result.accepted is None
    assert result.refinement.reason == "REFINEMENT_BUDGET_EXCEEDED"


@pytest.mark.parametrize("mutation", ["bounds","category","objective","delete","new_variable"])
def test_callback_model_mutation_fails_closed(mutation):
    module = _module()
    prob = pulp.LpProblem("callback",pulp.LpMaximize)
    a = pulp.LpVariable("a",0,1)
    prob += a
    prob += a <= 1,"base"
    before = module.capture_model_contract(prob)
    if mutation == "bounds": a.upBound=.3
    if mutation == "category": a.cat=pulp.LpInteger
    if mutation == "objective": prob.setObjective(2*a)
    if mutation == "delete": del prob.constraints["base"]
    if mutation == "new_variable": prob += pulp.LpVariable("extra") <= 2
    with pytest.raises(module.UnsupportedModelMutation):
        module.project_additive_constraints(before,module.capture_model_contract(prob),allocation_variables={a.name:(0,0)},fixed_values={})


def test_actual_pulp_extra_row_is_projected():
    module = _module()
    prob = pulp.LpProblem("callback",pulp.LpMaximize)
    a = pulp.LpVariable("a",0,1)
    z = pulp.LpVariable("z",0,1)
    prob += a+z
    before = module.capture_model_contract(prob)
    prob += a+2*z <= 2.3
    rows = module.project_additive_constraints(before,module.capture_model_contract(prob),allocation_variables={a.name:(0,0)},fixed_values={z.name:1.})
    assert rows[0].coefficients == {(0,0):1.}
    assert rows[0].rhs == pytest.approx(.3)
