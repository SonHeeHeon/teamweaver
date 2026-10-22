"""변경 기록·변경 보고서(api/change_log, api/routes/changes -- 2026-10-22 claude-a, 사용자 "액션 하나하나에 대한 것도 보고서로 … 기간으로 잡고 …
인사팀 보고 사항들을 정리해서 함께 pdf에 묶는것"). 연말형 묶음에서 J004의 한 명을 빼 오고 대기 인력 한 명을 더해 J018을 확정한다(검토 자료 시험과 같은 장면)."""
import pytest
from fastapi.testclient import TestClient

from tests.scenarios.test_review_docs import BENCH, PULLED, SRC, TGT, _confirm, _create, _ws


def _items(c, **params):
    r = c.get("/api/changes", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_confirm_is_logged_with_names_reasons_and_review_targets(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        it = _items(c)[0]
        assert it["kind"] == "apply_manual" and it["version"] == v and it["status"] is None and it["review_kind"]
        moved = {x["person_id"]: x for x in it["people"]["moved"]}
        joined = {x["person_id"]: x for x in it["people"]["joined"]}
        assert moved[PULLED]["from"] == [SRC] and moved[PULLED]["to"] == [TGT] and moved[PULLED]["name"]   # 그때 이름(가상 데이터)
        assert joined[BENCH]["to"] == [TGT] and joined[BENCH]["grade"]
        assert it["projects"][TGT] != TGT                      # 사업 이름도 그때 값
        # 빼 온 자리를 메우지 않은 결정과 사유가 줄로 남는다
        assert any(line.startswith("빼 온 자리 메우지 않음") and "사유 시험" in line for line in it["reasons"])
        assert it["review"] == [{"project_id": TGT, "name": it["projects"][TGT], "exists": False, "status": None, "objections": 0}]
        _create(c, v)
        assert _items(c)[0]["review"][0]["exists"] is True


def test_undo_and_cancel_mark_the_confirm(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _confirm(c)
        ws = _ws(c)
        assert c.post("/api/workspace/undo", json={"dataset_version": ws["dataset_version"]}).status_code == 200
        items = _items(c)
        assert items[0]["kind"] == "undo" and "되돌린 변경: 손 편성 확정" in items[0]["reasons"][0]
        assert {x["person_id"] for x in items[0]["people"]["moved"]} == {PULLED}     # 되돌리기도 사람 변화를 남긴다
        confirm = next(x for x in items if x["version"] == v and x["kind"] == "apply_manual")
        assert confirm["status"] == "undone"
        # 다시 확정하고 확정 취소 → 그 확정은 "cancelled"
        _confirm(c)
        ws = _ws(c)
        h = next(h for h in ws["history"] if h["kind"] == "apply_manual")
        r = c.post("/api/workspace/cancel", json={"dataset_version": ws["dataset_version"], "target_version": h["version"],
                                                  "target_at": h["at"]})
        assert r.status_code == 200, r.text
        items = _items(c)
        assert items[0]["kind"] == "cancel" and items[0]["reasons"][0].startswith("취소한 확정")
        again = next(x for x in items if x["kind"] == "apply_manual" and x["at"] == h["at"])
        assert again["status"] == "cancelled"


def test_period_filter_and_bad_time(yearend):
    from api.main import app
    with TestClient(app) as c:
        _confirm(c)
        assert len(_items(c)) == 1
        assert _items(c, start="2999-01-01T00:00:00+09:00") == []
        assert len(_items(c, start="2000-01-01T00:00:00Z", end="2999-01-01T00:00:00Z")) == 1
        assert c.get("/api/changes", params={"start": "어제"}).status_code == 422


def test_log_survives_history_reset_and_backfills_old_history(yearend):
    """기록은 되돌리기용 이력(20판, 다른 데이터를 열면 비움)과 따로 남는다. 기록 전에 생긴 이력은 보관 판에서 메운다."""
    from api.change_log import ChangeLog
    from api.main import app
    from api.workspace import History
    with TestClient(app) as c:
        v = _confirm(c)
        assert len(ChangeLog().entries()) == 1
        ChangeLog().path.unlink()                              # 기록이 생기기 전의 이력처럼
        it = _items(c)[0]
        assert it["backfilled"] and it["version"] == v and {x["person_id"] for x in it["people"]["moved"]} == {PULLED}
        assert it["projects"][TGT] != TGT                      # 사업 이름은 보관 판의 표에서
        assert it["review"][0]["project_id"] == TGT
        # 기록이 있으면 이력을 비워도(다른 데이터를 연 것처럼) 남는다
        ws = _ws(c)
        r = c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": BENCH, "allow": True,
                                                        "max_projects": 2, "reason": "긴급 지원"})
        assert r.status_code == 200, r.text
        History().clear()
        assert [x["kind"] for x in _items(c)] == ["concurrency"]


def test_report_payload_bundles_review_docs_and_rejects_unknown_ids(yearend, monkeypatch):
    from api.main import app
    seen = {}

    async def fake_render(request, payload):
        seen["payload"] = payload
        return b"%PDF-fake"
    monkeypatch.setattr("api.routes.report.render_review_pdf", fake_render)
    with TestClient(app) as c:
        v = _confirm(c)
        doc = _create(c, v)
        loss = next(f for f in doc["facts"] if f["kind"] == "MOVE_LOSS")
        r = c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"],
                                                       "notes": [{"key": loss["key"], "type": "objection", "text": "경력 누락"}]})
        assert r.status_code == 200, r.text
        it = _items(c)[0]
        r = c.post("/api/changes/report", json={"ids": [it["id"]], "start": "2026-10-01T00:00:00Z"})
        assert r.status_code == 200, r.text
        assert r.content == b"%PDF-fake" and r.headers["content-disposition"].startswith('attachment; filename="change-report-')
        p = seen["payload"]
        assert p["kind"] == "changes" and p["period"]["start"] == "2026-10-01T00:00:00Z"
        assert p["totals"]["changes"] == 1 and p["totals"]["moved"] == 1 and p["totals"]["objections"] == 1
        docs = p["items"][0]["review_docs"]
        assert docs[0]["doc"]["project_id"] == TGT and docs[0]["doc"]["notes"][0]["type"] == "objection"
        assert c.get(f"/api/review-docs/{v}/{TGT}").json()["status"] == "draft"     # 묶음 PDF는 "내보냄"으로 바꾸지 않는다
        assert c.post("/api/changes/report", json={"ids": ["nope"]}).status_code == 404
        assert c.post("/api/changes/report", json={"ids": []}).status_code == 422


