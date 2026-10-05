import numpy as np
import pytest

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import (
    CoworkRecord, Dataset, Grade, Person, Project, ProjectPhase, Sector, SkillRequirement,
)
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, solve_milp_diagnostic
from core.optimize.types import AssignEntry
from core.scoring.engine import ScoringEngine

ALL_PAIRS = MilpParams(pair_keep_ratio=1.0)


def _person(pid, grade=Grade.MID, rate=1_000, availability=None):
    return Person(id=pid, name=pid.upper(), grade=grade, monthly_rate=rate,
                  skills={"Python": 3}, availability=availability or [1.0] * 6)


def _project(headcount=None, budget=3_000):
    return Project(id="j0", name="J0", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                   start_month=0, end_month=1,
                   grade_headcount=headcount or {Grade.MID: 2},
                   requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                   monthly_budget=budget)


def _graph(people, project, coworks=()):
    ds = Dataset(people=people, projects=[project], coworks=list(coworks), reviews=[])
    return MemoryGraph.build(ds, parsed=[])


def _fixture(**project_kw):
    people = [_person("p0"), _person("p1"), _person("p2", grade=Grade.SENIOR)]
    graph = _graph(people, _project(**project_kw),
                   coworks=[CoworkRecord(a_id="p0", b_id="p1", co_months=8, project_count=1)])
    S = np.array([[0.8], [0.6], [0.9]])
    C = np.zeros((3, 3))
    C[0, 1] = C[1, 0] = 0.5
    C[0, 2] = C[2, 0] = -0.4
    return graph, S, C


def _entries(*rows):
    return [AssignEntry(person_id=p, project_id="j0", alloc=a) for p, a in rows]


def test_breakdown_matches_hand_computed_milp_objective():
    graph, S, C = _fixture()
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p1", 1.0)))

    assert ev.objective.skill == pytest.approx(0.8 * 0.5 + 0.6 * 1.0)
    assert ev.objective.synergy == pytest.approx(0.3 * 0.5)          # lambda * C[p0,p1]
    assert ev.objective.overfamiliarity == pytest.approx(-0.2)       # 8 months >= 6
    assert ev.objective.unfilled == pytest.approx(0.0)
    assert ev.objective.total == pytest.approx(1.0 + 0.15 - 0.2)
    assert ev.violations == ()


def test_negative_synergy_pair_lowers_score_only_when_both_present():
    graph, S, C = _fixture(headcount={Grade.MID: 1, Grade.SENIOR: 1})
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p2", 0.5)))
    assert ev.objective.synergy == pytest.approx(0.3 * -0.4)
    assert ev.objective.overfamiliarity == pytest.approx(0.0)


def test_reward_pairs_follow_milp_pruning_scope():
    graph, S, C = _fixture()
    pruned = MilpParams(pair_keep_ratio=0.0)                          # no reward pairs kept
    ev = evaluate_plan(graph, S, C, pruned, _entries(("p0", 0.5), ("p1", 0.5)))
    assert ev.objective.synergy == 0.0
    assert ev.objective.overfamiliarity == pytest.approx(-0.2)       # penalty ignores pruning


def test_unfilled_grade_slot_costs_slack_penalty():
    graph, S, C = _fixture()
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5)))
    assert ev.objective.unfilled == pytest.approx(-100.0)
    assert ev.violations == ()
    assert [(s.project_id, s.grade, s.missing) for s in ev.shortfalls] == [("j0", "중급", 1)]


def test_full_plan_has_no_shortfalls():
    graph, S, C = _fixture()
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p1", 0.5)))
    assert ev.shortfalls == ()


def test_availability_violation_reports_person_and_month():
    people = [_person("p0", availability=[1.0, 0.4, 1, 1, 1, 1]), _person("p1")]
    graph = _graph(people, _project())
    S, C = np.ones((2, 1)), np.zeros((2, 2))
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p1", 0.5)))
    assert [(v.code, v.location) for v in ev.violations] == [("availability", "p0:month1")]
    assert ev.violations[0].actual == pytest.approx(0.5)
    assert ev.violations[0].limit == pytest.approx(0.4)


def test_budget_violation():
    graph, S, C = _fixture(budget=1_200)
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p1", 1.0)))
    assert [(v.code, v.location) for v in ev.violations] == [("budget", "j0")]
    assert ev.violations[0].actual == pytest.approx(1_500)


