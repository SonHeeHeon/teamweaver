"""데이터 원천(2026-10-19 사용자 요청, api/hr_source): 인사 시스템 API가 기본, 없으면 CSV. 가짜 인사 시스템 서버로
받기 → 같은 검사 → 새 판(이력·되돌리기), 그대로면 새 판 없음, 검증 실패·인증 실패면 지금 데이터 그대로,
진행 중 사업 기술 경력 갱신 주체(관리자 설정)와 밤 배치 첫 단계까지 지난다."""
import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from scripts.mock_hr_api import load_tables, make_server
from tests.scenarios.conftest import YEAREND


@pytest.fixture
def hr(monkeypatch, yearend):
    tables = load_tables(YEAREND)
    srv = make_server(None, 0, "t0k", tables=tables)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    monkeypatch.setenv("TEAMWEAVER_HR_API_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("TEAMWEAVER_HR_API_TOKEN", "t0k")
    yield tables
    srv.shutdown()
    srv.server_close()


def _stale_ongoing_skill(tables):
    """진행 중 업무에 등록된 기술 하나를 '두 달 전 숫자에서 멈춘' 원천처럼 고친다(정적인 기술 이력 서비스)."""
    works = {w["work_id"]: w for w in tables["work-history"] if w["status"] == "진행중" and w["start_date"] < "2026-07"}
    for link in tables["work-skills"]:
        w = works.get(link["work_id"])
        if w is None:
            continue
        for r in tables["person-skills"]:
            if (r["person_id"], r["skill_name"]) == (w["person_id"], link["skill_name"]) and r["last_used_month"] == "2026-09":
                r["experience_months"] = str(int(r["experience_months"]) - 2)
                r["last_used_month"] = "2026-07"
                return r["person_id"], r["skill_name"]
    raise AssertionError("진행 중 업무 기술이 없다")


def test_csv_mode_without_an_hr_address(yearend):
    from api.main import app
    with TestClient(app) as c:
        st = c.get("/api/hr-source").json()
        assert st["mode"] == "csv" and st["editable"] and st["skill_months_owner"] == "service"
        assert {t["from"] for t in st["tables"]} == {"data"}
        assert st["aging"]["cutoff"] == "2026-09" and st["aging"]["skills"] == 0
        assert c.post("/api/hr-source/sync").status_code == 409


def test_sync_applies_changes_once_and_keeps_the_projects(hr):
    from api.main import app
    with TestClient(app) as c:
        before = c.get("/api/workspace").json()
        v0 = before["dataset_version"]
        st = c.get("/api/hr-source").json()
        assert st["mode"] == "api" and {t["name"] for t in st["tables"] if t["from"] == "api"} >= {"people.csv", "reviews.csv"}
        assert {t["from"] for t in st["tables"] if t["service"]} == {"data"}       # 사업·현재 배치는 이 서비스가 관리
        hr["people"][0]["display_name"] = "새이름"
        r = c.post("/api/hr-source/sync")
        assert r.status_code == 200, r.text
        res = r.json()["result"]
        assert res["status"] == "applied" and "people.csv" in res["changed"], res
        ws = c.get("/api/workspace").json()
        assert ws["dataset_version"] != v0 and ws["history"][0]["kind"] == "hr_sync"
        assert [p["project_id"] for p in ws["pipeline"]] == [p["project_id"] for p in before["pipeline"]]
        assert [p["project_id"] for p in ws["ongoing"]] == [p["project_id"] for p in before["ongoing"]]
        names = {p["id"]: p["name"] for p in c.get("/api/meta").json()["people"]}
        assert names[hr["people"][0]["person_id"]] == "새이름"
        assert c.get("/api/hr-source").json()["last"]["status"] == "applied"
        # 원천이 그대로면 새 판을 만들지 않는다
        res2 = c.post("/api/hr-source/sync").json()["result"]
        assert res2["status"] == "unchanged" and c.get("/api/workspace").json()["dataset_version"] == ws["dataset_version"]
        # 되돌리기는 다른 확정과 같다
        back = c.post("/api/workspace/undo", json={"dataset_version": ws["dataset_version"]})
        assert back.status_code == 200 and back.json()["workspace"]["dataset_version"] == v0


def test_bad_or_unreachable_source_keeps_the_current_data(hr, monkeypatch):
    from api.main import app
    with TestClient(app) as c:
        v0 = c.get("/api/workspace").json()["dataset_version"]
        hr["person-skills"][0]["experience_months"] = "많이"
        res = c.post("/api/hr-source/sync").json()["result"]
        assert res["status"] == "invalid" and "person_skills.csv" in res["detail"], res
        monkeypatch.setenv("TEAMWEAVER_HR_API_TOKEN", "wrong")
        res = c.post("/api/hr-source/sync").json()["result"]
        assert res["status"] == "fail" and "인증" in res["detail"]
        assert c.get("/api/workspace").json()["dataset_version"] == v0
        assert c.get("/api/hr-source").json()["last"]["status"] == "fail"


def test_static_source_numbers_catch_up_unless_the_source_updates_them(hr):
    """사용자 질문의 경우: 기술 이력 서비스가 진행 중 사업 경력을 갱신하지 않으면(정적) TeamWeaver가 계획 직전 달까지 늘리고,
    관리자가 '원천 시스템이 갱신한다'로 바꾸면 늘리지 않는다."""
    from api.main import app
    with TestClient(app) as c:
        _stale_ongoing_skill(hr)
        res = c.post("/api/hr-source/sync").json()["result"]
        assert res["status"] == "applied" and res["aging"]["skills"] == 1 and res["aging"]["max_months"] == 2, res
        v = c.get("/api/workspace").json()["dataset_version"]
        r = c.post("/api/hr-source/skill-months-owner", json={"dataset_version": v, "owner": "source"})
        assert r.status_code == 200, r.text
        st = r.json()["status"]
        assert st["skill_months_owner"] == "source" and st["aging"]["skills"] == 0
        ws = c.get("/api/workspace").json()
        assert ws["dataset_version"] != v and ws["history"][0]["kind"] == "skill_months_owner"
        # 화면이 든 판이 옛것이면 거절, 같은 값이면 그대로
        assert c.post("/api/hr-source/skill-months-owner", json={"dataset_version": v, "owner": "service"}).status_code == 409
        same = c.post("/api/hr-source/skill-months-owner", json={"dataset_version": ws["dataset_version"], "owner": "source"})
        assert same.json()["unchanged"] is True
        # 받기는 지금 판의 갱신 주체를 이어 쓴다
        hr["people"][1]["display_name"] = "또다른이름"
        res = c.post("/api/hr-source/sync").json()["result"]
        assert res["aging"]["owner"] == "source" and res["aging"]["skills"] == 0


def test_nightly_runs_the_sync_first(hr):
    from api.main import app
    with TestClient(app) as c:
        v0 = c.get("/api/workspace").json()["dataset_version"]
        hr["people"][0]["display_name"] = "밤에바뀜"
        check = app.state.nightly._hr_sync(time.time() + 120)
        assert check["status"] == "ok" and "새 판" in check["detail"], check
        assert c.get("/api/workspace").json()["dataset_version"] != v0
        assert app.state.nightly._hr_sync(time.time() + 120)["detail"].startswith("원천이 그대로")


def _wait_unlocked(timeout=20.0):
    from api import hr_source
    t0 = time.time()
    while hr_source._sync_lock.locked() and time.time() - t0 < timeout:
        time.sleep(0.05)
    assert not hr_source._sync_lock.locked()


def _slow_commit(monkeypatch, started: threading.Event, delay=0.6):
    import api.workspace as ws
    real = ws.commit

    async def slow(*a, **kw):
        started.set()
        await asyncio.sleep(delay)                     # 켜기 도중(여기서 바깥 취소·시간 초과가 온다)
        return await real(*a, **kw)
    monkeypatch.setattr(ws, "commit", slow)


def test_a_cancelled_sync_still_finishes_the_switch(hr, monkeypatch):
    """Codex 리뷰(2026-10-19, high): 켜기 도중 바깥에서 취소해도 켜기·저장·이력이 끝까지 가고, 잠금이 풀리며, 결과가 '켰다'로 남는다."""
    from api import hr_source
    from api.main import app
    started = threading.Event()
    _slow_commit(monkeypatch, started)
    with TestClient(app) as c:
        v0 = c.get("/api/workspace").json()["dataset_version"]
        hr["people"][0]["display_name"] = "취소돼도켜짐"
        fut = asyncio.run_coroutine_threadsafe(hr_source.sync(app, trigger="manual"), app.state.nightly.loop)
        assert started.wait(10)
        fut.cancel()
        _wait_unlocked()
        ws = c.get("/api/workspace").json()
        assert ws["dataset_version"] != v0 and ws["history"][0]["kind"] == "hr_sync"
        last = c.get("/api/hr-source").json()["last"]
        assert last["status"] == "applied" and last["version"] == ws["dataset_version"] and last["persisted"] is True
        saved = app.state.dataset_store.load()
        assert saved is not None and saved[0]["version"] == app.state.dataset.info.content_version


def test_nightly_timeout_stops_waiting_without_cancelling(hr, monkeypatch):
    import api.nightly as nightly_mod
    from api.main import app
    started = threading.Event()
    _slow_commit(monkeypatch, started, delay=1.0)
    monkeypatch.setattr(nightly_mod, "HR_SYNC_MAX_S", 0.3)
    with TestClient(app) as c:
        v0 = c.get("/api/workspace").json()["dataset_version"]
        hr["people"][0]["display_name"] = "늦게켜짐"
        check = app.state.nightly._hr_sync(time.time() + 120)
        assert check["status"] == "warn" and "기다리지 않고" in check["detail"], check
        _wait_unlocked()
        assert c.get("/api/workspace").json()["dataset_version"] != v0
        assert c.get("/api/hr-source").json()["last"]["status"] == "applied"


def test_nightly_stop_ends_the_wait(hr, monkeypatch):
    """Codex 재리뷰(2026-10-19): 받기를 기다리는 동안에도 밤 배치 멈춤을 본다 -- 받기(켜기)는 끝까지 간다."""
    from api.main import app
    started = threading.Event()
    _slow_commit(monkeypatch, started, delay=3.0)
    with TestClient(app) as c:
        hr["people"][0]["display_name"] = "멈춰도켜짐"
        nightly = app.state.nightly
        threading.Thread(target=lambda: (started.wait(10), nightly._cancel.set()), daemon=True).start()
        t0 = time.time()
        check = nightly._hr_sync(time.time() + 120)
        assert check["status"] == "warn" and "멈춤 요청" in check["detail"] and time.time() - t0 < 3.0, check
        nightly._cancel.clear()
        _wait_unlocked()
        assert c.get("/api/hr-source").json()["last"]["status"] == "applied"