@pytest.mark.parametrize("kind", ["concurrency"])
def test_other_changes_have_reason_lines(yearend, kind):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        r = c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": BENCH, "allow": True,
                                                        "max_projects": 2, "reason": "긴급 지원", "memo": "J004 장애"})
        assert r.status_code == 200, r.text
        it = _items(c)[0]
        assert it["kind"] == kind and it["label"] == "프로젝트 동시 수행 허용"
        assert "사유 긴급 지원" in it["reasons"][0] and "J004 장애" in it["reasons"][0]
        assert not it["reasons"][0].startswith(BENCH)          # 사번이 아니라 그때 이름(가상 데이터)


def test_review_doc_list_keeps_the_project_name_of_that_confirm(yearend):
    """부서장 점검 f1: 목록의 사업 이름이 지금 데이터 기준이라 나중에 이름이 바뀌면(다른 데이터를 열거나 양식을 고치면) 옛 확정이 틀린 이름을 보였다."""
    from api.main import app
    with TestClient(app) as c:
        _confirm(c)
        ws = _ws(c)
        old = c.get("/api/review-docs").json()["confirms"][0]["projects"][0]["name"]
        form = c.get(f"/api/workspace/projects/{TGT}/form").json()
        r = c.patch(f"/api/workspace/projects/{TGT}", json={"dataset_version": ws["dataset_version"],
                                                            "project": {**form, "name": form["name"] + " 개명"}})
        assert r.status_code == 200, r.text
        row = next(x for x in c.get("/api/review-docs").json()["confirms"] if x["projects"])
        assert row["active"] is False and row["projects"][0]["name"] == old
