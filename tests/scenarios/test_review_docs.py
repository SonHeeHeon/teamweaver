"""인사팀 검토 자료(api/routes/review_docs, 2026-10-19 claude-a): 연말형 묶음에서 진행 사업 J010의 한 명을 빼 오고 대기 인력 한 명을 더해
신규 사업 J014를 확정한 뒤 자료를 만들고 고친다. AI는 가짜 client로만(외부 호출 없음)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

PULLED, BENCH, SRC, TGT = "DP0074", "DP0032", "J010", "J014"


def _ws(c):
    return c.get("/api/workspace").json()


def _entries(ws):
    out = []
    for item in ws["pipeline"] + ws["ongoing"]:
        out += [{"person_id": t["person_id"], "project_id": item["project_id"], "alloc": t["alloc"]} for t in item["team"]]
    return out


def _confirm(c, ws=None, source="manual"):
    ws = ws or _ws(c)
    entries = [e for e in _entries(ws) if not (e["person_id"] == PULLED and e["project_id"] == SRC)]
    entries += [{"person_id": PULLED, "project_id": TGT, "alloc": 1.0}, {"person_id": BENCH, "project_id": TGT, "alloc": 1.0}]
    r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": source})
    assert r.status_code == 200, r.text
    return r.json()["workspace"]["dataset_version"]


def _create(c, v):
    r = c.post(f"/api/review-docs/{v}/{TGT}")
    assert r.status_code == 200, r.text
    return r.json()


def test_confirm_lists_target_and_doc_has_locked_move_facts(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        confirms = c.get("/api/review-docs").json()["confirms"]
        assert confirms[0]["version"] == v and confirms[0]["kind"] == "new" and confirms[0]["active"] is True
        assert [p["project_id"] for p in confirms[0]["projects"]] == [TGT] and confirms[0]["projects"][0]["exists"] is False
        doc = _create(c, v)
        joined = {j["person_id"]: j for j in doc["moves"]["joined"]}
        assert joined[PULLED]["origin"] == "moved" and joined[BENCH]["origin"] == "bench"
        assert joined[PULLED]["sources"][0]["project_id"] == SRC
        kinds = {f["kind"] for f in doc["facts"] if f["section"] == "move"}
        assert {"MOVE_TARGET", "MOVE_WHO", "MOVE_FROM", "MOVE_LOSS", "MOVE_FILL"} <= kinds
        loss = next(f for f in doc["facts"] if f["kind"] == "MOVE_LOSS")
        assert loss["adverse"] is True and SRC in loss["text"]
        assert "NOT_CALIBRATED" in doc["disclaimer"]
        assert doc["milp_params"]["min_alloc"] > 0         # 만들 때의 평가 설정을 남긴다(초안도 같은 기준, Codex 리뷰)
        assert not any("크다" in f["text"] or "더 낫" in f["text"] for f in doc["facts"])   # 결론은 서버가 쓰지 않는다
        # 두 번 만들면 같은 자료(편집 보존)
        assert _create(c, v)["created_at"] == doc["created_at"]


def test_edit_notes_judgments_and_locks(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        loss = next(f for f in doc["facts"] if f["kind"] == "MOVE_LOSS")
        body = {"revision": doc["revision"], "body": "부서장이 쓴 사유.",
                "notes": [{"key": loss["key"], "type": "reply", "text": "J010은 막바지라 한 명이면 된다."}],
                "judgments": {PULLED: {"text": "J010 Java 작업이 거의 끝났다.", "agreement": {"status": "requested", "memo": "인사팀 경유"}}}}
        r = c.put(f"/api/review-docs/{v}/{TGT}", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["revision"] == doc["revision"] + 1 and out["body_edited"] is True
        assert out["notes"][0]["author"] and out["notes"][0]["orphan"] is False
        assert out["judgments"][PULLED]["agreement"]["status"] == "requested"
        # 시스템 사실은 받지 않는다
        assert c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": out["revision"], "facts": []}).status_code == 422
        # 이의는 사유 필수
        bad = {"revision": out["revision"], "notes": [{"key": loss["key"], "type": "objection", "text": "  "}]}
        assert c.put(f"/api/review-docs/{v}/{TGT}", json=bad).status_code == 422
        # 새로 들어온 사람이 아닌 판단은 거절
        assert c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": out["revision"],
                                                          "judgments": {"DP9999": {"text": "x"}}}).status_code == 422


def test_stale_revision_conflicts(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        assert c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"], "body": "앞"}).status_code == 200
        r = c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"], "body": "뒤"})
        assert r.status_code == 409
        assert c.get(f"/api/review-docs/{v}/{TGT}").json()["body"] == "앞"


def test_notes_follow_fact_keys_and_orphans_survive(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        r = c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"], "notes": [
            {"key": doc["facts"][0]["key"], "type": "reply", "text": "있는 사실"},
            {"key": "0000000000000000", "type": "objection", "text": "사라진 사실에 단 이의"}]})
        notes = {n["text"]: n for n in r.json()["notes"]}
        assert notes["있는 사실"]["orphan"] is False and notes["사라진 사실에 단 이의"]["orphan"] is True


def test_reverted_confirm_locks_export_and_edit(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        assert c.post("/api/workspace/undo", json={"dataset_version": v}).status_code == 200
        got = c.get(f"/api/review-docs/{v}/{TGT}").json()
        assert got["reverted"] is True
        assert c.post(f"/api/review-docs/{v}/{TGT}/pdf").status_code == 409
        # 이력이 통째로 지워져도(다른 데이터에서 다시 시작) 되돌린 기록은 남는다(폴백 리뷰 2라운드)
        from api.workspace import History
        History().clear()
        assert c.get(f"/api/review-docs/{v}/{TGT}").json()["reverted"] is True
        assert c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"], "body": "x"}).status_code == 409
        row = next(r for r in c.get("/api/review-docs").json()["confirms"] if r["version"] == v)
        assert row["reverted"] is True and row["projects"][0]["exists"] is True


def test_confirm_pushed_out_of_history_is_not_reverted(yearend):
    """다른 데이터로 바꾸거나 20판 밖으로 밀려 이력에서 빠진 확정은 되돌린 것이 아니다 -- 열고 고치고 내보낼 수 있다(폴백 리뷰 P1)."""
    from api.main import app
    from api.workspace import History
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        History().clear()
        got = c.get(f"/api/review-docs/{v}/{TGT}").json()
        assert got["reverted"] is False and got["history"] == "out_of_history"
        assert c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"], "body": "계속 고친다"}).status_code == 200
        row = next(r for r in c.get("/api/review-docs").json()["confirms"] if r["version"] == v)
        assert row["reverted"] is False and row["projects"][0]["exists"] is True


def test_base_version_reappearing_does_not_mark_a_live_confirm_reverted(yearend):
    """확정 직전 판이 다시 이력에 나타나도(같은 묶음을 다시 열고 고침) 되돌리기 기록이 없으면 되돌림이 아니다(폴백 리뷰 2라운드)."""
    from api.main import app
    from api.workspace import History
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        hist = History()
        entries = hist.entries()
        hist.clear()
        hist.append(entries[0])                     # 직전 판만 남은 이력(확정 판은 없음)
        got = c.get(f"/api/review-docs/{v}/{TGT}").json()
        assert got["reverted"] is False and got["history"] == "out_of_history"
        assert doc["revision"] == got["revision"]


def test_undone_confirm_stays_reverted_when_its_data_returns_as_a_base_version(yearend):
    """되돌린 뒤 같은 데이터를 다시 열면 이력의 시작 판(base)이 그 버전이 된다 -- 재확정이 아니므로 여전히 되돌려짐(Codex Stop 훅)."""
    from api.main import app
    from api.workspace import History
    with TestClient(app) as c:
        v = _confirm(c)
        _create(c, v)
        assert c.post("/api/workspace/undo", json={"dataset_version": v}).status_code == 200
        hist = History()
        hist.clear()
        hist.append({"version": v, "content_version": "x", "at": "t", "kind": "base", "note": "시작 데이터", "summary": {}})
        assert c.get(f"/api/review-docs/{v}/{TGT}").json()["reverted"] is True
        assert c.post(f"/api/review-docs/{v}/{TGT}/pdf").status_code == 409


def test_body_reset_and_judgment_author_kept(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        j = {PULLED: {"text": "판단", "agreement": {"status": "agreed", "memo": ""}}}
        out = c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"], "body": "고친 글", "judgments": j}).json()
        at = out["judgments"][PULLED]["at"]
        out = c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": out["revision"], "body": "", "judgments": j}).json()
        assert out["body"] is None and out["judgments"][PULLED]["at"] == at     # 빈 글 = AI 초안으로, 같은 판단은 시각 유지
        assert c.get("/api/review-docs/../J014").status_code in (404, 422)


def test_create_only_while_the_confirm_is_active(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        ws = _ws(c)
        c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": "J001", "locked": True})
        r = c.post(f"/api/review-docs/{v}/{TGT}")
        assert r.status_code == 409 and "새로 만들 수 없다" in r.text
        # 이 확정으로 사람이 들어가지 않은 사업은 404
        assert c.post(f"/api/review-docs/{v}/J001").status_code in (404, 409)


def test_list_has_one_doc_per_target_project(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        entries = [e for e in _entries(ws) if not (e["person_id"] == PULLED and e["project_id"] == SRC)]
        entries += [{"person_id": PULLED, "project_id": TGT, "alloc": 1.0}, {"person_id": BENCH, "project_id": "J015", "alloc": 1.0}]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "auto"})
        assert r.status_code == 200, r.text
        v = r.json()["workspace"]["dataset_version"]
        row = c.get("/api/review-docs").json()["confirms"][0]
        assert [p["project_id"] for p in row["projects"]] == ["J014", "J015"]
        d14 = c.post(f"/api/review-docs/{v}/J014").json()
        d15 = c.post(f"/api/review-docs/{v}/J015").json()
        assert [j["person_id"] for j in d14["moves"]["joined"]] == [PULLED]
        assert [j["person_id"] for j in d15["moves"]["joined"]] == [BENCH]


def test_draft_uses_ai_once_and_keeps_manager_body(yearend, monkeypatch):
    from api import deps
    from api.main import app
    calls = []

    def fake_generate(client, model, inp, **kw):
        calls.append(inp)
        return {"project_id": inp.project_id, "method": "graphrag", "text": "AI가 쓴 초안.", "facts": [], "addendum": None}
    monkeypatch.setattr("api.rag.justification.generate_justification", fake_generate)
    app.dependency_overrides[deps.get_openai_client_or_none] = lambda: SimpleNamespace()
    try:
        with TestClient(app) as c:
            v = _confirm(c, source="emergency")
            doc = _create(c, v)
            assert doc["confirm"]["kind"] == "emergency"
            r = c.post(f"/api/review-docs/{v}/{TGT}/draft")
            assert r.status_code == 200, r.text
            assert r.json()["body_ai"] == "AI가 쓴 초안." and len(calls) == 1
            # 긴급 투입은 새로 들어온 사람 사실만 AI에 넘긴다
            assert set(calls[0].team) == {PULLED, BENCH}
            assert c.post(f"/api/review-docs/{v}/{TGT}/draft").json()["body_ai"] == "AI가 쓴 초안." and len(calls) == 1
            got = c.get(f"/api/review-docs/{v}/{TGT}").json()
            c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": got["revision"], "body": "부서장 본문"})
            c.post(f"/api/review-docs/{v}/{TGT}/draft", params={"force": True})
            got = c.get(f"/api/review-docs/{v}/{TGT}").json()
            assert len(calls) == 2 and got["body"] == "부서장 본문"
    finally:
        app.dependency_overrides.clear()
