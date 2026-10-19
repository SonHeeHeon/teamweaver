"""프로젝트 동시 수행 허용(2026-10-19 사용자 결정 -- 사람 단위, 사유 필수, 서비스 기본 전일 투입): 연말형 묶음으로 서버를 띄워
허용 → 두 사업 확정 → 해제 거절 → 되돌리기, 그리고 확정과 함께 허용(긴급 투입 '동시 수행으로 지원')을 지난다."""
from fastapi.testclient import TestClient


def _ws(c):
    return c.get("/api/workspace").json()


def _entries(ws):
    out = []
    for item in ws["pipeline"] + ws["ongoing"]:
        out += [{"person_id": t["person_id"], "project_id": item["project_id"], "alloc": t["alloc"]} for t in item["team"]]
    return out


def _member_and_overlap(ws):
    """잠기지 않은 진행 사업 팀원 한 명과, 그 사업과 기간이 겹치는 다른 진행 사업."""
    ongoing = ws["ongoing"]
    for a in ongoing:
        for t in a["team"]:
            if t["locked"] or t.get("unavailable"):
                continue
            for b in ongoing:
                if b["project_id"] != a["project_id"] and b["start"] <= a["end"] and a["start"] <= b["end"]:
                    return t["person_id"], a["project_id"], b["project_id"], t["grade"]
    raise AssertionError("겹치는 진행 사업이 없다")


def test_grant_needs_a_reason_and_lets_only_that_person_run_two_projects(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        assert ws["concurrency"] == []
        pid, own, other, grade = _member_and_overlap(ws)
        grow = {"headcount_add": {other: {grade: 1}}, "budget_add": {other: 5000}}      # 그 사업 자리 하나 더(보강)
        v = ws["dataset_version"]
        assert c.post("/api/workspace/concurrency", json={"dataset_version": v, "person_id": pid}).status_code == 422
        # 허용 안 된 사람을 겹치는 사업에 더하면 확정 거절(전일 투입: 같은 달 한 사업)
        two = _entries(ws) + [{"person_id": pid, "project_id": other, "alloc": 1.0}]
        r = c.post("/api/workspace/apply", json={"dataset_version": v, "entries": two, **grow})
        assert r.status_code == 422 and "concurrent_projects" in r.text
        r = c.post("/api/workspace/concurrency", json={"dataset_version": v, "person_id": pid, "reason": "기술 지원",
                                                       "memo": "잠깐 봐줌"})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        assert ws["dataset_version"] != v and ws["history"][0]["kind"] == "concurrency"
        assert [(g["person_id"], g["reason"], g["max_projects"]) for g in ws["concurrency"]] == [(pid, "기술 지원", 2)]
        person = next(p for p in c.get("/api/meta").json()["people"] if p["id"] == pid)
        assert person["max_concurrent"] == 2 and person["concurrency"]["memo"] == "잠깐 봐줌"
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": _entries(ws)
                                                 + [{"person_id": pid, "project_id": other, "alloc": 1.0}], **grow})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        # 두 사업에 있는 동안은 해제할 수 없다
        assert c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": pid,
                                                          "allow": False}).status_code == 422
        ws = c.post("/api/workspace/undo", json={"dataset_version": ws["dataset_version"]}).json()["workspace"]
        r = c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": pid,
                                                       "allow": False})
        assert r.status_code == 200 and r.json()["workspace"]["concurrency"] == []


def test_grant_recorded_with_the_confirm_and_undone_with_it(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        pid, own, other, grade = _member_and_overlap(ws)
        entries = _entries(ws) + [{"person_id": pid, "project_id": other, "alloc": 1.0}]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries,
                                                 "source": "emergency", "headcount_add": {other: {grade: 1}},
                                                 "budget_add": {other: 5000},
                                                 "concurrency_add": [{"person_id": pid, "memo": f"{other} 장애 대응"}]})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        assert [(g["person_id"], g["reason"]) for g in ws["concurrency"]] == [(pid, "긴급 지원")]
        assert pid in {t["person_id"] for o in ws["ongoing"] if o["project_id"] == other for t in o["team"]}
        assert pid in {t["person_id"] for o in ws["ongoing"] if o["project_id"] == own for t in o["team"]}
        ws = c.post("/api/workspace/undo", json={"dataset_version": ws["dataset_version"]}).json()["workspace"]
        assert ws["concurrency"] == []


