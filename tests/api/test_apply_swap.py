"""K10: 검토한 교체를 '적용'한 명단을 같은 기준(현행 MILP 목적·제약)으로 다시 계산한다.

서버는 명단을 저장하지 않는다(stateless). 적용 상태는 웹이 들고 PDF에 실어 보낸다.
교체 규칙은 /api/whatif와 같은 함수를 쓰므로, 검토한 교체와 적용한 교체가 다를 수 없다.
"""
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
def tiny(tmp_path):
    people = [_person("p0", python=2), _person("p1", python=4),
              _person("p2", avail=0.3), _person("p3", grade=Grade.SENIOR),
              _person("p4", rate=50_000)]
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


def _apply(client, entries, out, inn, project="j0", **extra):
    return client.post("/api/plans/apply-swap", json={
        "entries": [{"person_id": p, "project_id": j, "alloc": a} for p, j, a in entries],
        "swap": {"out_person_id": out, "in_person_id": inn, "project_id": project},
        "weights": {}, **extra})


def test_apply_returns_swapped_roster_with_full_reevaluation(tiny):
    res = _apply(tiny, [("p0", "j0", 0.5), ("p1", "j1", 0.2)], "p0", "p3")
    assert res.status_code == 200, res.text
    body = res.json()
    roster = {(e["person_id"], e["project_id"]): e["alloc"] for e in body["entries"]}
    assert roster == {("p3", "j0"): 0.5, ("p1", "j1"): 0.2}      # 같은 투입률로 교체
    ev = body["evaluation"]
    assert set(ev["objective"]) == {"skill", "synergy", "overfamiliarity", "unfilled", "total"}
    # j0은 중급 1명을 요구하는데 고급(p3)이 들어갔다 -> 중급 미충원이 명단 전체 평가에 남는다.
    assert {"project_id": "j0", "grade": "중급", "missing": 1} in ev["shortfalls"]
    assert "j0:중급:1명 미충원" in body["unfilled"]
    assert body["feasible"] is True                                 # 미충원은 위반이 아니다
    assert 0.0 <= body["fulfillment"] <= 1.0
    assert body["optimization_ratio"] >= 0.0


def test_apply_delta_matches_whatif_for_the_same_swap(tiny):
    """검토(what-if)와 적용이 같은 함수·같은 기준으로 계산된다."""
    entries = [("p0", "j0", 0.5)]
    w = tiny.post("/api/whatif", json={
        "entries": [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}],
        "swap": {"out_person_id": "p0", "in_person_id": "p1", "project_id": "j0"},
        "weights": {}}).json()
    a = _apply(tiny, entries, "p0", "p1").json()
    assert a["objective_delta"] == pytest.approx(w["objective_delta"])
    assert a["evaluation"]["objective"] == pytest.approx(w["after"])


def test_apply_keeps_violations_of_the_whole_roster(tiny):
    """예산을 넘기는 교체도 적용은 된다(사람이 결정) -- 대신 위반이 명확히 남는다."""
    res = _apply(tiny, [("p1", "j1", 0.5)], "p1", "p4", project="j1")
    body = res.json()
    assert res.status_code == 200
    assert body["feasible"] is False
    codes = {v["code"] for v in body["evaluation"]["violations"]}
    assert codes and all(isinstance(c, str) for c in codes)


@pytest.mark.parametrize("out, inn, project, status", [
    ("p9", "p1", "j0", 404),        # 없는 사람
    ("p0", "p1", "j9", 404),        # 없는 프로젝트
    ("p1", "p2", "j0", 422),        # 명단에 없는 교체 대상
    ("p0", "p0", "j0", 422),        # 이미 그 프로젝트에 있는 사람
])
def test_invalid_swaps_are_rejected_like_whatif(tiny, out, inn, project, status):
    assert _apply(tiny, [("p0", "j0", 0.5)], out, inn, project).status_code == status


def test_apply_uses_sent_milp_params(tiny):
    a = _apply(tiny, [("p0", "j0", 0.5)], "p0", "p3", milp_params={"slack_penalty": 1.0}).json()
    b = _apply(tiny, [("p0", "j0", 0.5)], "p0", "p3").json()
    assert a["evaluation"]["objective"]["unfilled"] != b["evaluation"]["objective"]["unfilled"]


def test_apply_rejects_stale_dataset_version(tiny):
    res = _apply(tiny, [("p0", "j0", 0.5)], "p0", "p1", dataset_version="0" * 64)
    assert res.status_code == 409


# --- Codex 리뷰 1라운드 반영 ----------------------------------------------

def test_violating_roster_has_no_optimization_ratio_and_carries_warnings(tiny):
    """상한은 제약을 지키는 배치에만 의미가 있다 -- 위반 명단은 산정하지 않는다."""
    body = _apply(tiny, [("p1", "j1", 0.5)], "p1", "p4", project="j1").json()
    assert body["feasible"] is False
    assert body["optimization_ratio"] is None
    assert body["warnings"] and all(isinstance(w, str) for w in body["warnings"])


def _report_ready(client, monkeypatch, tmp_path):
    import api.routes.report as report_route
    index = tmp_path / "index.html"
    index.write_text("x")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    seen = []

    async def fake_render(payload, *args, **kwargs):
        seen.append(payload)
        return b"%PDF-fake"

    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    return seen


_REPORT = {"plan_label": "A", "objective": 999.0, "fulfillment": 1.0, "optimization_ratio": 1.0,
           "entries": [{"person_id": "p9", "project_id": "j9", "alloc": 1.0}]}


