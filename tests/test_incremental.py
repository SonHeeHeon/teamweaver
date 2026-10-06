"""Operating-time staffing with a move budget (core/optimize/incremental.py, core/evaluate/operating.py)."""
import itertools

import numpy as np
import pytest

from core.domain.models import CurrentAssignment, Dataset, Grade, Project, ProjectPhase, Sector, SkillRequirement
from core.evaluate.operating import compare_move_budgets
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry
from tests.phase0.factories import _person

GRADES = {"p0": Grade.MID, "p1": Grade.MID, "p2": Grade.JUNIOR, "p3": Grade.MID, "p4": Grade.SENIOR, "p5": Grade.SENIOR}
CURRENT = [CurrentAssignment(person_id=p, project_id="P1", alloc=1.0) for p in ("p0", "p1", "p2")]


def _case():
    people = [_person(p, g) for p, g in GRADES.items()]
    projects = [
        Project(id="P1", name="진행 중", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=0, end_month=5,
                grade_headcount={Grade.MID: 2, Grade.JUNIOR: 1},
                requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)], monthly_budget=100_000),
        Project(id="P2", name="신규 제안", sector=Sector.INTERNAL, phase=ProjectPhase.PROPOSAL, start_month=1, end_month=5,
                grade_headcount={Grade.JUNIOR: 1, Grade.SENIOR: 1},
                requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)], monthly_budget=100_000),
    ]
    graph = MemoryGraph.build(Dataset(people=people, projects=projects, coworks=[], reviews=[]), parsed=[])
    rng = np.random.default_rng(7)
    S = rng.uniform(0.2, 1.0, size=(6, 2))
    C = np.zeros((6, 6))
    C[2, 4] = C[4, 2] = 0.9                          # the junior p2 works very well with the senior p4
    params = MilpParams(min_alloc=1.0, time_limit=30, gap=0.0, solver="highs", max_pairs=10, pair_keep_ratio=1.0)
    return graph, S, C, params


def _brute_force(graph, S, C, params, k):
    people = [p.id for p in graph.people]
    best = None
    for choice in itertools.product((None, "P1", "P2"), repeat=len(people)):
        plan = dict(zip(people, choice))
        moved = sum(plan[c.person_id] != "P1" for c in CURRENT)
        if moved > k or sum(v == "P1" for v in plan.values()) > 3:
            continue
        if any(plan[c.person_id] is None for c in CURRENT):          # a moved person must be placed elsewhere
            continue
        entries = [AssignEntry(person_id=p, project_id=j, alloc=1.0) for p, j in plan.items() if j]
        ev = evaluate_plan(graph, S, C, params, entries)
        if ev.violations:
            continue
        if best is None or ev.objective.total > best + 1e-9:
            best = ev.objective.total
    return best


@pytest.mark.parametrize("k", [0, 1, 2])
def test_engine_matches_exhaustive_search(k):
    graph, S, C, params = _case()
    (row,) = compare_move_budgets(graph, S, C, params, CURRENT, ks=(k,))
    assert row["accepted"]
    assert row["objective"] == pytest.approx(_brute_force(graph, S, C, params, k), abs=1e-4)   # rows are rounded to 4 places


def test_move_budget_rules_hold_and_gains_never_drop():
    graph, S, C, params = _case()
    rows = compare_move_budgets(graph, S, C, params, CURRENT, ks=(0, 1, 2))
    for r in rows:
        assert len(r["diff"]["moved"]) <= r["k"]
        p1 = [e for e in r["entries"] if e["project_id"] == "P1"]
        assert len(p1) <= 3                                          # an ongoing team does not grow
        kept = {e["person_id"] for e in p1} & {"p0", "p1", "p2"}
        assert all(e["alloc"] == 1.0 for e in p1 if e["person_id"] in kept)
    assert rows[0]["diff"]["moved"] == []                            # K=0 keeps the current plan
    objs = [r["objective"] for r in rows]
    assert objs == sorted(objs)                                      # more freedom never hurts (exact solves)
    assert rows[1]["gain_vs_k0"] > 0                                 # moving the junior p2 next to the senior p4 helps


