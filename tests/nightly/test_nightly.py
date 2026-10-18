"""야간 배치(api/nightly.py): 예약 시각, 밤 한도, 낮 요청 기록, 밤 풀이 → 아침 즉시 응답, 재기동 뒤 다시 넣기·재채점 거절,
시연 미리 계산 보호, 멈춤, 점검 보고서."""
import json
import threading
import time
from datetime import datetime, time as dtime, timedelta

import pytest
from fastapi.testclient import TestClient

import api.nightly as nightly
from core.ingest.org_profile import generate_org_bundle
from core.optimize.types import PlanAssignment
from tests.nightly.helpers import current_entries, evaluated, run_now, sse, web_settings


# --- 순수 함수 ----------------------------------------------------------------------------------------------------

def test_window_parsing_and_next_start():
    assert nightly.parse_window("00:00-05:00") == (dtime(0, 0), dtime(5, 0))
    for bad in ("05:00-01:00", "1-2", "00:00", "25:00-26:00"):
        with pytest.raises(ValueError):
            nightly.parse_window(bad)
    win = (dtime(0, 0), dtime(5, 0))
    evening = datetime(2026, 10, 18, 22, 30)
    assert nightly.next_start(evening, win) == datetime(2026, 10, 19, 0, 0)
    # 시간대 안에서 켜지면 그날 밤은 돌지 않는다 -- 다음 날 시작
    inside = datetime(2026, 10, 19, 2, 0)
    assert nightly.next_start(inside, win) == datetime(2026, 10, 20, 0, 0)
    assert nightly.next_start(datetime(2026, 10, 19, 0, 0), win) == datetime(2026, 10, 20, 0, 0)
    win2 = (dtime(1, 30), dtime(4, 0))
    assert nightly.next_start(datetime(2026, 10, 19, 0, 10), win2) == datetime(2026, 10, 19, 1, 30)


def test_bad_window_setting_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(nightly.WINDOW_ENV, "nonsense")
    assert nightly.window() == (dtime(0, 0), dtime(5, 0))


def test_night_limit_rules():
    assert nightly.night_limit(30, 4, 4 * 3600) == 300           # 낮 × 10, 최소 300초
    assert nightly.night_limit(180, 4, 4 * 3600) == 1800         # 최대 30분
    assert nightly.night_limit(180, 4, 1000) == 225              # 남은 시간의 90%에 맞춰 줄인다(낮 한도보다는 길다)
    assert nightly.night_limit(180, 4, 1000 * 4) == 900
    assert nightly.night_limit(30, 4, 100) is None               # 낮 한도보다 짧으면 풀지 않는다


# --- 서버 준비 ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    op_b = generate_org_bundle(root / "org-n100-operating", 100, seed=11, scenario="operating")
    return root, op_b


@pytest.fixture
def boot(bundles, monkeypatch):
    root, op_b = bundles
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(op_b))
    return root, op_b


def _solve(report, prefix):
    return [s for s in report["solves"] if str(s.get("target", "")).startswith(prefix)]


# --- 밤 풀이 → 아침 즉시 ---------------------------------------------------------------------------------------------

def test_manual_run_prepares_and_the_morning_request_is_instant(boot, fake_solvers, fast_checks):
    from api.main import app
    with TestClient(app) as c:
        version = c.get("/api/datasets/active").json()["version"]
        report = run_now(app)
        default = _solve(report, "기본 요청")[0]
        assert default["status"] == "prepared" and default["installed"] is True
        assert default["night_time_limit"] >= default["day_time_limit"]
        assert fake_solvers["plans"] == [default["night_time_limit"]]          # 밤에는 시간 한도만 늘려 푼다
        op = _solve(report, "운영 중 비교")[0]
        assert op["status"] == "prepared" and op["installed"] is True
        assert fake_solvers["operating"] and fake_solvers["operating"][0] == op["night_time_limit"]

        body = {"weights": {}, "milp_params": web_settings(c), "n_alternatives": 3, "dataset_version": version}
        plans = [d for e, d in sse(c.post("/api/optimize", json=body)) if e == "plan"]
        assert len(plans) == 1 and plans[0]["cached"] is True and plans[0]["precomputed_at"]
        rows = [d for e, d in sse(c.post("/api/operating/compare",
                                          json={"milp_params": web_settings(c), "dataset_version": version})) if e == "row"]
        assert len(rows) == 4 and all(r["precomputed_at"] for r in rows)
        assert len(fake_solvers["plans"]) == 1                                  # 아침 요청은 다시 풀지 않았다

        st = c.get("/api/nightly").json()
        assert st["running"] is False and st["last"]["trigger"] == "manual"
        assert "미리 풀어 둠" in st["last"]["summary"]
        ids = {ch["id"] for ch in st["last"]["checks"]}
        assert {"data", "precomputed", "judgments", "plan_edits", "disk", "model", "pdf", "ai"} <= ids
        disk = [ch for ch in st["last"]["checks"] if ch["id"] == "disk"][0]
        assert disk["status"] == "ok"