def test_report_replays_applied_swaps_and_ignores_client_numbers(tiny, monkeypatch, tmp_path):
    """PDF의 적용 이력·명단·지표·위반은 서버가 원 명단에서 다시 계산한다(클라이언트 값 무시)."""
    seen = _report_ready(tiny, monkeypatch, tmp_path)
    res = tiny.post("/api/report", json={
        **_REPORT,
        "base_entries": [{"person_id": "p0", "project_id": "j0", "alloc": 0.5},
                         {"person_id": "p1", "project_id": "j1", "alloc": 0.5}],
        "applied_swaps": [{"out_person_id": "p0", "in_person_id": "p3", "project_id": "j0"},
                          {"out_person_id": "p1", "in_person_id": "p4", "project_id": "j1"}]})
    assert res.status_code == 200, res.text
    p = seen[0]
    assert {(e["person_id"], e["project_id"]) for e in p["entries"]} == {("p3", "j0"), ("p4", "j1")}
    assert p["objective"] != 999.0
    assert [s["in_person_id"] for s in p["applied_swaps"]] == ["p3", "p4"]
    assert p["applied_swaps"][1]["feasible"] is False and p["applied_swaps"][1]["warnings"]
    assert p["applied_violations"]                          # 최종 명단의 예산 위반
    assert p["optimization_ratio"] is None
    direct = _apply(tiny, [("p0", "j0", 0.5), ("p1", "j1", 0.5)], "p0", "p3").json()
    assert p["applied_swaps"][0]["objective_delta"] == pytest.approx(direct["objective_delta"])


def test_report_rejects_applied_swaps_without_base_or_invalid_swap(tiny, monkeypatch, tmp_path):
    _report_ready(tiny, monkeypatch, tmp_path)
    swap = {"out_person_id": "p0", "in_person_id": "p3", "project_id": "j0"}
    assert tiny.post("/api/report", json={**_REPORT, "applied_swaps": [swap]}).status_code == 422
    bad = tiny.post("/api/report", json={
        **_REPORT, "base_entries": [{"person_id": "p1", "project_id": "j1", "alloc": 0.5}],
        "applied_swaps": [swap]})
    assert bad.status_code == 422                             # 원 명단에 없는 교체 대상


def test_report_without_applied_swaps_keeps_client_plan(tiny, monkeypatch, tmp_path):
    seen = _report_ready(tiny, monkeypatch, tmp_path)
    assert tiny.post("/api/report", json=_REPORT).status_code == 200
    assert seen[0]["objective"] == 999.0 and seen[0]["applied_swaps"] == []


# --- Codex 리뷰 2라운드 반영 ----------------------------------------------

def test_plan_token_round_trip_and_tamper(tiny, monkeypatch, tmp_path):
    """optimize가 붙인 서명으로 원 플랜을 확인한다. 명단을 바꾸면 422, 서명이 없으면 '미검증'."""
    import json as _json

    seen = _report_ready(tiny, monkeypatch, tmp_path)
    plans = []
    with tiny.stream("POST", "/api/optimize", json={"weights": {}, "n_alternatives": 0}) as res:
        for line in res.iter_lines():
            if line.startswith("data:") and '"label"' in line:
                plans.append(_json.loads(line[5:]))
    plan = plans[0]
    assert plan["plan_token"]
    base = {"plan_label": plan["label"], "objective": plan["objective"], "fulfillment": 1.0,
            "optimization_ratio": 1.0, "entries": plan["entries"], "weights": {}}
    ok = tiny.post("/api/report", json={**base, "plan_token": plan["plan_token"]})
    assert ok.status_code == 200, ok.text
    assert seen[-1]["plan_provenance"] == "verified"

    forged = [dict(e, alloc=0.2) for e in plan["entries"]] or \
        [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}]
    bad = tiny.post("/api/report", json={**base, "entries": forged, "plan_token": plan["plan_token"]})
    assert bad.status_code == 422
    other_weights = tiny.post("/api/report", json={**base, "weights": {"Python": 5},
                                                   "plan_token": plan["plan_token"]})
    assert other_weights.status_code == 422
    assert tiny.post("/api/report", json=base).status_code == 200
    assert seen[-1]["plan_provenance"] == "unverified"


def test_report_caps_replayed_swaps(tiny, monkeypatch, tmp_path):
    _report_ready(tiny, monkeypatch, tmp_path)
    # 서로 되돌리는 유효한 교체 52건 -- 상한이 없으면 200이 된다.
    swaps = [{"out_person_id": "p0", "in_person_id": "p3", "project_id": "j0"},
             {"out_person_id": "p3", "in_person_id": "p0", "project_id": "j0"}] * 26
    body = {**_REPORT, "base_entries": [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}]}
    assert tiny.post("/api/report", json={**body, "applied_swaps": swaps}).status_code == 422
    assert tiny.post("/api/report", json={**body, "applied_swaps": swaps[:50]}).status_code == 200


def test_report_replay_solves_upper_bound_once(tiny, monkeypatch, tmp_path):
    """교체가 여러 건이어도 LP 상한은 요청당 한 번만 푼다."""
    import api.routes.plans as plans_route
    import api.routes.report as report_route
    _report_ready(tiny, monkeypatch, tmp_path)
    calls = []
    real = report_route._skill_relaxation_upper_bound
    counting = lambda *a: calls.append(1) or real(*a)          # noqa: E731
    monkeypatch.setattr(report_route, "_skill_relaxation_upper_bound", counting)
    monkeypatch.setattr(plans_route, "_skill_relaxation_upper_bound", counting)
    swaps = [{"out_person_id": "p0", "in_person_id": "p3", "project_id": "j0"},
             {"out_person_id": "p3", "in_person_id": "p0", "project_id": "j0"}] * 3
    res = tiny.post("/api/report", json={
        **_REPORT, "base_entries": [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}],
        "applied_swaps": swaps})
    assert res.status_code == 200, res.text
    assert calls == [1]