def test_locked_assignments_never_move():
    graph, S, C, params = _case()
    locked = [CurrentAssignment(person_id=c.person_id, project_id="P1", alloc=1.0, locked=(c.person_id == "p2"))
              for c in CURRENT]
    (row,) = compare_move_budgets(graph, S, C, params, locked, ks=(2,))
    assert "p2" not in {m["person_id"] for m in row["diff"]["moved"]}


def test_existing_two_argument_callbacks_still_work():
    """The alternatives' diversity cut uses extra_constraints(prob, z): unchanged."""
    from core.optimize.milp import solve_milp_assessment
    graph, S, C, params = _case()
    seen = []
    a = solve_milp_assessment(graph, S, C, params, extra_constraints=lambda prob, z: seen.append(len(z)))
    assert a.accepted is not None and seen == [6]


# ---- S2: reinforcement simulator (core/evaluate/staffing_sim.py) ----

def _plan_entries():
    return [AssignEntry(person_id=c.person_id, project_id="P1", alloc=1.0) for c in CURRENT]


def test_candidate_ranking_matches_adding_each_person_one_by_one():
    from core.evaluate.staffing_sim import graph_with_extra_seats, rank_candidates
    graph, S, C, params = _case()
    entries = _plan_entries()
    ranked = rank_candidates(graph, S, C, params, entries, "P1", top=None)
    assert {r["person_id"] for r in ranked} == {"p3", "p4", "p5"}            # bench only by default
    for r in ranked:
        g2 = graph_with_extra_seats(graph, "P1", {GRADES[r["person_id"]]: 1}, r["budget_added"])
        before = evaluate_plan(graph, S, C, params, entries).objective.total     # before = original seats
        after = evaluate_plan(g2, S, C, params, entries + [AssignEntry(person_id=r["person_id"], project_id="P1",
                                                                         alloc=r["alloc"])]).objective.total
        assert r["delta_total"] == pytest.approx(after - before, abs=1e-4)
        assert r["new_violations"] == []
    assert [r["delta_total"] for r in ranked] == sorted((r["delta_total"] for r in ranked), reverse=True)
    p4 = next(r for r in ranked if r["person_id"] == "p4")
    assert p4["team_synergy"] == pytest.approx(0.9)       # the reason shows: synergy 0.9 with p2 on the team
    assert p4["delta"]["synergy"] == pytest.approx(0.27)  # lambda 0.3 x C 0.9


def test_pulling_from_another_project_counts_the_hole_it_leaves():
    """p2 sits on P1; reinforcing P2 with p2 leaves P1's junior seat empty -- the slack penalty shows up."""
    from core.evaluate.staffing_sim import rank_candidates
    graph, S, C, params = _case()
    entries = _plan_entries()
    without = {r["person_id"] for r in rank_candidates(graph, S, C, params, entries, "P2", top=None)}
    assert "p2" not in without
    ranked = rank_candidates(graph, S, C, params, entries, "P2", include_pull=True, top=None)
    pulled = next(r for r in ranked if r["person_id"] == "p2")
    assert pulled["source"] == "pull" and pulled["pulled_from"] == ["P1"]
    # P2's existing empty junior seat gets filled (+100), but P1's junior seat empties (-100); the extra seat added
    # for the reinforcement is counted only AFTER, so the evaluator sees exactly those two changes
    from core.evaluate.staffing_sim import graph_with_extra_seats, simulate
    g2 = graph_with_extra_seats(graph, "P2", {Grade.JUNIOR: 1}, pulled["budget_added"])
    sim = simulate(graph, S, C, params, entries, adds=[AssignEntry(person_id="p2", project_id="P2", alloc=1.0)],
                   removes=[("p2", "P1")], graph_after=g2)
    assert sim["delta"]["unfilled"] == pytest.approx(pulled["delta"]["unfilled"])
    assert pulled["delta"]["unfilled"] == pytest.approx(-100.0)       # P1's hole; P2's +1 seat stays empty in "after"


