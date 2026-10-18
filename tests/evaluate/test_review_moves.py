"""인사팀 검토 자료의 옮겨 온 인력 사실(core.evaluate.review_moves, 2026-10-19)."""
from pathlib import Path

import pytest

from core.evaluate.review_moves import mover_effects, target_changes
from core.optimize.types import AssignEntry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def world():
    from api.settings import PlacementSettings
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(ROOT / "demo" / "org-n100-operating")
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    current = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current]
    placed = {c.person_id for c in ds.current}
    bench = sorted(p.id for p in ds.people if p.id not in placed)
    return dict(g=g, S=eng.skill_matrix({}), C=eng.synergy_matrix(), params=params, kg=build_kg(b, ds, parsed),
                current=current, bench=bench)


def _run(w, before, after, target="J014"):
    return mover_effects(w["g"], w["S"], w["C"], w["params"], w["kg"], before, after, target)


def _team(entries, j):
    return [e for e in entries if e.project_id == j]


def test_pull_one_person_and_one_bench(world):
    before = world["current"]
    pulled = _team(before, "J001")[0]
    b1 = world["bench"][0]
    after = [e for e in before if e is not pulled] + [AssignEntry(person_id=pulled.person_id, project_id="J014", alloc=1.0),
                                                     AssignEntry(person_id=b1, project_id="J014", alloc=1.0)]
    assert target_changes(before, after) == ["J014"]
    out = _run(world, before, after)
    by = {j["person_id"]: j for j in out["joined"]}
    assert by[b1]["origin"] == "bench" and by[b1]["sources"] == []
    m = by[pulled.person_id]
    assert m["origin"] == "moved" and len(m["sources"]) == 1
    src = m["sources"][0]
    assert src["project_id"] == "J001" and src["left"] is True and src["alloc_after"] == 0.0
    assert src["score_after"] < src["score_before"]          # 원래 사업은 잃는다
    assert src["backfill"] == []
    assert out["target_gain"]["score_after"] > out["target_gain"]["score_before"]


def test_backfill_and_partial_reduction(world):
    before = world["current"]
    pulled = _team(before, "J002")[0]
    b2 = world["bench"][1]
    after = ([e for e in before if e is not pulled]
             + [AssignEntry(person_id=pulled.person_id, project_id="J002", alloc=0.5),
                AssignEntry(person_id=pulled.person_id, project_id="J014", alloc=0.5),
                AssignEntry(person_id=b2, project_id="J002", alloc=1.0)])
    assert target_changes(before, after) == ["J002", "J014"]
    src = {j["person_id"]: j for j in _run(world, before, after)["joined"]}[pulled.person_id]["sources"][0]
    assert src["left"] is False and src["alloc_before"] == 1.0 and src["alloc_after"] == 0.5
    assert src["backfill"] == [{"person_id": b2, "grade": src["backfill"][0]["grade"], "from_bench": True}]


def test_mover_with_two_sources(world):
    base = [e for e in world["current"]]
    p = _team(base, "J003")[0]
    # 겸직자로 만든 전 배치: J003 0.5 + J004 0.5 -> 둘 다 빠져 J014로
    before = [e for e in base if e is not p] + [AssignEntry(person_id=p.person_id, project_id="J003", alloc=0.5),
                                                AssignEntry(person_id=p.person_id, project_id="J004", alloc=0.5)]
    after = [e for e in before if e.person_id != p.person_id] + [AssignEntry(person_id=p.person_id, project_id="J014", alloc=1.0)]
    m = _run(world, before, after)["joined"][0]
    assert m["origin"] == "moved" and [s["project_id"] for s in m["sources"]] == ["J003", "J004"]


def test_unchanged_side_job_is_not_a_source(world):
    base = world["current"]
    p = _team(base, "J005")[0]
    before = [e for e in base if e is not p] + [AssignEntry(person_id=p.person_id, project_id="J005", alloc=0.5)]
    after = before + [AssignEntry(person_id=p.person_id, project_id="J014", alloc=0.5)]
    m = _run(world, before, after)["joined"][0]
    assert m["origin"] == "partly_free" and m["sources"] == []
