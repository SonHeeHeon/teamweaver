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
    assert nightly.night_limit(180, 4, 1000) == 205              # 남은 시간의 90%에서 풀이마다 준비 20초를 빼고 나눈다
    assert nightly.night_limit(180, 4, 1000 * 4) == 880
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


def _record_day_request(app, c, n_alt: int = 1) -> dict:
    """낮에 보낸 최적화 요청 하나를 기록한다(안 개수만 기본과 다르게 -- 밤에 다시 풀 대상)."""
    version = c.get("/api/datasets/active").json()["version"]
    body = {"weights": {}, "milp_params": web_settings(c), "n_alternatives": n_alt, "dataset_version": version}
    app.state.nightly.remember("optimize", json.dumps(body).encode())
    return body


# --- 밤 풀이 → 아침 즉시 ---------------------------------------------------------------------------------------------

def test_manual_run_prepares_and_the_morning_request_is_instant(boot, fake_solvers, fast_checks):
    """운영 중 데이터: 연간 계획(백지)은 풀지 않고, 신규 사업이 있으니 운영 중 비교를 미리 풀고, 긴급 대기 명단을 뽑는다.
    AI는 부르지 않는다(2026-10-18 재정비)."""
    from api.main import app
    with TestClient(app) as c:
        version = c.get("/api/datasets/active").json()["version"]
        report = run_now(app)
        annual = _solve(report, "연간 계획")[0]
        assert annual["status"] == "skipped" and "운영 중" in annual["reason"]
        assert fake_solvers["plans"] == []                                      # 운영 중에는 백지 계획을 풀지 않는다
        op = _solve(report, "운영 중 비교")[0]
        assert op["status"] == "prepared" and op["installed"] is True
        assert fake_solvers["operating"] and fake_solvers["operating"][0] == op["night_time_limit"]
        assert op["night_time_limit"] >= op.get("day_time_limit", 0)
        rows = [d for e, d in sse(c.post("/api/operating/compare",
                                          json={"milp_params": web_settings(c), "dataset_version": version})) if e == "row"]
        assert len(rows) == 4 and all(r["precomputed_at"] for r in rows)
        assert len(fake_solvers["operating"]) == 1                              # 아침 요청은 다시 풀지 않았다
        sb = c.get("/api/emergency/standby", params={"dataset_version": version}).json()
        assert sb["source"] == "nightly" and len(sb["projects"]) == report["standby"]["projects"] > 0
        assert all(len(v) <= 5 for v in sb["projects"].values())

        st = c.get("/api/nightly").json()
        assert st["running"] is False and st["last"]["trigger"] == "manual"
        assert "미리 풀어 둠" in st["last"]["summary"] and "긴급 대기 명단" in st["last"]["summary"]
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
        assert [(i["kind"], i["status"]) for i in installed] == [("operating", "installed")]
    path = _isolated / "data" / "nightly" / "prepared.json"
    data = json.loads(path.read_text("utf-8"))
    op = [e for e in data["entries"] if e["kind"] == "operating"][0]
    op["rows"][1]["objective"] += 1.0                            # 기록을 고치면 재채점이 맞지 않는다
    path.write_text(json.dumps(data), "utf-8")
    with TestClient(app) as c:
        installed = c.get("/api/nightly").json()["installed"]
        bad = [i for i in installed if i["kind"] == "operating"][0]
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


def test_results_already_in_the_cache_are_not_solved_again(boot, fake_solvers, fast_checks):
    """이미 바로 나오는 결과(편성 기록에서 연 결과·낮 결과)는 밤에 다시 풀지 않는다 -- 기록의 숫자를 덮지 않는다."""
    from api.cache import ResultCache
    from api.main import app
    from api.schemas import MilpParamsIn
    with TestClient(app) as c:
        body = _record_day_request(app, c, n_alt=3)
        active = app.state.dataset
        params = MilpParamsIn(**body["milp_params"]).to_milp_params()
        ev = evaluated(active, params, current_entries(active))
        plan = PlanAssignment(entries=current_entries(active), objective=ev.objective.total, unfilled=[], violations=[],
                              label="A")
        app.state.cache.put(ResultCache.key({}, params, 3, active.info.version), [plan])
        report = run_now(app)
    day = _solve(report, "낮 요청")
    assert len(day) == 1 and day[0]["status"] == "skipped"
    assert fake_solvers["plans"] == []


# --- 낮 요청 기록 ----------------------------------------------------------------------------------------------------

def test_recorder_keeps_recent_accepted_bodies_without_fresh(boot, fake_solvers, monkeypatch, _isolated):
    """서버가 받아들인 요청(200)만, fresh를 뺀 본문으로, 같은 것은 하나로 남긴다(Opus 대체 리뷰 2026-10-18: 422 본문을 남기던 것)."""
    import api.routes.optimize as opt_route
    import core.optimize.alternatives as alt
    monkeypatch.setattr(opt_route, "generate_plans_streaming", alt.generate_plans_streaming)   # 가짜 풀이(fake_solvers)
    from api.main import app
    with TestClient(app) as c:
        version = c.get("/api/datasets/active").json()["version"]
        op = {"milp_params": web_settings(c), "dataset_version": version}
        assert c.post("/api/operating/compare", json={**op, "fresh": True}).status_code == 200
        assert c.post("/api/operating/compare", json=op).status_code == 200
        assert c.post("/api/operating/compare", json={"ks": [9], "milp_params": {}}).status_code == 422   # 남기지 않음
        assert c.post("/api/optimize", json={"n_alternatives": 99}).status_code == 422
        body = {"weights": {}, "milp_params": web_settings(c), "n_alternatives": 1, "dataset_version": version}
        assert c.post("/api/optimize", json=body).status_code == 200
        for _ in range(100):                                       # 기록은 별도 스레드에서 쓴다
            recs = app.state.nightly.recorded()
            if len(recs) >= 2:
                break
            time.sleep(0.05)
    assert [r["kind"] for r in recs] == ["operating", "optimize"]
    assert all("fresh" not in r["body"] for r in recs) and recs[1]["body"]["n_alternatives"] == 1


