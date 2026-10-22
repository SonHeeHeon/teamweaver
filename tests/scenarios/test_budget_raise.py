"""예산 증액하고 확정(2026-10-22 사용자 "전부 하자" -- 화면 검토 P3-2): 손 편성이 월 예산을 새로 넘기면 막지 않고 사유와 함께 넘친 만큼 증액한다.
연말형 묶음 J019(특급 1·중급 2, 월 예산 3,950)에 정원 표에 없는 고급 한 명을 더해 예산을 넘긴다(정원 표에 없는 등급은 정원식 대상이
아니라 등급 대체가 아니다 -- 비용만 는다)."""
from fastapi.testclient import TestClient

from tests.scenarios.test_review_docs import _entries, _ws

TGT = "J019"


def _bench_team(c, ws):
    """J019에 넣어도 위반이 없는 대기 인력: 특급 1·중급 2 + 정원 표에 없는 고급 1(비용만 늘림)."""
    cand = c.get(f"/api/pipeline/{TGT}/candidates", params={"dataset_version": ws["dataset_version"]}).json()
    rows = [r for r in cand["bench"] if not r["new_violations"]]
    by = lambda g, n: [r["person_id"] for r in rows if r["grade"] == g][:n]   # noqa: E731
    picked = by("특급", 1) + by("중급", 2)
    extra = [r["person_id"] for r in cand["bench"] if r["grade"] == "고급"
             and not [v for v in r["new_violations"] if v[0] != "budget"]][:1]
    assert len(picked) == 3 and len(extra) == 1, (picked, extra)
    return picked + extra


def _plan(c):
    ws = _ws(c)
    team = _bench_team(c, ws)
    entries = _entries(ws) + [{"person_id": p, "project_id": TGT, "alloc": 1.0} for p in team]
    return ws, team, entries


def test_preview_and_draft_offer_the_raise_and_apply_needs_a_reason(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws, team, entries = _plan(c)
        p = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": entries}).json()
        amount = p["budget_raises"][TGT]
        assert amount > 0 and not p["grade_swaps"]
        assert not any("예산" in v for v in p["new_violations"])        # 증액을 적용해 판정한다(사유는 확정 때)
        # 신규 사업 draft(넣기로 고른 사람)도 같은 금액
        d = c.post(f"/api/pipeline/{TGT}/candidates", json={"dataset_version": ws["dataset_version"], "picks": [
            {"person_id": x, "alloc": 1.0, "pulled_from": []} for x in team]}).json()["draft"]
        assert d["budget_raises"] == {TGT: amount} and not any("예산" in v for v in d["new_violations"])
        base = {"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual"}
        r = c.post("/api/workspace/apply", json=base)                    # 증액 없이: 예전처럼 새 예산 위반으로 거절
        assert r.status_code == 422 and "위반" in r.text
        bad = [{"project_id": TGT, "amount": amount, "reason": "  "}]
        assert c.post("/api/workspace/apply", json={**base, "budget_raises": bad}).status_code == 422
        wrong = [{"project_id": TGT, "amount": amount + 1, "reason": "고객 요청"}]
        r = c.post("/api/workspace/apply", json={**base, "budget_raises": wrong})
        assert r.status_code == 422 and r.json()["detail"]["expected"] == {TGT: amount}
        emergency = c.post("/api/workspace/apply", json={**base, "source": "emergency",
                                                         "budget_raises": [{**wrong[0], "amount": amount}]})
        assert emergency.status_code == 422


def test_apply_with_reason_raises_budget_and_reaches_review_doc_and_change_report(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws, team, entries = _plan(c)
        before = next(p for p in c.get("/api/meta").json()["projects"] if p["id"] == TGT)["monthly_budget"]
        amount = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": entries}).json()["budget_raises"][TGT]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual",
                                                 "budget_raises": [{"project_id": TGT, "amount": amount, "reason": "고객 요청으로 고급 1명 추가"}]})
        assert r.status_code == 200, r.text
        v = r.json()["workspace"]["dataset_version"]
        after = next(p for p in c.get("/api/meta").json()["projects"] if p["id"] == TGT)["monthly_budget"]
        assert after == before + amount
        h = next(h for h in r.json()["workspace"]["history"] if h["kind"] == "apply_manual")
        doc = c.post(f"/api/review-docs/{v}/{TGT}").json()
        fact = next(f for f in doc["facts"] if f["kind"] == "MOVE_BUDGET")
        assert fact["adverse"] is True and "고객 요청으로 고급 1명 추가" in fact["text"] and f"+{amount:,}" in fact["text"]
        line = next(x for x in c.get("/api/changes").json()["items"][0]["reasons"] if x.startswith("예산 증액"))
        assert "고객 요청" in line and TGT in line
        # 확정 취소는 예산도 되돌린다
        ws = _ws(c)
        r = c.post("/api/workspace/cancel", json={"dataset_version": ws["dataset_version"], "target_version": h["version"],
                                                  "target_at": h["at"]})
        assert r.status_code == 200, r.text
        assert next(p for p in c.get("/api/meta").json()["projects"] if p["id"] == TGT)["monthly_budget"] == before
