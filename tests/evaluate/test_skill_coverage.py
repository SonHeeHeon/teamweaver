"""사업 요구 기술별 충족 인원(core.evaluate.skill_coverage, 2026-10-23) -- 검토 자료 근거(project_evidence)와 같은 수인가."""
from pathlib import Path

import pytest

from core.evaluate.skill_coverage import skill_coverage
from core.optimize.types import AssignEntry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def world():
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.kg import build_kg
    b, rep = load_bundle(ROOT / "demo" / "org-n100-operating")
    ds, parsed = to_dataset(b, rep)
    current = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in ds.current]
    return dict(g=MemoryGraph.build(ds, parsed), kg=build_kg(b, ds, parsed), current=current)


def test_counts_match_review_evidence_for_every_running_project(world):
    from core.kg.views import project_evidence
    g, kg = world["g"], world["kg"]
    for j in sorted({e.project_id for e in world["current"]}):
        mine = {r["skill"]: r["met"] for r in skill_coverage(g, j, world["current"])}
        ev = {r["skill"]: r["met_by"] for r in project_evidence(kg, j, world["current"])["requirements"]}
        assert mine == ev, j


def test_adding_a_person_moves_met_and_below_but_never_both():
    from types import SimpleNamespace as NS
    people = [NS(id="A", skills={"ETL": 3}), NS(id="B", skills={"ETL": 1, "SQL": 4}), NS(id="C", skills={})]
    req = [NS(skill="ETL", min_level=2, headcount=2), NS(skill="SQL", min_level=2, headcount=1)]
    g = NS(projects=[NS(requirements=req)], project_index={"J1": 0}, people=people, pid_index={p.id: i for i, p in enumerate(people)})
    team = [AssignEntry(person_id="A", project_id="J1", alloc=1.0), AssignEntry(person_id="C", project_id="J9", alloc=1.0)]
    before = {r["skill"]: r for r in skill_coverage(g, "J1", team)}
    assert (before["ETL"]["met"], before["ETL"]["headcount"], before["SQL"]["met"]) == (1, 2, 0)   # 다른 사업 배치(C)는 세지 않는다
    after = {r["skill"]: r for r in skill_coverage(g, "J1", team + [AssignEntry(person_id="B", project_id="J1", alloc=1.0)])}
    assert after["ETL"]["met_by"] == ["A"] and after["ETL"]["below_by"] == ["B"]                  # 레벨이 모자라면 충족이 아니다
    assert after["SQL"]["met_by"] == ["B"]