def test_simulate_add_and_remove_against_the_evaluator():
    from core.evaluate.staffing_sim import simulate
    graph, S, C, params = _case()
    entries = _plan_entries()
    sim = simulate(graph, S, C, params, entries, adds=[AssignEntry(person_id="p4", project_id="P2", alloc=1.0)],
                   removes=[("p0", "P1")])
    assert {(e.person_id, e.project_id) for e in sim["entries"]} == {("p1", "P1"), ("p2", "P1"), ("p4", "P2")}
    assert sim["delta"]["total"] == pytest.approx(sim["after"]["total"] - sim["before"]["total"])


def test_best_additions_adds_at_most_n_and_touches_nothing_else():
    """n=1 on P1 from the bench: at most one new member, nobody moved, the proposal P2 untouched."""
    from core.evaluate.staffing_sim import best_additions, rank_candidates
    graph, S, C, params = _case()
    entries = _plan_entries()
    best = best_additions(graph, S, C, params, entries, "P1", 1, grade=None)
    assert best["accepted"] and best["diff"]["moved"] == []
    added = [j for j in best["diff"]["joined"] if j["project_id"] == "P1"]
    assert len(added) <= 1 and all(j["from_bench"] for j in best["diff"]["joined"])
    assert {j["project_id"] for j in best["diff"]["joined"]} <= {"P1"}       # the proposal P2 is not touched


def test_best_two_additions_are_at_least_as_good_as_the_top_two_ranked_candidates_together():
    """The exact n-person solve must not lose to adding the two best single candidates together."""
    from core.evaluate.staffing_sim import best_additions, graph_with_extra_seats, rank_candidates, simulate
    graph, S, C, params = _case()
    entries = _plan_entries()
    best = best_additions(graph, S, C, params, entries, "P1", 2, budget_add=10_000)
    top = rank_candidates(graph, S, C, params, entries, "P1", budget_add=10_000, top=2)
    adds = [AssignEntry(person_id=r["person_id"], project_id="P1", alloc=r["alloc"]) for r in top]
    extra = {}
    for r in top:
        extra[GRADES[r["person_id"]]] = extra.get(GRADES[r["person_id"]], 0) + 1
    together = simulate(graph, S, C, params, entries, adds=adds,
                        graph_after=graph_with_extra_seats(graph, "P1", extra, 10_000))
    assert best["accepted"] and best["violations"] == []
    assert best["delta"]["total"] >= together["delta"]["total"] - 1e-6


def test_baseline_comparison_scores_both_plans_with_the_evaluator():
    from core.evaluate.baseline import compare_with_baseline
    from core.optimize.milp import solve_milp_assessment
    graph, S, C, params = _case()
    a = solve_milp_assessment(graph, S, C, params)
    cmp = compare_with_baseline(graph, S, C, params, a.accepted.plan.entries)
    assert cmp["optimized"]["total"] == pytest.approx(a.accepted.objective, abs=1e-4)
    if not cmp["baseline"]["violations"]:                # a feasible baseline can never beat the exact optimum
        assert cmp["difference"]["total"] >= -1e-6
    assert set(cmp["difference"]) >= {"quality", "unfilled_seats", "violations"}


def test_screening_keeps_the_exact_top_candidates_on_the_operating_data(tmp_path):
    """The fast screen (top 40 by an approximate score, then exact scoring) must return the same top 5 as scoring
    everyone exactly -- on the realistic operating bundle, pulls included."""
    from api.settings import PlacementSettings
    from core.evaluate.staffing_sim import rank_candidates
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.ingest.org_profile import generate_org_bundle
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(generate_org_bundle(tmp_path / "b", 100, seed=2026, scenario="operating"))
    ds, parsed = to_dataset(b, rep)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current]
    fast = rank_candidates(graph, S, C, params, entries, "J005", include_pull=True, top=5)
    full = rank_candidates(graph, S, C, params, entries, "J005", include_pull=True, top=5, screen=None)
    assert [(r["person_id"], r["delta_total"]) for r in fast] == [(r["person_id"], r["delta_total"]) for r in full]