def test_empty_seats_get_concurrency_candidates_from_busy_people_only(yearend):
    """빈자리마다 후보: 같은 등급·자격·그 기간 다른 사업에 있음·예산 안(다시 풀지 않는다)."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        proposal = next(p for p in ws["pipeline"] if p["stage"] == "제안")    # 신규 제안 -- 아직 아무도 없다
        r = c.post("/api/concurrency/candidates", json={"dataset_version": ws["dataset_version"], "entries": _entries(ws)})
        assert r.status_code == 200, r.text
        rows = [s for s in r.json()["shortfalls"] if s["project_id"] == proposal["project_id"]]
        assert rows and all(s["missing"] >= 1 for s in rows) and any(s["candidates"] for s in rows)
        busy = {t["person_id"] for o in ws["ongoing"] for t in o["team"]}
        for s in rows:
            for cand in s["candidates"]:
                assert cand["person_id"] in busy and cand["busy_with"] and cand["name"] != cand["person_id"]
                assert cand["grade"] == s["grade"] and cand["monthly_cost"] <= s["budget_left"]
        assert c.post("/api/concurrency/candidates", json={"dataset_version": "old", "entries": []}).status_code == 409


def test_review_doc_shows_the_concurrency_grant_as_a_locked_fact(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        pid, own, other, grade = _member_and_overlap(ws)
        entries = _entries(ws) + [{"person_id": pid, "project_id": other, "alloc": 1.0}]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries,
                                                 "source": "emergency", "headcount_add": {other: {grade: 1}},
                                                 "budget_add": {other: 5000},
                                                 "concurrency_add": [{"person_id": pid, "memo": "장애 대응 2주"}]})
        assert r.status_code == 200, r.text
        v = r.json()["workspace"]["dataset_version"]
        doc = c.post(f"/api/review-docs/{v}/{other}")
        assert doc.status_code == 200, doc.text
        doc = doc.json()
        joined = {j["person_id"]: j for j in doc["moves"]["joined"]}
        assert joined[pid]["origin"] == "partly_free" and joined[pid]["concurrency"]["reason"] == "긴급 지원"
        texts = [f["text"] for f in doc["facts"] if f["kind"] == "MOVE_CONCURRENT"]
        assert texts and "긴급 지원" in texts[0] and "장애 대응 2주" in texts[0]
        assert any("프로젝트 동시 수행(원래 사업 유지)" in f["text"] for f in doc["facts"] if f["kind"] == "MOVE_WHO")


def test_emergency_offers_concurrent_support_that_needs_a_grant(yearend):
    """긴급 투입(전일 투입): 다른 사업에 있는 사람도 '동시 수행으로 지원' 후보(needs_grant) -- 투입률 100%, 원래 사업은 그대로."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        target = next(o["project_id"] for o in ws["ongoing"] if not o["ends_in_horizon"] and o["project_id"] != "J001")
        r = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": target, "n": 1})
        assert r.status_code == 200, r.text
        spare = r.json()["candidates"]["spare"]
        assert spare and all(x["source"] == "concurrent" and x["needs_grant"] and x["alloc"] == 1.0 for x in spare)
        assert all(not x["pulled_from"] for x in spare)
        assert all(x["needs_grant"] is False for x in r.json()["candidates"]["bench"])


