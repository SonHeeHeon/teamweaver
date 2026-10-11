"""운영 중 편성·진행 사업 보강·단순 규칙 대비 API(claude-a 요청 docs/requests/2026-10-06-operating-staffing-ui.md).

계산의 정확성은 claude-a의 tests/test_incremental.py가 본다(전수 탐색 대조 등). 여기서는 API 배선만 본다:
현재 배치·잠금이 넘어가는가, SSE 형식, 409·422, 직렬화, 시연 묶음 고르기."""
import json

import pytest

from api.datasets import build_active
from core.domain.models import CurrentAssignment, Dataset, Grade, Project, ProjectPhase, Sector, SkillRequirement
from tests.phase0.factories import _person

GRADES = {"p0": Grade.MID, "p1": Grade.MID, "p2": Grade.JUNIOR, "p3": Grade.MID, "p4": Grade.SENIOR, "p5": Grade.SENIOR}
CURRENT = [CurrentAssignment(person_id="p0", project_id="P1", alloc=1.0, locked=True),
           CurrentAssignment(person_id="p1", project_id="P1", alloc=1.0),
           CurrentAssignment(person_id="p2", project_id="P1", alloc=1.0)]
FAST = {"min_alloc": 1.0, "time_limit": 30, "gap": 0.0, "max_pairs": 10, "pair_keep_ratio": 1.0}


def _operating_dataset():
    people = [_person(p, g) for p, g in GRADES.items()]
    projects = [
        Project(id="P1", name="진행 중", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=0, end_month=5,
                grade_headcount={Grade.MID: 2, Grade.JUNIOR: 1},
                requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)], monthly_budget=100_000),
        Project(id="P2", name="신규 제안", sector=Sector.INTERNAL, phase=ProjectPhase.PROPOSAL, start_month=1, end_month=5,
                grade_headcount={Grade.JUNIOR: 1, Grade.SENIOR: 1},
                requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)], monthly_budget=100_000),
    ]
    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[], current=CURRENT)
    return build_active(ds, [], dataset_id="op-mini", version="o" * 64, source="demo-bundle", synthetic=True,
                        judge=False, manifest={"scenario": "operating", "bench": ["p3", "p4", "p5"],
                                               "proposals": ["P2"]})


@pytest.fixture
def op_client(client):
    old = client.app.state.dataset
    client.app.state.dataset = _operating_dataset()
    old.retire()
    return client


def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_state_says_when_there_is_no_current_roster(client):
    st = client.get("/api/operating/state").json()
    assert st["available"] is False and st["hint"] and st["current"] == []


def test_state_lists_current_roster_bench_and_proposals(op_client):
    st = op_client.get("/api/operating/state").json()
    assert st["available"] is True and st["scenario"] == "operating"
    assert st["bench"] == ["p3", "p4", "p5"] and st["proposals"] == ["P2"]
    assert {c["person_id"] for c in st["current"]} == {"p0", "p1", "p2"}
    assert any(c["locked"] for c in st["current"])
    assert {p["id"] for p in st["projects"]} == {"P1", "P2"} and "NOT_CALIBRATED" in st["note"]


