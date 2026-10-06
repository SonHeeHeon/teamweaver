"""Experiment G (2026-10-06): partner-diversity floor instead of (or with) the over-familiar pair penalty.

A person with recent partners (the over-familiar pair rule) should have at least `partner_floor` teammates who are not
recent partners; each missing one costs `partner_floor_weight`. Off by default (the previous model, byte for byte)."""
import itertools

import numpy as np
import pytest

from core.domain.models import CoworkRecord, Dataset, Grade, Project, ProjectPhase, Sector, SkillRequirement
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, solve_milp, solve_milp_assessment
from core.optimize.types import AssignEntry
from core.optimize.validation import _independent_partner_floor
from tests.phase0.factories import _person

IDS = ["p0", "p1", "p2", "p3", "p4", "p5"]


def _case():
    people = [_person(p, Grade.MID) for p in IDS]
    proj = lambda pid: Project(id=pid, name=pid, sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=0,
                               end_month=5, grade_headcount={Grade.MID: 3},
                               requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                               monthly_budget=100_000)
    # p0, p1, p2 have worked together a lot recently (a familiar trio); p3 and p4 too
    coworks = [CoworkRecord(a_id=a, b_id=b, co_months=12, project_count=1)
               for a, b in (("p0", "p1"), ("p0", "p2"), ("p1", "p2"), ("p3", "p4"))]
    graph = MemoryGraph.build(Dataset(people=people, projects=[proj("P1"), proj("P2")], coworks=coworks, reviews=[]),
                              parsed=[])
    S = np.array([[0.9, 0.3], [0.9, 0.3], [0.9, 0.3], [0.3, 0.8], [0.3, 0.8], [0.5, 0.5]])
    return graph, S, np.zeros((6, 6))


def _params(**kw):
    return MilpParams(min_alloc=1.0, time_limit=30, gap=0.0, solver="highs", clique_threshold_months=12,
                      max_pairs=10, pair_keep_ratio=1.0, **kw)


def _brute_force(graph, S, C, params):
    best = None
    for choice in itertools.product((None, "P1", "P2"), repeat=len(IDS)):
        entries = [AssignEntry(person_id=p, project_id=j, alloc=1.0) for p, j in zip(IDS, choice) if j]
        ev = evaluate_plan(graph, S, C, params, entries)
        if not ev.violations and (best is None or ev.objective.total > best[0] + 1e-9):
            best = (ev.objective.total, choice)
    return best


@pytest.mark.parametrize("floor,weight,mu", [(1, 0.5, 0.0), (2, 0.3, 0.0), (1, 0.4, 0.2)])
def test_milp_validator_and_evaluator_agree_with_exhaustive_search(floor, weight, mu):
    graph, S, C = _case()
    params = _params(partner_floor=floor, partner_floor_weight=weight, mu=mu)
    a = solve_milp_assessment(graph, S, C, params)
    assert a.accepted is not None, a                         # the independent validator recomputed the same objective
    plan = a.accepted.plan
    best, _ = _brute_force(graph, S, C, params)
    assert plan.objective == pytest.approx(best, abs=1e-6)
    assert evaluate_plan(graph, S, C, params, plan.entries).objective.total == pytest.approx(best, abs=1e-6)


def test_the_floor_breaks_up_the_familiar_trio():
    graph, S, C = _case()
    off = solve_milp(graph, S, C, _params(mu=0.0))
    on = solve_milp(graph, S, C, _params(mu=0.0, partner_floor=1, partner_floor_weight=0.5))
    team = lambda plan, j: {e.person_id for e in plan.entries if e.project_id == j}
    assert team(off, "P1") == {"p0", "p1", "p2"}             # skill alone keeps the trio together
    assert len(team(on, "P1") & {"p0", "p1", "p2"}) <= 2      # with the floor, each needs a new partner


def test_off_by_default_keeps_the_model_identical():
    graph, S, C = _case()
    base = solve_milp_assessment(graph, S, C, _params())
    weight_only = solve_milp_assessment(graph, S, C, _params(partner_floor_weight=0.5))      # floor 0 -> off
    assert base.native_capture.variable_count == weight_only.native_capture.variable_count
    assert base.accepted.plan.objective == weight_only.accepted.plan.objective


def test_independent_count():
    z = {(i, j): 0.0 for i in range(4) for j in range(1)}
    for i in (0, 1, 2):
        z[(i, 0)] = 1.0
    params = _params(partner_floor=1, partner_floor_weight=1.0)
    # trio of mutual partners: each has 0 new partners -> 3 missing; p3 is not on the team
    assert _independent_partner_floor(z, [(0, 1), (0, 2), (1, 2)], params, 4, 1) == -3.0
    z[(3, 0)] = 1.0                                           # a stranger joins: everyone now has one new partner
    assert _independent_partner_floor(z, [(0, 1), (0, 2), (1, 2)], params, 4, 1) == 0.0


def test_budget_refinement_keeps_the_floor_term():
    """Review MUST: the C1 refinement (a tiny budget residue on the allocations) recomputed the objective without the
    floor term, so the final validation rejected every plan with a non-zero floor penalty."""
    from dataclasses import replace

    from core.optimize.numerics import NumericalPolicy, assess_candidate
    graph, S, C = _case()
    tight = [pj.model_copy(update={"monthly_budget": 2_500}) for pj in graph.projects]
    graph = replace(graph, projects=tight)
    params = _params(mu=0.0, partner_floor=2, partner_floor_weight=0.3).model_copy(
        update={"min_alloc": 0.5, "max_concurrent_projects": 1})
    cand = solve_milp_assessment(graph, S, C, params).accepted
    assert cand is not None
    from core.optimize.validation import validate_raw_solution
    assert validate_raw_solution(graph, S, C, params, cand).objective.overfamiliarity < 0   # the floor term is in play
    key = next(k for k, v in cand.a.items() if 0.5 <= v < 1.0 - 1e-6)    # a budget-bound fractional allocation
    na = dict(cand.a)
    na[key] += 5e-8                                                       # a residue just over the budget
    nudged = replace(cand, a=na, objective=cand.objective + S[key] * 5e-8,
                     plan=cand.plan.model_copy(update={"objective": cand.objective + S[key] * 5e-8}))
    out = assess_candidate(graph, S, C, params, nudged, native_capture=nudged, policy=NumericalPolicy(enabled=True))
    assert out.refinement.reason == "REFINED" and out.accepted is not None