def test_grade_over_headcount_is_a_violation_but_unlisted_grade_is_not():
    graph, S, C = _fixture(headcount={Grade.MID: 1})
    ev = evaluate_plan(graph, S, C, ALL_PAIRS,
                       _entries(("p0", 0.5), ("p1", 0.5), ("p2", 0.5)))
    assert [(v.code, v.location) for v in ev.violations] == [("grade_over", "j0:중급")]


def test_alloc_outside_range_is_a_violation():
    graph, S, C = _fixture()
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.1), ("p1", 0.5)))
    assert [(v.code, v.location) for v in ev.violations] == [("alloc_range", "p0:j0")]


def test_every_violation_has_a_message():
    graph, S, C = _fixture(budget=100)
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p1", 0.5)))
    assert ev.violations and all(v.message for v in ev.violations)


@pytest.mark.parametrize("entry", [
    AssignEntry(person_id="nobody", project_id="j0", alloc=0.5),
    AssignEntry(person_id="p0", project_id="nowhere", alloc=0.5),
])
def test_unknown_ids_are_rejected(entry):
    graph, S, C = _fixture()
    with pytest.raises(ValueError):
        evaluate_plan(graph, S, C, ALL_PAIRS, [entry])


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_alloc_is_rejected(bad):
    graph, S, C = _fixture()
    with pytest.raises(ValueError):
        evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", bad)))


def test_duplicate_assignment_is_rejected():
    graph, S, C = _fixture()
    with pytest.raises(ValueError):
        evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p0", 0.3)))


def test_milp_solution_has_no_violations_and_matches_objective():
    ds = generate_dataset(15, 3, seed=1)
    graph = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = MilpParams()
    raw = solve_milp_diagnostic(graph, S, C, params)
    plan = raw.plan

    ev = evaluate_plan(graph, S, C, params, plan.entries)

    assert ev.violations == ()
    assert sorted(f"{s.project_id}:{s.grade}:{s.missing}명 미충원" for s in ev.shortfalls) \
        == sorted(plan.unfilled)
    # display allocs are floored to 2 decimals: each entry may lose < 0.01 * S <= 0.01
    assert ev.objective.total <= plan.objective + 1e-6
    assert plan.objective - ev.objective.total < 0.01 * len(plan.entries) + 1e-6
    # the non-skill terms depend only on z, which display rounding does not change
    raw_skill = sum(S[i, j] * a for (i, j), a in raw.a.items())
    non_skill = ev.objective.synergy + ev.objective.overfamiliarity + ev.objective.unfilled
    assert non_skill == pytest.approx(plan.objective - raw_skill, abs=1e-6)


def _multi_project_graph(n_projects):
    people = [_person("p0", availability=[1.0] * 6)]
    projects = [Project(id=f"j{k}", name=f"J{k}", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                        start_month=0, end_month=1, grade_headcount={Grade.MID: 1},
                        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                        monthly_budget=10_000) for k in range(n_projects)]
    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    return MemoryGraph.build(ds, parsed=[])


@pytest.mark.parametrize("n, expect", [(3, 0), (4, 2)])
def test_concurrent_projects_over_the_limit_is_a_violation(n, expect):
    """C6: one person on more than max_concurrent_projects projects in the same month (claude-b request)."""
    g = _multi_project_graph(n)
    entries = [AssignEntry(person_id="p0", project_id=f"j{k}", alloc=0.25) for k in range(n)]
    ev = evaluate_plan(g, np.full((1, n), 0.5), np.zeros((1, 1)), MilpParams(min_alloc=0.2), entries)
    found = [v for v in ev.violations if v.code == "concurrent_projects"]
    assert len(found) == expect                      # months 0 and 1 when over the limit
    if expect:
        assert found[0].actual == 4.0 and found[0].limit == 3.0 and "상한 3개를 초과" in found[0].message


def test_plan_eval_matches_the_solver_objective_with_per_seat_fit():
    ds = generate_dataset(20, 4, seed=3)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    e = ScoringEngine(g)
    S, C = e.skill_matrix({}), e.synergy_matrix()
    params = MilpParams(time_limit=30, seat_fit_weight=0.5)
    raw = solve_milp_diagnostic(g, S, C, params)
    ev = evaluate_plan(g, S, C, params, raw.plan.entries)
    # display allocations are floored, so allow the flooring loss on the S*a part only
    assert ev.objective.total == pytest.approx(raw.objective, abs=0.05)
    seat_part = 0.5 * sum(float(S[g.pid_index[x.person_id], g.project_index[x.project_id]]) for x in raw.plan.entries)
    no_seat = evaluate_plan(g, S, C, params.model_copy(update={"seat_fit_weight": 0.0}), raw.plan.entries)
    assert ev.objective.skill - no_seat.objective.skill == pytest.approx(seat_part)


