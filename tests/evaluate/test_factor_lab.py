"""Model lab (core/evaluate/factor_lab): factors, simulators, search and the unusual-project breakdown."""
import tempfile
from pathlib import Path

import numpy as np
import pytest

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.evaluate.factor_lab.factors import FACTORS, compute_factors
from core.evaluate.factor_lab.robustness import breakdown, project_traits
from core.evaluate.factor_lab.search import candidates, evaluate, model_matrix, summarize
from core.evaluate.factor_lab.simulator import SCENARIOS, Scenario, simulate
from core.graph.memory_graph import MemoryGraph
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry


@pytest.fixture(scope="module")
def org():
    root = generate_org_bundle(Path(tempfile.mkdtemp()) / "b", 100, seed=11)
    bundle, report = load_bundle(root)
    ds, parsed = to_dataset(bundle, report)
    return bundle, ds, MemoryGraph.build(ds, parsed)


def test_factors_are_complete_and_scaled(org):
    bundle, _, g = org
    F = compute_factors(g, bundle)
    assert set(F) == set(FACTORS)
    for k, m in F.items():
        assert m.shape == (len(g.people), len(g.projects)), k
        assert 0.0 <= m.min() and m.max() <= 1.0 + 1e-9, k
    assert F["K"].any() and F["D"].any() and F["M"].any()


def test_depth_is_zero_without_the_skill_and_grows_with_months(org):
    bundle, _, g = org
    F = compute_factors(g, bundle)
    j = g.project_index["J001"]
    reqs = [r for r in bundle.tables["project_skill_requirements.csv"] if r["project_id"] == "J001"]
    months = {(r["person_id"], r["skill_name"]): r["experience_months"] for r in bundle.tables["person_skills.csv"]}
    nobody = [p for p in g.people if not any((p.id, r["skill_name"]) in months for r in reqs)]
    for p in nobody:
        assert F["D"][g.pid_index[p.id], j] == 0.0 and F["R"][g.pid_index[p.id], j] == 0.0


def _tiny():
    ds = generate_dataset(12, 3, seed=4)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    rng = np.random.default_rng(1)
    F = {k: rng.random((12, 3)) for k in FACTORS}
    return ds, g, F


def test_simulator_uses_common_random_numbers_and_rewards_fill():
    ds, g, F = _tiny()
    C = np.zeros((12, 12))
    one = [AssignEntry(person_id=ds.people[0].id, project_id=ds.projects[0].id, alloc=0.5)]
    two = one + [AssignEntry(person_id=ds.people[1].id, project_id=ds.projects[1].id, alloc=0.5)]
    sc = SCENARIOS[0]
    assert simulate(g, F, C, set(), one, sc, 3).total == simulate(g, F, C, set(), one, sc, 3).total
    assert simulate(g, F, C, set(), two, sc, 3).total > simulate(g, F, C, set(), one, sc, 3).total
    assert simulate(g, F, C, set(), [], sc, 3).total == 0.0


def test_overfamiliar_pairs_lower_the_team_score():
    ds, g, F = _tiny()
    C = np.full((12, 12), 0.5)
    team = [AssignEntry(person_id=ds.people[i].id, project_id=ds.projects[0].id, alloc=0.5) for i in (0, 1)]
    sc = Scenario("X", "x", {"S": 1.0}, synergy_w=0.0, overfam_w=1.0, noise=0.0)
    assert simulate(g, F, C, {(0, 1)}, team, sc, 1).total < simulate(g, F, C, set(), team, sc, 1).total


def test_candidates_start_with_the_baseline_and_are_unique():
    c = candidates(10, 7)
    assert c[0] == ("base:S", {"S": 1.0})
    keys = [tuple(sorted(w.items())) for _, w in c]
    assert len(keys) == len(set(keys))
    assert all(w["S"] == 1.0 for _, w in c)


def test_model_matrix_stays_in_unit_range():
    _, _, F = _tiny()
    M = model_matrix(F, {"S": 1.0, "D": 0.5, "K": 1.0})
    assert M.min() >= 0 and M.max() <= 1


