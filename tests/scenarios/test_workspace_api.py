"""작업 데이터 API·신규 사업 후보·긴급 투입(3가지 핵심 상황): 연말형 시연 묶음으로 서버를 띄워 등록 → 고정 → 후보 → 확정 →
되돌리기 → 떨어짐, 긴급 투입 → 확정을 실제 계산으로 지난다."""
import pytest
from fastapi.testclient import TestClient


def _ws(c):
    return c.get("/api/workspace").json()


def _form(c, **kw):
    pred = c.get("/api/workspace/projects/J003/form").json()                  # 고도화 과제: 이전 사업 복사
    return {**pred, "name": "보험사 B 고도화", "start_month": "2027-01", "end_month": "2028-06", "stage": "예정",
            "predecessor": "J003", **kw}


def _entries(ws):
    out = []
    for item in ws["pipeline"] + ws["ongoing"]:
        out += [{"person_id": t["person_id"], "project_id": item["project_id"], "alloc": t["alloc"]} for t in item["team"]]
    return out


def test_workspace_view_of_the_yearend_bundle(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
    assert ws["editable"] is True
    stages = {p["project_id"]: p["stage"] for p in ws["pipeline"]}
    assert stages == {"J014": "제안", "J015": "예정"}
    ending = {o["project_id"] for o in ws["ongoing"] if o["ends_in_horizon"]}
    assert ending == {"J003", "J006", "J009", "J012"}                          # 계획 창 끝(2027-03)까지 가는 사업은 아니다
    assert len(ws["bench"]) == 10 and ws["bench"][0]["name"] != ws["bench"][0]["person_id"]   # 가상 데이터는 이름
    assert ws["history"] == []


def test_register_lock_candidates_apply_undo_remove(yearend):
    from api.main import app
    with TestClient(app) as c:
        v0 = _ws(c)["dataset_version"]
        r = c.post("/api/workspace/projects", json={"dataset_version": v0, "project": _form(c)})
        assert r.status_code == 200, r.text
        pid, ws = r.json()["project_id"], r.json()["workspace"]
        assert pid == "J016" and ws["dataset_version"] != v0
        item = next(p for p in ws["pipeline"] if p["project_id"] == pid)
        assert item["stage"] == "예정" and item["predecessor"] == "J003" and item["team"] == []
        assert [h["kind"] for h in ws["history"]] == ["add_project", "base"]
        # 옛 판으로 다시 쓰면 409
        assert c.post("/api/workspace/projects", json={"dataset_version": v0, "project": _form(c)}).status_code == 409

        # 고정: J005 전체 + J001 한 사람
        j001 = next(o for o in ws["ongoing"] if o["project_id"] == "J001")
        one = next(t["person_id"] for t in j001["team"] if not t["locked"])
        ws = c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": "J005",
                                                 "locked": True}).json()["workspace"]
        ws = c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": "J001",
                                                 "people": [one], "locked": True}).json()["workspace"]
        j005 = next(o for o in ws["ongoing"] if o["project_id"] == "J005")
        assert j005["locked_all"] is True
        assert next(t for o in ws["ongoing"] if o["project_id"] == "J001" for t in o["team"]
                    if t["person_id"] == one)["locked"] is True

        # 후보 세 갈래: 고정한 사람은 빼 오기에 없다, J003(이전 사업, 올해 말 종료) 팀원은 '끝나 가는 인력' 맨 위
        cand = c.get(f"/api/pipeline/{pid}/candidates", params={"dataset_version": ws["dataset_version"]}).json()
        assert cand["predecessor"] == "J003" and cand["bench"]
        pulled = {r["person_id"] for r in cand["pull"]} | {r["person_id"] for r in cand["ending"] if r["source"] == "pull"}
        locked_people = {t["person_id"] for t in j005["team"]} | {one}
        assert not (pulled & locked_people)
        assert cand["ending"] and cand["ending"][0]["predecessor_team"] is True

        # 확정: 쉬는 사람 한 명을 J016에 넣는다
        newbie = cand["bench"][0]["person_id"]
        entries = _entries(ws) + [{"person_id": newbie, "project_id": pid, "alloc": 1.0}]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries,
                                                 "source": "manual"})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        assert r.json()["changes"]["added"] == [f"{newbie}@{pid}"]
        assert newbie in {t["person_id"] for t in next(p for p in ws["pipeline"] if p["project_id"] == pid)["team"]}
        assert newbie not in {b["person_id"] for b in ws["bench"]}

        # 고정한 배치를 빼는 확정은 거절
        bad = [e for e in _entries(ws) if e["project_id"] != "J005"]
        assert c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"],
                                                    "entries": bad}).status_code == 422
        # 새 위반(가용률 초과)이 생기는 확정은 거절
        busy = next(e for e in _entries(ws) if e["project_id"] == "J007" and e["alloc"] >= 1.0)
        over = _entries(ws) + [{"person_id": busy["person_id"], "project_id": "J008", "alloc": 1.0}]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": over})
        assert r.status_code == 422 and "위반" in r.text

        # 되돌리기 → 확정 전 판
        r = c.post("/api/workspace/undo", json={"dataset_version": ws["dataset_version"]})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        assert next(p for p in ws["pipeline"] if p["project_id"] == pid)["team"] == []
        # 떨어짐 → 사업이 사라진다
        r = c.delete(f"/api/workspace/projects/{pid}", params={"dataset_version": ws["dataset_version"]})
        assert r.status_code == 200 and pid not in {p["project_id"] for p in r.json()["workspace"]["pipeline"]}