def test_compare_streams_start_rows_and_done(op_client):
    v = op_client.get("/api/operating/state").json()["dataset_version"]
    res = op_client.post("/api/operating/compare", json={"dataset_version": v, "milp_params": FAST, "ks": [1, 0]})
    assert res.status_code == 200
    events = _sse(res.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and kinds.count("row") == 2
    rows = [d for k, d in events if k == "row"]
    assert [r["k"] for r in rows] == [0, 1]
    for r in rows:
        assert r["accepted"] and {"quality", "unfilled_seats", "diff", "project_change_vs_k0", "entries",
                                  "proven_optimal"} <= set(r)
    assert rows[1]["quality_gain_vs_k0"] is not None
    # 잠긴 p0은 어떤 K에서도 옮겨지지 않는다
    assert all(m["person_id"] != "p0" for r in rows for m in r["diff"]["moved"])


def test_compare_refuses_stale_screens_and_planning_data(op_client, client):
    assert op_client.post("/api/operating/compare", json={"dataset_version": "x" * 64}).status_code == 409


def test_compare_needs_a_current_roster(client):
    assert client.post("/api/operating/compare", json={}).status_code == 422


def test_candidates_rank_people_for_a_project(op_client):
    res = op_client.post("/api/staffing/candidates", json={"milp_params": FAST, "project_id": "P1", "top": 5})
    assert res.status_code == 200, res.text
    rows = res.json()["candidates"]
    assert rows and {"person_id", "delta_total", "delta", "skill_fit", "monthly_cost", "source"} <= set(rows[0])
    assert all(r["person_id"] not in {"p0", "p1", "p2"} for r in rows)      # 이미 팀에 있는 사람은 후보가 아니다


def test_candidates_reject_unknown_project(op_client):
    assert op_client.post("/api/staffing/candidates", json={"project_id": "NOPE"}).status_code == 422


def test_simulate_scores_adds_and_removes_and_protects_locks(op_client):
    body = {"milp_params": FAST, "project_id": "P2", "adds": [{"person_id": "p4", "project_id": "P2", "alloc": 1.0}],
            "removes": [["p2", "P1"]], "extra_seats": {}, "budget_add": 0}
    res = op_client.post("/api/staffing/simulate", json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert set(out["delta"]) == {"total", "skill", "synergy", "overfamiliarity", "unfilled"}
    assert {"person_id": "p4", "project_id": "P2", "alloc": 1.0} in out["entries"]
    assert all(not (e["person_id"] == "p2" and e["project_id"] == "P1") for e in out["entries"])
    locked = {**body, "removes": [["p0", "P1"]]}
    assert op_client.post("/api/staffing/simulate", json=locked).status_code == 422


def test_best_additions_returns_a_scored_plan(op_client):
    res = op_client.post("/api/staffing/best", json={"milp_params": FAST, "project_id": "P1", "n": 1})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["accepted"] is True and {"before", "after", "delta", "diff", "added_cost", "entries"} <= set(out)


def test_baseline_card_compares_with_the_simple_rule(op_client):
    entries = [c.model_dump(exclude={"locked"}) for c in CURRENT]
    res = op_client.post("/api/baseline", json={"milp_params": FAST, "entries": entries})
    assert res.status_code == 200, res.text
    out = res.json()
    assert {"rule", "optimized", "baseline", "difference"} <= set(out)
    assert "avg_seat_fit" in out["optimized"] and "NOT_CALIBRATED" in out["note"]


def test_plan_events_carry_confidence_fields(small_graph_client):
    res = small_graph_client.post("/api/optimize", json={"n_alternatives": 0, "milp_params": {"time_limit": 20}})
    plan = next(d for k, d in _sse(res.text) if k == "plan")
    assert {"termination", "best_bound", "gap_allowed"} <= set(plan)
    assert plan["gap_allowed"] == pytest.approx(0.01)        # Plan A는 min(설정 5%, 1%)로 푼다(리뷰 S5)


# --- 시연 묶음 고르기 -------------------------------------------------------------------------

@pytest.fixture
def demo_dir(tmp_path, monkeypatch):
    from core.ingest.synthetic import generate_bundle
    root = tmp_path / "demo"
    generate_bundle(root / "mini", 12, 3, 5)
    (root / "not-a-bundle").mkdir()
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(root))
    return root


def test_demo_list_only_shows_bundles(client, demo_dir):
    demos = client.get("/api/datasets/demos").json()
    assert [d["name"] for d in demos] == ["mini"] and demos[0]["people"] == 12


def test_choosing_a_demo_switches_and_survives_restart(client, demo_dir):
    res = client.post("/api/datasets/demo", json={"name": "mini"})
    assert res.status_code == 200, res.text
    info = res.json()
    assert info["source"] == "demo-bundle" and info["people"] == 12 and info["judge_endpoint"]
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as again:
        assert again.get("/api/datasets/active").json()["people"] == 12
    # 되돌리기는 고른 묶음도 잊는다
    assert client.post("/api/datasets/reset", json={}).json()["source"] == "fixture"
    with TestClient(app) as again:
        assert again.get("/api/datasets/active").json()["source"] == "fixture"


@pytest.mark.parametrize("name", ["../demo", "nope", "not-a-bundle"])
def test_choosing_an_unknown_demo_is_404(client, demo_dir, name):
    assert client.post("/api/datasets/demo", json={"name": name}).status_code == 404


def test_choosing_a_demo_needs_admin(client, demo_dir, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "t0ken")
    assert client.post("/api/datasets/demo", json={"name": "mini"}).status_code == 401



def test_broken_demo_changes_nothing(client, demo_dir, tmp_path):
    from tests.api.test_datasets import ZIP, _zip_dir
    from core.ingest.synthetic import generate_bundle
    up = client.post("/api/datasets", content=_zip_dir(generate_bundle(tmp_path / "u", 12, 3, 5)), headers=ZIP).json()
    (demo_dir / "mini" / "people.csv").write_text("broken\n", "utf-8")
    res = client.post("/api/datasets/demo", json={"name": "mini"})
    assert res.status_code == 422 and "읽지 못했다" in res.json()["detail"]
    active = client.get("/api/datasets/active").json()
    assert active["source"] == "upload" and active["content_version"] == up["dataset"]["content_version"]
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as again:                       # 보관본도 선택 기록도 그대로 -- 재기동해도 업로드
        assert again.get("/api/datasets/active").json()["source"] == "upload"


def test_compare_is_one_at_a_time(op_client):
    from api.routes import operating
    assert operating._COMPARE_SLOT.acquire(blocking=False)
    try:
        assert op_client.post("/api/operating/compare", json={}).status_code == 429
    finally:
        operating._COMPARE_SLOT.release()


def test_inputs_are_capped(op_client):
    assert op_client.post("/api/operating/compare", json={"ks": [4]}).status_code == 422
    many = [{"person_id": "p3", "project_id": "P1", "alloc": 1.0}] * 21
    assert op_client.post("/api/staffing/simulate", json={"project_id": "P1", "adds": many}).status_code == 422


def test_candidates_report_unfilled_as_seats(op_client):
    rows = op_client.post("/api/staffing/candidates", json={"milp_params": FAST, "project_id": "P1",
                                                             "include_pull": True}).json()["candidates"]
    assert all(isinstance(r["unfilled_seats_delta"], int) for r in rows)



def test_compare_slot_is_released_after_a_failing_calculation(op_client, monkeypatch):
    import core.evaluate.operating as op

    def boom(*a, **k):
        raise RuntimeError("solver exploded")
    monkeypatch.setattr(op, "compare_move_budgets", boom)
    res = op_client.post("/api/operating/compare", json={"milp_params": FAST, "ks": [0]})
    assert any(k == "error" for k, _ in _sse(res.text))
    from api.routes import operating
    assert operating._COMPARE_SLOT.acquire(blocking=False)          # 슬롯이 돌아왔다
    operating._COMPARE_SLOT.release()


def test_unknown_ids_in_sent_entries_are_422(op_client):
    body = {"project_id": "P1", "entries": [{"person_id": "ghost", "project_id": "P1", "alloc": 1.0}]}
    assert op_client.post("/api/staffing/candidates", json=body).status_code == 422


def test_compare_streams_each_k_as_it_finishes(op_client, monkeypatch):
    # K별 실시간 송출(2026-10-11): 끝난 K 행은 뒤 K가 실패해도 이미 흘러갔다(예전엔 다 끝난 뒤 한꺼번에 보냈다)
    import core.evaluate.operating as op

    def first_then_fail(*a, on_row=None, **k):
        on_row({"k": 0, "accepted": True, "elapsed_s": 0.1})
        raise RuntimeError("K=1 solver exploded")
    monkeypatch.setattr(op, "compare_move_budgets", first_then_fail)
    events = _sse(op_client.post("/api/operating/compare", json={"milp_params": FAST, "ks": [0, 1]}).text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "start" and "row" in kinds and kinds.index("row") < kinds.index("error")
    row = next(d for k, d in events if k == "row")
    assert row["k"] == 0 and "elapsed_total_s" in row and "dataset_version" in row