def test_a_person_on_two_projects_cannot_be_benched_by_a_move():
    """Review MUST: someone already on P1 and P3 used to satisfy 'placed elsewhere' through P3 alone, so the engine could
    take them off P1, bench them and refill from the bench. A move must land on a project they were not on before."""
    people = [_person(p, g) for p, g in {"p0": Grade.MID, "p1": Grade.MID, "p2": Grade.MID}.items()]
    proj = lambda pid, phase, hc: Project(id=pid, name=pid, sector=Sector.INTERNAL, phase=phase, start_month=0,
                                          end_month=5, grade_headcount=hc,
                                          requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                                          monthly_budget=100_000)
    projects = [proj("P1", ProjectPhase.EXECUTION, {Grade.MID: 1}), proj("P3", ProjectPhase.EXECUTION, {Grade.MID: 1})]
    graph = MemoryGraph.build(Dataset(people=people, projects=projects, coworks=[], reviews=[]), parsed=[])
    S = np.array([[0.1, 0.5], [0.9, 0.1], [0.2, 0.2]])     # p1 (bench) fits P1 far better than p0
    current = [CurrentAssignment(person_id="p0", project_id="P1", alloc=0.5),
               CurrentAssignment(person_id="p0", project_id="P3", alloc=0.5)]
    params = MilpParams(min_alloc=0.3, time_limit=30, gap=0.0, solver="highs")
    (row,) = compare_move_budgets(graph, S, np.zeros((3, 3)), params, current, ks=(1,))
    for m in row["diff"]["moved"]:
        assert m["to"], f"{m['person_id']} was taken off {m['from']} and benched"


def test_kept_assignments_keep_their_monthly_allocation():
    """Monthly allocation mode: a kept assignment keeps its current allocation (0.5) in every month (alloc_vars path).
    Without the pin the solver raises it, since min_alloc 0.3 leaves room and more allocation scores more."""
    graph, S, C, params = _case()
    monthly = params.model_copy(update={"allocation_mode": "monthly", "min_alloc": 0.3})
    half = [CurrentAssignment(person_id=c.person_id, project_id=c.project_id, alloc=0.5) for c in CURRENT]
    (row,) = compare_move_budgets(graph, S, C, monthly, half, ks=(1,))
    assert row["accepted"]
    moved = {m["person_id"] for m in row["diff"]["moved"]}
    kept = [e for e in row["entries"] if e["project_id"] == "P1" and e["person_id"] in {"p0", "p1", "p2"} - moved]
    assert len(kept) >= 2
    for e in kept:
        assert e["alloc"] == 0.5
        assert all(abs(v - 0.5) < 1e-9 for v in (e.get("monthly_alloc") or {}).values())


def test_numeric_budget_fits_the_allocation_and_screening_still_matches(tmp_path):
    from api.settings import PlacementSettings
    from core.evaluate.staffing_sim import rank_candidates
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.ingest.org_profile import generate_org_bundle
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(generate_org_bundle(tmp_path / "b", 100, seed=2026, scenario="operating"))
    ds, parsed = to_dataset(b, rep)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    entries = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current]
    fast = rank_candidates(graph, S, C, params, entries, "J007", budget_add=600, top=5)
    full = rank_candidates(graph, S, C, params, entries, "J007", budget_add=600, top=5, screen=None)
    assert [(r["person_id"], r["alloc"]) for r in fast] == [(r["person_id"], r["alloc"]) for r in full]
    assert any(r["alloc"] < 1.0 for r in full)                    # allocations were fitted to the small budget
    assert all(not any(code == "budget" for code, _ in r["new_violations"]) for r in full)