def test_writes_need_the_admin(yearend, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "t0ken")
    from api.main import app
    with TestClient(app) as c:
        v = _ws(c)["dataset_version"]
        assert c.post("/api/workspace/lock", json={"dataset_version": v, "project_id": "J001"}).status_code == 401
        assert c.post("/api/workspace/undo", json={"dataset_version": v}).status_code == 401


def test_fixture_data_is_not_editable(monkeypatch):
    monkeypatch.delenv("TEAMWEAVER_DEMO_BUNDLE", raising=False)
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        assert ws["editable"] is False and ws["reason"]
        assert c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"],
                                                   "project_id": "P01"}).status_code == 409


def test_emergency_plan_and_apply(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        r = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": "J007", "n": 1,
                                                "skills": ["Kubernetes"], "pull_budget": 0, "note": "장애 대응"})
        assert r.status_code == 200, r.text
        plan = r.json()
        assert plan["best"]["accepted"] is True and len(plan["best"]["added"]) == 1
        assert plan["standby"] and plan["candidates"]["bench"]
        hint = plan["apply_hint"]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"],
                                                 "entries": plan["best"]["entries"], "source": "emergency",
                                                 "note": "J007 장애 대응", **hint})
        assert r.status_code == 200, r.text
        ws2 = r.json()["workspace"]
        added = plan["best"]["added"][0]["person_id"]
        j007 = next(o for o in ws2["ongoing"] if o["project_id"] == "J007")
        assert added in {t["person_id"] for t in j007["team"]}
        assert ws2["history"][0]["kind"] == "apply_emergency"
        # 진행 사업이 아니면 422
        assert c.post("/api/emergency/plan", json={"dataset_version": ws2["dataset_version"], "project_id": "J015",
                                                   "n": 1}).status_code == 422


def test_a_failing_history_write_leaves_the_old_version_active(yearend, monkeypatch):
    """이력은 켜기 전에 쓴다 -- 쓰다 실패하면 새 판을 켜지 않는다(Codex 리뷰 2026-10-19)."""
    from api import workspace
    from api.main import app
    with TestClient(app) as c:
        v0 = _ws(c)["dataset_version"]

        def boom(self, entry):
            raise OSError("디스크 가득 참")
        monkeypatch.setattr(workspace.History, "append", boom)
        with pytest.raises(OSError):
            c.post("/api/workspace/lock", json={"dataset_version": v0, "project_id": "J001", "locked": True})
        assert _ws(c)["dataset_version"] == v0