def test_regrant_below_current_overlap_and_empty_revoke_are_refused_and_grants_are_kept(yearend):
    """폴백 리뷰 P2·P3: 지금 겹침보다 낮게 다시 걸기 거절, 허용 없는 사람 해제 거절(빈 판 안 만듦), 확정의 concurrency_add가 기존 기록을 덮지 않음."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        pid, own, other, grade = _member_and_overlap(ws)
        assert c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": pid,
                                                          "allow": False}).status_code == 422
        ws = c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": pid,
                                                        "reason": "기술 지원", "memo": "원래 사유", "max_projects": 3}).json()["workspace"]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "source": "emergency",
                                                 "entries": _entries(ws) + [{"person_id": pid, "project_id": other, "alloc": 1.0}],
                                                 "headcount_add": {other: {grade: 1}}, "budget_add": {other: 5000},
                                                 "concurrency_add": [{"person_id": pid}]})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        assert [(g["reason"], g["memo"], g["max_projects"]) for g in ws["concurrency"]] == [("기술 지원", "원래 사유", 3)]
        third = next(o for o in ws["ongoing"] if o["project_id"] not in (own, other)
                     and o["start"] <= max(a["end"] for a in ws["ongoing"] if a["project_id"] in (own, other)))
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"],
                                                 "entries": _entries(ws) + [{"person_id": pid, "project_id": third["project_id"], "alloc": 1.0}],
                                                 "headcount_add": {third["project_id"]: {grade: 1}}, "budget_add": {third["project_id"]: 5000}})
        if r.status_code == 200:                         # 세 사업이 같은 달에 겹치면 -- 2로 다시 걸기는 거절
            ws = r.json()["workspace"]
            r2 = c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": pid,
                                                            "reason": "기술 지원", "max_projects": 2})
            assert r2.status_code == 422 and "줄일 수 없다" in r2.text


def test_emergency_keeps_bench_and_pull_candidates_next_to_concurrency_offers(yearend):
    """폴백 리뷰 P2: 동시 수행 후보를 따로 구해 빼 오기·대기 후보가 밀려나지 않는다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        target = next(o["project_id"] for o in ws["ongoing"] if not o["ends_in_horizon"] and o["project_id"] != "J001")
        body = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": target, "n": 1,
                                                   "pull_budget": 1}).json()["candidates"]
        assert body["bench"] and body["pull"] and body["spare"]
        assert all(x["source"] == "pull" for x in body["pull"]) and all(x["source"] == "concurrent" for x in body["spare"])


def test_concurrent_support_rows_carry_the_server_hint_that_confirms_cleanly(yearend):
    """폴백 리뷰 2라운드 P2: 지원 행마다 서버가 계산한 증원(넘친 만큼) -- 그대로 확정하면 새 위반 없이 통과한다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        target = next(o["project_id"] for o in ws["ongoing"] if not o["ends_in_horizon"] and o["project_id"] != "J001")
        plan = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": target, "n": 1,
                                                   "skills": ["Kubernetes"]}).json()
        row = next(x for x in plan["candidates"]["spare"] if x["needs_grant"] and not x["new_violations"])
        hint = row["apply_hint"]
        assert set(hint) == {"headcount_add", "budget_add", "skills_add"} and hint["skills_add"]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "source": "emergency",
                                                 "entries": _entries(ws) + [{"person_id": row["person_id"], "project_id": target,
                                                                             "alloc": 1.0}],
                                                 "concurrency_add": [{"person_id": row["person_id"]}], **hint})
        assert r.status_code == 200, r.text


def test_a_pre_granted_person_gets_a_hint_and_keeps_the_grant(yearend):
    """Codex 리뷰(2026-10-19): 미리 허용받은 사람은 일반 후보에 source="concurrent"·needs_grant=False로 나온다 -- 그 행에도 확정 힌트가
    있어야 화면이 지원을 확정할 수 있고, 확정해도 원래 허용 기록(사유·메모)은 그대로다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        pid, own, other, grade = _member_and_overlap(ws)
        r = c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": pid, "allow": True,
                                                       "reason": "기술 지원", "memo": "미리 허용"})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        plan = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": other, "n": 1}).json()
        row = next((x for x in plan["candidates"]["spare"] if x["person_id"] == pid), None)
        assert row is not None and row["source"] == "concurrent" and row["needs_grant"] is False
        assert set(row["apply_hint"]) == {"headcount_add", "budget_add", "skills_add"}
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "source": "emergency",
                                                 "entries": _entries(ws) + [{"person_id": pid, "project_id": other, "alloc": 1.0}],
                                                 **row["apply_hint"]})
        assert r.status_code == 200, r.text
        grant = next(g for g in r.json()["workspace"]["concurrency"] if g["person_id"] == pid)
        assert grant["reason"] == "기술 지원" and grant["memo"] == "미리 허용"