def test_install_refuses_when_the_data_changed(boot, fake_solvers, fast_checks):
    from api.main import app
    with TestClient(app):
        run_now(app)
        data = app.state.nightly.store.read("prepared.json")
        import copy
        stale = copy.copy(app.state.dataset)                    # 같은 내용이지만 지금 켜진 데이터가 아닌 객체(전환 뒤 늦은 넣기)
        res = nightly._install_entry(app, stale, data["entries"][0])
        assert res["status"] == "skipped" and res["reason"] == "데이터가 바뀌었다"


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

def test_a_stopped_bundle_is_not_kept_and_the_rest_is_skipped(boot, monkeypatch, fake_solvers, fast_checks, _isolated):
    """멈춤으로 끝까지 풀지 못한 묶음은 저장·캐시하지 않는다 -- 아침 결과로 굳고 다음 밤에도 다시 풀지 않게 되므로
    (Opus 대체 리뷰 2026-10-18). 남은 단계는 하지 않는다."""
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
        _record_day_request(app, c)
        assert c.post("/api/nightly/run", json={"minutes": 10}).json()["started"] is True
        assert c.post("/api/nightly/run", json={"minutes": 10}).status_code == 409
        assert yielded.wait(60)
        assert c.post("/api/nightly/stop").json()["stopping"] is True
        gate.set()
        app.state.nightly._run_thread.join(60)
        report = c.get("/api/nightly").json()["last"]
    assert report["stopped"] is True and "중간에 멈춤" in report["summary"]
    day = _solve(report, "낮 요청(")[0]
    assert day["status"] == "incomplete" and day["plans"] == 1 and "멈춤" in day["reason"]
    assert "ai" not in {ch["id"] for ch in report["checks"]}                  # 남은 단계는 하지 않았다
    data = json.loads((_isolated / "data" / "nightly" / "prepared.json").read_text("utf-8"))
    assert [e["kind"] for e in data["entries"]] == ["operating"]              # 멈춘 묶음은 저장하지 않았다
    assert report["status"] == "attention"


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
        default = _solve(report, "연간 계획")[0]                              # 백지 데이터라 연간 계획을 푼다
        assert default["status"] == "prepared" and default["installed"] is True, default
        assert default["terminations"][0] in ("Optimal", "time_limit_incumbent")
        body = {"weights": {}, "milp_params": web_settings(c), "n_alternatives": 3, "dataset_version": "small-v1"}
        plans = [d for e, d in sse(c.post("/api/optimize", json=body)) if e == "plan"]
        assert plans and all(p["cached"] and p["precomputed_at"] for p in plans)
        assert _solve(report, "운영 중 비교")[0]["status"] == "skipped"            # 현재 배치가 없는 데이터
        assert report["standby"]["projects"] == 0                                  # 진행 사업이 없다


def test_model_check_runs_phase0():
    run = nightly._Run.__new__(nightly._Run)
    run.report = {"checks": []}
    run.check_model()
    assert run.report["checks"][0]["status"] == "ok", run.report["checks"]
    assert run.report["checks"][0]["detail"].startswith("PASS")


def test_a_stop_during_preparation_reaches_the_solver(boot, monkeypatch, fast_checks):
    """자리 대기·행렬 계산 중에 온 멈춤도 풀이가 물려받는다(Codex 리뷰 2026-10-18)."""
    import core.evaluate.operating as op_mod
    from core.optimize import interrupt
    from api.main import app
    seen = []
    real = nightly._matrices

    def matrices_then_stop(graph, weights):
        out = real(graph, weights)
        app.state.nightly.request_stop("abort")                   # 준비가 끝나기 직전에 멈춤
        return out

    def compare(graph, S, C, params, current, ks=(0, 1, 2, 3), on_row=None):
        st = interrupt.current()
        seen.append(None if st is None else st.mode)
        if st is not None and st.is_set():
            raise interrupt.SolveCancelled()
        return []
    monkeypatch.setattr(nightly, "_matrices", matrices_then_stop)
    monkeypatch.setattr(op_mod, "compare_move_budgets", compare)
    with TestClient(app):
        report = run_now(app)
    assert seen == ["abort"]
    op = _solve(report, "운영 중 비교")[0]
    assert op["status"] == "failed" and report["stopped"] is True


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
        # K=2는 풀이에 실패해 직전 해를 이어 썼다(accepted이지만 solve_failed), K=3은 실패 행
        return [{"k": k, "accepted": k < 3, "solve_failed": k == 2,
                 "entries": [e.model_dump() for e in entries] if k < 3 else [],
                 "objective": ev.objective.total, "violations": [v.code for v in ev.violations]} for k in ks]
    monkeypatch.setattr(alt, "generate_plans_streaming", failing_stream)
    monkeypatch.setattr(op_mod, "compare_move_budgets", failing_compare)
    with TestClient(app) as c:
        _record_day_request(app, c)
        report = run_now(app)
        default = _solve(report, "낮 요청(")[0]
        op = _solve(report, "운영 중 비교")[0]
        assert default["status"] == "incomplete" and "solver_failed" in default["reason"]
        assert op["status"] == "failed" and "2, 3" in op["reason"]
        assert not (_isolated / "data" / "nightly" / "prepared.json").exists()
