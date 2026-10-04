"""What-if contract: the swap is re-scored with the full MILP objective and checked
against the MILP constraints (core.evaluate.plan_eval), on a hand-sized dataset."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.deps import get_graph, get_openai_client_or_none, get_sqlite_conn
from api.main import app
from core.domain.models import (
    Dataset, Grade, Person, Project, ProjectPhase, Sector, SkillRequirement,
)
from core.graph.memory_graph import MemoryGraph
from core.graph.sqlite_store import build_sqlite


def _person(pid, grade=Grade.MID, rate=1_000, avail=1.0, python=3):
    return Person(id=pid, name=pid.upper(), grade=grade, monthly_rate=rate,
                  skills={"Python": python}, availability=[avail] * 6)


def _project(pid, budget=10_000, headcount=None):
    return Project(id=pid, name=pid.upper(), sector=Sector.INTERNAL,
                   phase=ProjectPhase.EXECUTION, start_month=0, end_month=2,
                   grade_headcount=headcount or {Grade.MID: 1},
                   requirements=[SkillRequirement(skill="Python", min_level=4, headcount=1)],
                   monthly_budget=budget)


@pytest.fixture
def tiny_client(tmp_path):
    people = [_person("p0", python=2), _person("p1", python=4),
              _person("p2", avail=0.3), _person("p3", grade=Grade.SENIOR),
              _person("p4", rate=5_000)]
    projects = [_project("j0"), _project("j1", budget=2_000)]
    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    graph = MemoryGraph.build(ds, parsed=[])
    build_sqlite(ds, [], tmp_path / "tiny.db")
    conn = sqlite3.connect(tmp_path / "tiny.db", check_same_thread=False)
    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[get_sqlite_conn] = lambda: conn
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    conn.close()


def _swap(client, entries, out, inn, project="j0", **extra):
    return client.post("/api/whatif", json={
        "entries": [{"person_id": p, "project_id": j, "alloc": a} for p, j, a in entries],
        "swap": {"out_person_id": out, "in_person_id": inn, "project_id": project},
        "weights": {}, **extra})


def test_delta_is_full_objective_difference_with_breakdown(tiny_client):
    body = _swap(tiny_client, [("p0", "j0", 0.5)], "p0", "p1").json()
    # S = min(level/4, 1): p0 = 0.5, p1 = 1.0, alloc 0.5
    assert body["before"]["skill"] == pytest.approx(0.25)
    assert body["after"]["skill"] == pytest.approx(0.5)
    assert body["objective_delta"] == pytest.approx(
        body["after"]["total"] - body["before"]["total"])
    assert body["objective_delta"] == pytest.approx(0.25)
    assert body["feasible"] is True
    assert body["new_violations"] == [] and body["new_shortfalls"] == []


def test_swap_that_breaks_availability_is_reported_not_rejected(tiny_client):
    res = _swap(tiny_client, [("p0", "j0", 0.5)], "p0", "p2")
    assert res.status_code == 200
    body = res.json()
    assert body["feasible"] is False
    assert [(v["code"], v["location"]) for v in body["new_violations"]] == [
        ("availability", "p2:month0"), ("availability", "p2:month1"),
        ("availability", "p2:month2")]
    assert all(v["message"] for v in body["new_violations"])


def test_swap_to_other_grade_exposes_new_shortfall(tiny_client):
    body = _swap(tiny_client, [("p0", "j0", 0.5)], "p0", "p3").json()
    assert body["new_shortfalls"] == [{"project_id": "j0", "grade": "중급", "missing": 1}]
    # j1's 중급 slot is empty both before and after; the swap adds j0's
    assert body["before"]["unfilled"] == pytest.approx(-100.0)
    assert body["after"]["unfilled"] == pytest.approx(-200.0)
    assert body["objective_delta"] == pytest.approx((0.75 - 0.5) * 0.5 - 100.0)


def test_preexisting_violation_is_not_reported_as_new(tiny_client):
    # j1 budget 2,000; p4 costs 5,000 * 0.5 = 2,500 before the swap
    entries = [("p4", "j1", 0.5), ("p0", "j0", 0.5)]
    body = _swap(tiny_client, entries, "p0", "p1").json()
    assert body["new_violations"] == []
    assert body["feasible"] is False


def test_incoming_person_already_on_project_is_rejected(tiny_client):
    res = _swap(tiny_client, [("p0", "j0", 0.5), ("p1", "j0", 0.5)], "p0", "p1")
    assert res.status_code == 422


def test_entries_with_unknown_person_are_rejected(tiny_client):
    res = _swap(tiny_client, [("p0", "j0", 0.5), ("ghost", "j0", 0.5)], "p0", "p1")
    assert res.status_code == 422


def test_invalid_milp_params_are_rejected(tiny_client):
    res = _swap(tiny_client, [("p0", "j0", 0.5)], "p0", "p1", milp_params={"lam": "x"})
    assert res.status_code == 422


def test_milp_params_change_the_scoring_basis(tiny_client):
    base = _swap(tiny_client, [("p0", "j0", 0.5)], "p0", "p3").json()
    cheap = _swap(tiny_client, [("p0", "j0", 0.5)], "p0", "p3",
                  milp_params={"slack_penalty": 1.0}).json()
    assert base["after"]["unfilled"] == pytest.approx(-200.0)
    assert cheap["after"]["unfilled"] == pytest.approx(-2.0)