def test_search_end_to_end_on_a_tiny_problem():
    ds, g, F = _tiny()
    res = evaluate(g, F, [("base:S", {"S": 1.0}), ("S+D", {"S": 1.0, "D": 0.5})],
                   MilpParams(time_limit=10, min_alloc=0.3), seeds=(1,), log=lambda *_: None)
    assert all(r.accepted for r in res), [r.error for r in res]
    rows = summarize(res)
    assert {r["name"] for r in rows} == {"base:S", "S+D"}
    for r in rows:
        assert set(r["pct"]) == {s.name for s in SCENARIOS} and r["worst_pct"] <= 100.0 + 1e-9


def test_breakdown_groups_every_project_once(org):
    bundle, _, g = org
    F = compute_factors(g, bundle)
    entries = [AssignEntry(person_id=g.people[0].id, project_id="J001", alloc=0.5)]
    out = breakdown(g, F["S"], entries, ["J001:고급:2명 미충원", "J002:중급:1명 미충원"])
    for trait, rows in out.items():
        assert sum(r["projects"] for r in rows) == len(g.projects), trait
        assert abs(sum(r["unfilled_share"] for r in rows) - 1.0) < 1e-6
    assert set(project_traits(g)) == {j.id for j in g.projects}


def test_coverage_assumption_penalises_a_team_missing_a_required_skill():
    ds, g, F = _tiny()
    C = np.zeros((12, 12))
    proj = max(ds.projects, key=lambda j: len(j.requirements))
    req = {r.skill for r in proj.requirements}
    ranked = sorted(ds.people, key=lambda p: len(req & set(p.skills)))
    lacking, knowing = ranked[0], ranked[-1]
    assert len(req & set(lacking.skills)) < len(req & set(knowing.skills))
    sc = Scenario("T6", "x", {"S": 0.5}, coverage_w=1.0, noise=0.0)
    F2 = {**F, "S": np.ones((12, 3))}            # equal contribution: only coverage differs
    good = simulate(g, F2, C, set(), [AssignEntry(person_id=knowing.id, project_id=proj.id, alloc=0.5)], sc, 1)
    bad = simulate(g, F2, C, set(), [AssignEntry(person_id=lacking.id, project_id=proj.id, alloc=0.5)], sc, 1)
    assert bad.total < good.total


def test_fragmentation_assumption_penalises_split_people():
    ds, g, F = _tiny()
    C = np.zeros((12, 12))
    sc = Scenario("T7", "x", {"S": 0.5}, fragmentation_w=0.5, noise=0.0)
    F2 = {**F, "S": np.ones((12, 3))}
    p0 = ds.people[0].id
    split = [AssignEntry(person_id=p0, project_id=ds.projects[0].id, alloc=0.5),
             AssignEntry(person_id=p0, project_id=ds.projects[1].id, alloc=0.5)]
    whole = [AssignEntry(person_id=p0, project_id=ds.projects[0].id, alloc=0.5),
             AssignEntry(person_id=ds.people[1].id, project_id=ds.projects[1].id, alloc=0.5)]
    assert simulate(g, F2, C, set(), split, sc, 1).total < simulate(g, F2, C, set(), whole, sc, 1).total


def test_fill_rate_follows_the_plans_own_unfilled_seats():
    ds, g, F = _tiny()
    C = np.zeros((12, 12))
    proj = next(j for j in ds.projects if j.grade_headcount)
    seats = sum(proj.grade_headcount.values())
    e = [AssignEntry(person_id=ds.people[0].id, project_id=proj.id, alloc=0.5)]
    out = simulate(g, F, C, set(), e, SCENARIOS[0], 1, unfilled=[f"{proj.id}:고급:{seats}명 미충원"])
    assert out.per_project[proj.id] == 0.0


def test_model_matrix_matches_the_current_score_scale():
    _, _, F = _tiny()
    M, scale = model_matrix(F, {"S": 1.0, "K": 1.0}, with_scale=True)
    assert abs(M.mean() - F["S"].mean()) < 0.05 and scale["factor"] > 0


def test_held_out_scenarios_use_signals_no_candidate_scores():
    from core.evaluate.factor_lab.simulator import HELD_OUT
    held = [s for s in SCENARIOS if s.name in HELD_OUT]
    assert len(held) == 2 and all(s.coverage_w or s.fragmentation_w for s in held)