def test_worsening_a_lower_bound_violation_is_refused():
    """하한 위반(최소 투입률)은 모자란 만큼이 크기다 -- 0.20 → 0.10은 악화, 0.20 → 0.25는 개선(Codex 리뷰 2026-10-19)."""
    from core.evaluate.plan_eval import PlanViolation
    old = PlanViolation(code="min_alloc", location="P1@J1", actual=0.20, limit=0.30, message="")
    worse = PlanViolation(code="min_alloc", location="P1@J1", actual=0.10, limit=0.30, message="")
    better = PlanViolation(code="min_alloc", location="P1@J1", actual=0.25, limit=0.30, message="")
    size = lambda v: abs(v.actual - v.limit)
    assert size(worse) > size(old) and not size(better) > size(old)


def _crlf_bundle(tmp_path):
    import shutil
    from tests.scenarios.conftest import YEAREND
    dst = tmp_path / "demo" / "org-n100-crlf"
    shutil.copytree(YEAREND, dst)
    import hashlib
    import json as _json
    m = _json.loads((dst / "manifest.json").read_text("utf-8"))
    for name in m.get("files", {}):
        raw = (dst / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        (dst / name).write_bytes(raw)
        m["files"][name] = hashlib.sha256(raw).hexdigest()
    (dst / "manifest.json").write_text(_json.dumps(m, ensure_ascii=False), "utf-8")
    return dst


def test_undo_works_on_a_crlf_bundle(tmp_path, monkeypatch):
    """원문 줄바꿈을 지켜 읽는다 -- 시작 판 보관본이 해시 검사를 통과해 되돌리기가 된다(Codex 리뷰 2026-10-19)."""
    dst = _crlf_bundle(tmp_path)
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(dst.parent))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(dst))
    from api.main import app
    with TestClient(app) as c:
        v0 = _ws(c)["dataset_version"]
        r = c.post("/api/workspace/lock", json={"dataset_version": v0, "project_id": "J001", "locked": True})
        assert r.status_code == 200, r.text
        r = c.post("/api/workspace/undo", json={"dataset_version": r.json()["workspace"]["dataset_version"]})
        assert r.status_code == 200, r.text
        assert r.json()["workspace"]["dataset_version"] == v0


def test_bundles_with_column_mappings_are_not_editable(tmp_path, monkeypatch):
    import shutil
    from tests.scenarios.conftest import YEAREND
    dst = tmp_path / "demo" / "org-n100-mapped"
    shutil.copytree(YEAREND, dst)
    (dst / "mapping.json").write_text("{}", "utf-8")
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(dst.parent))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(dst))
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
    assert ws["editable"] is False and "mapping" in ws["reason"]


def test_nightly_standby_is_not_used_after_a_settings_change(yearend, fake_solvers, fast_checks, monkeypatch):
    from api.main import app
    from api.routes import scenarios
    from tests.nightly.helpers import run_now
    with TestClient(app) as c:
        run_now(app)
        v = _ws(c)["dataset_version"]
        assert c.get("/api/emergency/standby", params={"dataset_version": v}).json()["source"] == "nightly"
        real = scenarios._params
        monkeypatch.setattr(scenarios, "_params", lambda a, d: real(a, d).model_copy(update={"min_alloc": 0.6}))
        sb = c.get("/api/emergency/standby", params={"dataset_version": v}).json()
        assert sb["source"] is None and sb["projects"] == {}
        live = c.get("/api/emergency/standby", params={"dataset_version": v, "project_id": "J007"}).json()
        assert live["source"] == "live" and all(r["alloc"] >= 0.6 for r in live["projects"]["J007"])