def test_restart_reinstalls_and_a_tampered_record_is_refused(boot, fake_solvers, fast_checks, _isolated):
    from api.main import app
    with TestClient(app):
        run_now(app)
    with TestClient(app) as c:                                  # 재기동: 밤 결과를 다시 확인해 넣는다
        installed = c.get("/api/nightly").json()["installed"]
        assert [i["status"] for i in installed] == ["installed", "installed"]
    path = _isolated / "data" / "nightly" / "prepared.json"
    data = json.loads(path.read_text("utf-8"))
    opt = [e for e in data["entries"] if e["kind"] == "optimize"][0]
    opt["plans"][0]["eval_objective"] += 1.0                     # 기록을 고치면 재채점이 맞지 않는다
    path.write_text(json.dumps(data), "utf-8")
    with TestClient(app) as c:
        installed = c.get("/api/nightly").json()["installed"]
        bad = [i for i in installed if i["kind"] == "optimize"][0]
        assert bad["status"] == "skipped" and "다시 채점" in bad["reason"]


def test_other_data_version_is_not_installed(boot, fake_solvers, fast_checks, _isolated):
    from api.main import app
    with TestClient(app):
        run_now(app)
    path = _isolated / "data" / "nightly" / "prepared.json"
    data = json.loads(path.read_text("utf-8"))
    data["dataset_version"] = "other"
    path.write_text(json.dumps(data), "utf-8")
    with TestClient(app) as c:
        assert c.get("/api/nightly").json()["installed"][0]["reason"] == "다른 데이터의 밤 결과다"


def test_demo_precomputed_results_are_not_overwritten(tmp_path, monkeypatch, fake_solvers, fast_checks):
    """영상 대본 숫자의 근거(시연 미리 계산)가 있는 요청은 밤에 다시 풀지 않는다."""
    from api.demo_precomputed import FORMAT, precomputed_path
    from api.demos import build_demo
    from api.settings import PlacementSettings
    root = tmp_path / "demo2"
    bundle = generate_org_bundle(root / "org-n100-operating", 100, seed=11, scenario="operating")
    active = build_demo(bundle)
    params = PlacementSettings().to_milp_params(n_people=len(active.graph.people))
    ev = evaluated(active, params, current_entries(active))
    plan = PlanAssignment(entries=current_entries(active), objective=ev.objective.total, unfilled=[], violations=[],
                          time_limited=True, label="A")
    path = precomputed_path(bundle)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"format": FORMAT, "preset": bundle.name, "dataset_version": active.info.version,
                                "computed_at": "2026-10-06T12:00:00+00:00",
                                "optimize": {"weights": {}, "n_alternatives": 3, "milp_params": params.model_dump(),
                                             "plans": [{**plan.model_dump(), "eval_objective": ev.objective.total}]},
                                "operating": None}), "utf-8")
    active.retire()
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(root))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(bundle))
    from api.main import app
    with TestClient(app):
        report = run_now(app)
    default = _solve(report, "기본 요청")[0]
    assert default["status"] == "skipped" and "이미 아침에" in default["reason"]
    assert fake_solvers["plans"] == []
    pre = [ch for ch in report["checks"] if ch["id"] == "precomputed"][0]
    assert pre["status"] == "ok"
    # 관리자 설정이 바뀌면 시연 미리 계산이 쓰이지 않는다 -- 점검이 알리고, 밤 풀이가 새 설정으로 대신 준비한다
    real = nightly._server_params
    monkeypatch.setattr(nightly, "_server_params", lambda app, ds: real(app, ds).model_copy(update={"mu": 0.3}))
    with TestClient(app):
        report = run_now(app)
    pre = [ch for ch in report["checks"] if ch["id"] == "precomputed"][0]
    assert pre["status"] == "warn" and "시연 대본" in pre["detail"]
    assert _solve(report, "기본 요청")[0]["status"] == "prepared"


# --- 낮 요청 기록 ----------------------------------------------------------------------------------------------------