# ---- 달별 투입률(monthly_alloc, claude-b 요청 2026-10-05) ---------------------------------------
from dataclasses import dataclass as _dc


@_dc
class _MEntry:                      # stand-in until AssignEntry gains monthly_alloc on claude-b's branch
    person_id: str
    project_id: str
    alloc: float
    monthly_alloc: dict | None = None


def test_without_monthly_alloc_results_are_bit_identical():
    graph, S, C = _fixture()
    plain = _entries(("p0", 0.5), ("p1", 1.0))
    same = [_MEntry(e.person_id, e.project_id, e.alloc) for e in plain]
    assert evaluate_plan(graph, S, C, ALL_PAIRS, plain) == evaluate_plan(graph, S, C, ALL_PAIRS, same)


def test_monthly_alloc_checks_each_month_and_uses_the_mean_for_skill():
    graph, S, C = _fixture(budget=10_000)
    # project j0 runs months 0-1. p0: 0.2 then 0.8 (mean 0.5); p0 has availability 1.0 so month 1 is fine
    entries = [_MEntry("p0", "j0", 0.5, {0: 0.2, 1: 0.8}), _MEntry("p1", "j0", 1.0)]
    ev = evaluate_plan(graph, S, C, MilpParams(pair_keep_ratio=1.0, min_alloc=0.3), entries)
    assert ev.objective.skill == pytest.approx(0.8 * 0.5 + 0.6 * 1.0)
    codes = {(v.code, v.location) for v in ev.violations}
    assert ("alloc_range", "p0:j0:month0") in codes and ("alloc_range", "p0:j0:month1") not in codes


def test_monthly_alloc_budget_and_availability_are_per_month():
    people = [_person("p0", availability=[1.0, 0.5, 1.0, 1.0, 1.0, 1.0]), _person("p1")]
    graph = _graph(people, _project(budget=1_200))      # month0 cost 900, month1 cost 1300
    S, C = np.array([[0.8], [0.6]]), np.zeros((2, 2))
    entries = [_MEntry("p0", "j0", 0.6, {0: 0.4, 1: 0.8}), _MEntry("p1", "j0", 0.5)]
    ev = evaluate_plan(graph, S, C, MilpParams(min_alloc=0.2), entries)
    codes = {(v.code, v.location) for v in ev.violations}
    assert ("availability", "p0:month1") in codes and ("availability", "p0:month0") not in codes
    assert ("budget", "j0:month1") in codes and ("budget", "j0:month0") not in codes


def test_bit_identity_against_golden_values():
    """Values computed on the evaluator before monthly_alloc existed (same fixture as the hand-computed test)."""
    graph, S, C = _fixture()
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, [_MEntry("p0", "j0", 0.5, {}), _MEntry("p1", "j0", 1.0, None)])
    golden = evaluate_plan(graph, S, C, ALL_PAIRS, _entries(("p0", 0.5), ("p1", 1.0)))      # plain AssignEntry path
    assert ev == golden and ev.objective.total == pytest.approx(1.0 + 0.15 - 0.2)
    assert ev.objective.skill == 0.8 * 0.5 + 0.6 * 1.0 and ev.violations == ()


@pytest.mark.parametrize("bad", [{0: 0.5}, {0: 0.5, 1: 0.5, 2: 0.5}, {0: 0.9, 1: 0.9}, {True: 0.5, 1: 0.5}])
def test_monthly_alloc_must_cover_the_project_months_and_match_alloc(bad):
    graph, S, C = _fixture()
    with pytest.raises(ValueError):
        evaluate_plan(graph, S, C, ALL_PAIRS, [_MEntry("p0", "j0", 0.5, bad)])


def test_budget_location_stays_per_project_when_no_one_has_monthly_values():
    graph, S, C = _fixture(budget=500)
    ev = evaluate_plan(graph, S, C, ALL_PAIRS, [_MEntry("p0", "j0", 0.5), _MEntry("p1", "j0", 1.0)])
    assert ("budget", "j0") in {(v.code, v.location) for v in ev.violations}


def test_evaluator_advertises_monthly_support():
    from core.evaluate import plan_eval
    assert plan_eval.SUPPORTS_MONTHLY_ALLOC is True