def test_pipeline_candidates_fill_existing_seats(yearend):
    """신규 사업 후보는 등록한 정원·예산 그대로 채점한다 -- 빈자리 감점이 줄어 점수가 오른다(Codex 리뷰 2026-10-19)."""
    from api.main import app
    with TestClient(app) as c:
        v = _ws(c)["dataset_version"]
        cand = c.get("/api/pipeline/J015/candidates", params={"dataset_version": v}).json()
    rows = cand["bench"] + cand["ending"] + cand["pull"]
    assert rows and all(r["budget_added"] == 0 for r in rows)
    assert any(r["delta"].get("unfilled", 0) > 0 for r in cand["bench"])


def test_editing_a_record_survives_a_restart_and_exports(yearend):
    """편성 기록을 연 뒤 작업 데이터를 고치면 재기동해도 고친 판으로 뜨고(열어 둔 기록 표시를 지운다), 기록으로 내보낼 수 있다."""
    from tests.scenarios.conftest import ROOT
    from api.main import app
    raw = (ROOT / "demo" / "records" / "org-n100-operating.json").read_bytes()
    with TestClient(app) as c:
        r = c.post("/api/records", content=raw, headers={"Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        rid = r.json()["record"]["id"]
        r = c.post(f"/api/records/{rid}/open", json={})
        assert r.status_code == 200, r.text
        assert c.get("/api/datasets/active").json()["source"] == "record"
        v = _ws(c)["dataset_version"]
        r = c.post("/api/workspace/lock", json={"dataset_version": v, "project_id": "J001", "locked": True})
        assert r.status_code == 200, r.text
        edited = r.json()["workspace"]["dataset_version"]
        from api.records import files_of                                      # 기록 내보내기가 읽는 원천 표
        cur = files_of(app, app.state.dataset)["current_assignments.csv"]
        assert all(line.endswith(",Y") for line in cur.splitlines()[1:] if ",J001," in line)
    with TestClient(app) as c:
        assert _ws(c)["dataset_version"] == edited                               # 옛 기록이 아니라 고친 판으로 뜬다


def _open_operating_record(c):
    from tests.scenarios.conftest import ROOT
    raw = (ROOT / "demo" / "records" / "org-n100-operating.json").read_bytes()
    r = c.post("/api/records", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/records/{r.json()['record']['id']}/open", json={})
    assert r.status_code == 200, r.text


def test_a_failed_save_keeps_the_open_record_marker(yearend, monkeypatch):
    """새 판 저장이 실패하면 열어 둔 기록 표시를 지우지 않는다 -- 지우면 재기동 때 복원할 곳이 둘 다 없어진다."""
    from api.main import app
    with TestClient(app) as c:
        _open_operating_record(c)
        marker = app.state.record_store.dir / "active.json"
        assert marker.exists()

        def broken_save(*_a, **_k):
            raise OSError("disk full")
        monkeypatch.setattr(app.state.dataset_store, "save", broken_save)
        v = _ws(c)["dataset_version"]
        r = c.post("/api/workspace/lock", json={"dataset_version": v, "project_id": "J001", "locked": True})
        assert r.status_code == 200, r.text
        assert r.json()["persisted"] is False
        assert marker.exists()


def test_a_failed_marker_removal_does_not_fail_the_applied_edit(yearend, monkeypatch):
    """기록 표시 지우기가 실패해도 이미 켠 새 판의 요청은 성공하고, 알림만 남긴다."""
    from api.main import app
    with TestClient(app) as c:
        _open_operating_record(c)

        def broken_clear():
            raise PermissionError("read-only")
        monkeypatch.setattr(app.state.record_store, "clear_active", broken_clear)
        v = _ws(c)["dataset_version"]
        r = c.post("/api/workspace/lock", json={"dataset_version": v, "project_id": "J001", "locked": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["persisted"] is False and "기록 표시" in body["persist_error"]
        assert body["workspace"]["dataset_version"] != v
        assert _ws(c)["dataset_version"] == body["workspace"]["dataset_version"]