def test_recorder_keeps_recent_unique_bodies_without_fresh(boot, _isolated):
    from api.main import app
    with TestClient(app) as c:
        bad = {"ks": [9], "milp_params": {}, "fresh": True}
        assert c.post("/api/operating/compare", json=bad).status_code == 422          # 잘못된 요청도 본문은 남는다
        assert c.post("/api/operating/compare", json={**bad, "fresh": False}).status_code == 422
        assert c.post("/api/optimize", json={"n_alternatives": 99}).status_code == 422
        c.get("/api/nightly")
        recs = app.state.nightly.recorded()
    assert [r["kind"] for r in recs] == ["operating", "optimize"]                     # fresh만 다른 것은 하나로
    assert all("fresh" not in r["body"] for r in recs)
    saved = json.loads((_isolated / "data" / "nightly" / "requests.json").read_text("utf-8"))
    assert len(saved) == 2


def test_recorded_day_request_is_solved_at_night(boot, fake_solvers, fast_checks):
    from api.main import app
    with TestClient(app) as c:
        version = c.get("/api/datasets/active").json()["version"]
        body = {"weights": {}, "milp_params": web_settings(c), "n_alternatives": 1, "dataset_version": version}
        app.state.nightly.remember("optimize", json.dumps(body).encode())
        app.state.nightly.remember("optimize", json.dumps({**body, "dataset_version": "old"}).encode())
        report = run_now(app)
        day = _solve(report, "낮 요청(")
        assert len(day) == 1 and day[0]["status"] == "prepared" and day[0]["installed"] is True
        plans = [d for e, d in sse(c.post("/api/optimize", json=body)) if e == "plan"]
        assert plans[0]["cached"] is True and plans[0]["precomputed_at"]


# --- 멈춤·관리자 ----------------------------------------------------------------------------------------------------

def test_stop_keeps_the_best_so_far_and_skips_the_rest(boot, monkeypatch, fast_checks):
    import core.optimize.alternatives as alt
    from core.optimize.interrupt import SolveCancelled
    yielded, gate = threading.Event(), threading.Event()

    def slow_stream(graph, S, C, params, n, outcome):
        from api.main import app
        active = app.state.dataset
        ev = evaluated(active, params, current_entries(active))
        yield PlanAssignment(entries=current_entries(active), objective=ev.objective.total, unfilled=[], violations=[],
                             time_limited=True, label="A")
        yielded.set()
        gate.wait(20)
        raise SolveCancelled()
    monkeypatch.setattr(alt, "generate_plans_streaming", slow_stream)
    from api.main import app
    with TestClient(app) as c:
        assert c.post("/api/nightly/run", json={"minutes": 10}).json()["started"] is True
        assert c.post("/api/nightly/run", json={"minutes": 10}).status_code == 409
        assert yielded.wait(60)
        assert c.post("/api/nightly/stop").json()["stopping"] is True
        gate.set()
        app.state.nightly._run_thread.join(60)
        report = c.get("/api/nightly").json()["last"]
    assert report["stopped"] is True and "중간에 멈춤" in report["summary"]
    default = _solve(report, "기본 요청")[0]
    assert default["status"] == "prepared" and default["plans"] == 1 and default["stop_reason"] == "time_limit"
    assert not _solve(report, "운영 중 비교") and report["justifications"] is None


def test_run_and_stop_need_the_admin(boot, monkeypatch, fake_solvers, fast_checks):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "t0ken")
    from api.main import app
    with TestClient(app) as c:
        assert c.get("/api/nightly").status_code == 200                               # 상태는 누구나(개인정보 없음)
        assert c.post("/api/nightly/run", json={"minutes": 5}).status_code == 401
        assert c.post("/api/nightly/stop").status_code == 401
        assert c.post("/api/nightly/run", json={"minutes": 1}, headers={"x-admin-token": "t0ken"}).status_code == 422
        res = c.post("/api/nightly/run", json={"minutes": 5}, headers={"x-admin-token": "t0ken"})
        assert res.status_code == 200
        app.state.nightly._run_thread.join(120)


def test_scheduler_starts_a_run_at_the_window_start(monkeypatch):
    from types import SimpleNamespace
    n = nightly.Nightly(SimpleNamespace(state=SimpleNamespace()), root=None)
    start = datetime.now() + timedelta(seconds=0.5)
    end = datetime.now() + timedelta(hours=1)
    monkeypatch.setattr(nightly, "window", lambda: (start.time(), end.time()))
    monkeypatch.setattr(nightly, "next_start", lambda now, win: start)
    calls = []
    fired = threading.Event()

    def fake_start(trigger, deadline):
        calls.append((trigger, deadline))
        fired.set()
        return True
    monkeypatch.setattr(n, "start_run", fake_start)
    n.start_scheduler()
    try:
        assert fired.wait(10)
    finally:
        n.shutdown(timeout=5)
    trigger, deadline = calls[0]
    assert trigger == "schedule"
    assert abs(deadline - datetime.combine(start.date(), end.time()).timestamp()) < 1


def test_a_run_too_close_to_the_deadline_does_not_start():
    from types import SimpleNamespace
    n = nightly.Nightly(SimpleNamespace(state=SimpleNamespace()), root=None)
    assert n.start_run("schedule", time.time() + 10) is False                       # 잠자기에서 깨어난 아침 등


# --- 실제 솔버(작은 데이터) -------------------------------------------------------------------------------------------

def test_real_solver_path_on_a_small_dataset(fast_checks):
    from api.datasets import build_active
    from core.datagen.generator import generate_dataset
    from core.datagen.parse_reviews import parse_reviews_rule_based
    ds = generate_dataset(15, 3, seed=1)
    parsed = parse_reviews_rule_based(ds.reviews)
    from api.main import app
    with TestClient(app) as c:
        old = app.state.dataset
        app.state.dataset = build_active(ds, parsed, dataset_id="small", version="small-v1", source="fixture",
                                         synthetic=True, judge=False)
        old.retire()
        report = run_now(app)
        default = _solve(report, "기본 요청")[0]
        assert default["status"] == "prepared" and default["installed"] is True, default
        assert default["terminations"][0] in ("Optimal", "time_limit_incumbent")
        body = {"weights": {}, "milp_params": web_settings(c), "n_alternatives": 3, "dataset_version": "small-v1"}
        plans = [d for e, d in sse(c.post("/api/optimize", json=body)) if e == "plan"]
        assert plans and all(p["cached"] and p["precomputed_at"] for p in plans)
        assert _solve(report, "운영 중 비교")[0]["status"] == "skipped"            # 현재 배치가 없는 데이터
        js = report["justifications"]
        assert js["skipped_reason"]                                                # 원 표(CSV 묶음)가 없다


def test_model_check_runs_phase0():
    run = nightly._Run.__new__(nightly._Run)
    run.report = {"checks": []}
    run.check_model()
    assert run.report["checks"][0]["status"] == "ok", run.report["checks"]
    assert run.report["checks"][0]["detail"].startswith("PASS")


def test_a_stop_during_preparation_reaches_the_solver(boot, monkeypatch, fast_checks):
    """자리 대기·행렬 계산 중에 온 멈춤도 풀이가 물려받는다(Codex 리뷰 2026-10-18)."""
    import core.optimize.alternatives as alt
    from core.optimize import interrupt
    from api.main import app
    seen = []
    real = nightly._matrices

    def matrices_then_stop(graph, weights):
        out = real(graph, weights)
        app.state.nightly.request_stop("abort")                   # 준비가 끝나기 직전에 멈춤
        return out

    def stream(graph, S, C, params, n, outcome):
        st = interrupt.current()
        seen.append(None if st is None else st.mode)
        if st is not None and st.is_set():
            raise interrupt.SolveCancelled()
        yield from ()
    monkeypatch.setattr(nightly, "_matrices", matrices_then_stop)
    monkeypatch.setattr(alt, "generate_plans_streaming", stream)
    with TestClient(app):
        report = run_now(app)
    assert seen == ["abort"]
    default = _solve(report, "기본 요청")[0]
    assert default["status"] == "failed" and report["stopped"] is True


def test_failed_bundles_are_not_saved(boot, monkeypatch, fast_checks, _isolated):
    """솔버 실패로 끊긴 묶음·풀지 못한 K가 있는 비교는 저장하지 않는다 -- 다음 밤에 다시 푼다(Codex 리뷰 2026-10-18)."""
    import core.evaluate.operating as op_mod
    import core.optimize.alternatives as alt
    from api.main import app

    def failing_stream(graph, S, C, params, n, outcome):
        active = app.state.dataset
        ev = evaluated(active, params, current_entries(active))
        yield PlanAssignment(entries=current_entries(active), objective=ev.objective.total, unfilled=[], violations=[],
                             label="A")
        outcome["stop_reason"] = "solver_failed"

    def failing_compare(graph, S, C, params, current, ks=(0, 1, 2, 3), on_row=None):
        active = app.state.dataset
        entries = current_entries(active)
        ev = evaluated(active, params, entries)
        return [{"k": k, "accepted": k < 3, "entries": [e.model_dump() for e in entries] if k < 3 else [],
                 "objective": ev.objective.total, "violations": [v.code for v in ev.violations]} for k in ks]
    monkeypatch.setattr(alt, "generate_plans_streaming", failing_stream)
    monkeypatch.setattr(op_mod, "compare_move_budgets", failing_compare)
    with TestClient(app):
        report = run_now(app)
        default = _solve(report, "기본 요청")[0]
        op = _solve(report, "운영 중 비교")[0]
        assert default["status"] == "failed" and "solver_failed" in default["reason"]
        assert op["status"] == "failed" and "3" in op["reason"]
        assert not (_isolated / "data" / "nightly" / "prepared.json").exists()
