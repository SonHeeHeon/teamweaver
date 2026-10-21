"""확정 취소(2026-10-21 사용자 요청 3): 확정 한 건만 지금 판 위에서 거꾸로 적용한다 -- 빼 온 사람은 원래 사업으로,
그 뒤 다른 변경(고정 등)은 그대로, 그 뒤 바뀐 배치는 건너뜀, 원래 사업 복귀가 위반이면 대기, 긴급 투입은 증원까지."""
from fastapi.testclient import TestClient

from tests.scenarios.holes import waivers

from tests.scenarios.test_workspace_api import _entries, _ws


def _team(ws, pid):
    return {t["person_id"] for item in ws["pipeline"] + ws["ongoing"] if item["project_id"] == pid for t in item["team"]}


def _apply(c, ws, entries, source="manual", **kw):
    kw.setdefault("seat_waivers", waivers(c, ws["dataset_version"], entries))     # 빼 온 자리는 "메우지 않음"(2026-10-21)
    r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries,
                                             "source": source, **kw})
    assert r.status_code == 200, r.text
    return r.json()["workspace"]


def _confirm_entry(ws, kind):
    return next(h for h in ws["history"] if h["kind"] == kind)


def _preview(c, ws, h):
    return c.get("/api/workspace/cancel", params={"dataset_version": ws["dataset_version"], "version": h["version"],
                                                  "at": h["at"]})


def _cancel(c, ws, h):
    return c.post("/api/workspace/cancel", json={"dataset_version": ws["dataset_version"], "target_version": h["version"],
                                                 "target_at": h["at"]})


def _pick(c, ws, pid="J018"):
    """신규 사업 후보 중 넣어도 위반이 없는 대기 인력 하나·빼 오기 하나."""
    cand = c.get(f"/api/pipeline/{pid}/candidates", params={"dataset_version": ws["dataset_version"]}).json()
    bench = next(r for r in cand["bench"] if not r["new_violations"] and not r.get("grade_swap"))
    # 고른 사람을 팀원으로 친 다시 채점(화면 "넣기"와 같은 길) -- 따로 고르면 같은 등급 자리가 넘칠 수 있다
    cand = c.post(f"/api/pipeline/{pid}/candidates", json={"dataset_version": ws["dataset_version"], "picks": [
        {"person_id": bench["person_id"], "alloc": bench["alloc"], "pulled_from": []}]}).json()
    pull = next(r for r in cand["pull"] if not r["new_violations"] and not r.get("grade_swap") and len(r["pulled_from"]) == 1)
    return bench, pull


def test_cancel_restores_the_pulled_person_and_keeps_a_later_lock(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        bench, pull = _pick(c, ws)
        src = pull["pulled_from"][0]
        need = next(o for o in ws["ongoing"] if o["project_id"] == src)["seats"][pull["grade"]]["need"]
        entries = [e for e in _entries(ws) if not (e["person_id"] == pull["person_id"] and e["project_id"] == src)]
        entries += [{"person_id": bench["person_id"], "project_id": "J018", "alloc": 1.0},
                    {"person_id": pull["person_id"], "project_id": "J018", "alloc": 1.0}]
        ws = _apply(c, ws, entries)
        assert {bench["person_id"], pull["person_id"]} <= _team(ws, "J018")
        confirm = _confirm_entry(ws, "apply_manual")
        assert confirm["cancellable"] is True and confirm["cancelled"] is False
        assert confirm["projects"] == ["J018"]                  # "지금 팀" 옆 확정 취소가 이 사업의 마지막 확정을 찾는다
        assert confirm["joined"] == {"J018": sorted([bench["person_id"], pull["person_id"]])}
        # 확정 뒤 다른 변경: J001 사업 고정 -- 되돌리기라면 이것까지 사라졌다
        ws = c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": "J001",
                                                 "locked": True}).json()["workspace"]

        p = _preview(c, ws, confirm)
        assert p.status_code == 200, p.text
        view = p.json()
        assert view["ok"] is True and not view["skipped"] and not view["dropped"]
        # 사람마다 한 줄: 빼 온 사람은 "J018 → J008로 돌아간다(원래 사업)", 대기 인력은 "J018에서 뺀다"
        assert any(f"J018 → {src}" in line and "돌아간다(원래 사업)" in line for line in view["revert"])
        assert sum("J018에서 뺀다" in line for line in view["revert"]) == 1
        # 확정 때 "메우지 않음"으로 줄인 원래 사업 정원도 그 사람이 돌아오며 되살아난다(2026-10-21)
        assert [line for line in view["revert"] if "정원" in line] == [f"{src} {pull['grade']} 정원 {need - 1}명 → {need}명"]
        assert len(view["revert"]) == 3

        r = _cancel(c, ws, confirm)
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        assert not ({bench["person_id"], pull["person_id"]} & _team(ws, "J018"))
        assert pull["person_id"] in _team(ws, src)                          # 원래 사업으로 돌아갔다
        assert bench["person_id"] in {b["person_id"] for b in ws["bench"]}
        assert next(o for o in ws["ongoing"] if o["project_id"] == "J001")["locked_all"] is True   # 뒤 변경은 그대로
        assert ws["history"][0]["kind"] == "cancel" and ws["history"][0]["note"].startswith("확정 취소")
        again = _confirm_entry(ws, "apply_manual")
        assert again["cancelled"] is True and again["cancellable"] is False
        assert _cancel(c, ws, again).status_code == 409                     # 두 번 취소하지 않는다

        # 인사팀 검토 자료 목록: 취소된 확정은 잠긴다
        row = next(x for x in c.get("/api/review-docs").json()["confirms"] if x["version"] == confirm["version"])
        assert row["cancelled"] is True and row["reverted"] is True

        # 취소도 판이라 되돌리기로 무를 수 있다 -- 그러면 확정이 다시 살아난다
        r = c.post("/api/workspace/undo", json={"dataset_version": ws["dataset_version"]})
        ws = r.json()["workspace"]
        assert _confirm_entry(ws, "apply_manual")["cancelled"] is False
        assert pull["person_id"] in _team(ws, "J018")


def test_a_later_lock_on_a_confirmed_seat_is_kept_and_reported(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        first, _ = _pick(c, ws)
        cand = c.post("/api/pipeline/J018/candidates", json={"dataset_version": ws["dataset_version"], "picks": [
            {"person_id": first["person_id"], "alloc": 1.0, "pulled_from": []}]}).json()
        # 등급 대체 후보(2026-10-21 -- 사유가 있어야 확정)는 빼고 등급이 맞는 사람
        two = [first["person_id"], next(r["person_id"] for r in cand["bench"] if not r["new_violations"] and not r.get("grade_swap"))]
        ws = _apply(c, ws, _entries(ws) + [{"person_id": p, "project_id": "J018", "alloc": 1.0} for p in two])
        confirm = _confirm_entry(ws, "apply_manual")
        ws = c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": "J018",
                                                 "people": [two[0]], "locked": True}).json()["workspace"]
        view = _preview(c, ws, confirm).json()
        assert len(view["skipped"]) == 1 and "고정" in view["skipped"][0]
        ws = _cancel(c, ws, confirm).json()["workspace"]
        assert _team(ws, "J018") == {two[0]}


def test_nothing_left_to_cancel_is_refused(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        cand = c.get("/api/pipeline/J018/candidates", params={"dataset_version": ws["dataset_version"]}).json()
        p = next(r["person_id"] for r in cand["bench"] if not r["new_violations"] and not r.get("grade_swap"))
        ws = _apply(c, ws, _entries(ws) + [{"person_id": p, "project_id": "J018", "alloc": 1.0}])
        confirm = _confirm_entry(ws, "apply_manual")
        ws = _apply(c, ws, [e for e in _entries(ws) if e["person_id"] != p or e["project_id"] != "J018"])   # 그 뒤 손으로 뺐다
        r = _cancel(c, ws, confirm)
        assert r.status_code == 422 and "되돌릴 것이 없다" in r.text


def test_a_refilled_source_seat_leaves_the_returning_person_on_the_bench(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        _, pull = _pick(c, ws)
        mover, src = pull["person_id"], pull["pulled_from"][0]
        ws = _apply(c, ws, [e for e in _entries(ws) if not (e["person_id"] == mover and e["project_id"] == src)]
                    + [{"person_id": mover, "project_id": "J018", "alloc": 1.0}])
        confirm = _confirm_entry(ws, "apply_manual")
        # 그 뒤 원래 사업에 같은 등급 대기 인력을 넣는다 -- 첫 확정이 그 자리를 "메우지 않음"으로 줄였으니(2026-10-21) 긴급 투입 증원으로
        grade = next(t["grade"] for o in ws["pipeline"] for t in o["team"] if t["person_id"] == mover)
        filler = next(b["person_id"] for b in ws["bench"] if b["grade"] == grade and not b.get("unavailable"))
        ws = _apply(c, ws, _entries(ws) + [{"person_id": filler, "project_id": src, "alloc": 1.0}], source="emergency",
                    headcount_add={src: {grade: 1}})
        view = _preview(c, ws, confirm).json()
        assert view["ok"] is True and len(view["dropped"]) == 1 and "대기 인력으로 남는다" in view["dropped"][0]
        ws = _cancel(c, ws, confirm).json()["workspace"]
        assert mover not in _team(ws, "J018") and mover not in _team(ws, src)
        assert mover in {b["person_id"] for b in ws["bench"]} and filler in _team(ws, src)


def test_cancelling_an_emergency_confirm_takes_back_the_added_seats(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        before = next(o for o in ws["ongoing"] if o["project_id"] == "J007")
        plan = c.post("/api/emergency/plan", json={"dataset_version": ws["dataset_version"], "project_id": "J007", "n": 1,
                                                   "skills": ["Kubernetes"], "pull_budget": 0}).json()
        ws = _apply(c, ws, plan["best"]["entries"], source="emergency", note="J007 장애 대응", **plan["apply_hint"])
        grown = next(o for o in ws["ongoing"] if o["project_id"] == "J007")
        assert grown["monthly_budget"] > before["monthly_budget"]
        confirm = _confirm_entry(ws, "apply_emergency")
        view = _preview(c, ws, confirm).json()
        assert any("월 예산" in line for line in view["revert"]) and any("정원" in line for line in view["revert"])
        ws = _cancel(c, ws, confirm).json()["workspace"]
        after = next(o for o in ws["ongoing"] if o["project_id"] == "J007")
        assert after["monthly_budget"] == before["monthly_budget"] and after["seats"] == before["seats"]
        assert _team(ws, "J007") == {t["person_id"] for t in before["team"]}


def test_only_confirms_in_this_history_can_be_cancelled(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        ws = c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": "J001",
                                                 "locked": True}).json()["workspace"]
        lock = ws["history"][0]
        assert lock["cancellable"] is False
        assert _preview(c, ws, lock).status_code == 422                     # 고정은 확정이 아니다
        assert _preview(c, ws, {"version": "nope", "at": "x"}).status_code == 404


def test_nothing_left_after_locks_and_violations_is_blocked(yearend):
    """폴백 리뷰 M1: 확정이 넣은 배치를 그 뒤 고정했고 원래 사업 복귀는 겹침 위반이면 바뀌는 것이 없다 -- 취소로 잠그지 않는다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        _, pull = _pick(c, ws)
        mover, src = pull["person_id"], pull["pulled_from"][0]
        ws = _apply(c, ws, [e for e in _entries(ws) if not (e["person_id"] == mover and e["project_id"] == src)]
                    + [{"person_id": mover, "project_id": "J018", "alloc": 1.0}])
        confirm = _confirm_entry(ws, "apply_manual")
        ws = c.post("/api/workspace/lock", json={"dataset_version": ws["dataset_version"], "project_id": "J018",
                                                 "people": [mover], "locked": True}).json()["workspace"]
        view = _preview(c, ws, confirm).json()
        assert view["ok"] is False and view["revert"] == [] and "되돌릴 수 있는 것이 없다" in view["blocked"]
        assert not any("대기 인력으로 남는다" in d for d in view["dropped"])     # 그 사람은 J018에 그대로다
        assert view["new_violations"] == []                                     # 바뀌는 것이 없으니 생기는 위반도 없다
        r = _cancel(c, ws, confirm)
        assert r.status_code == 422
        assert _confirm_entry(_ws(c), "apply_manual")["cancelled"] is False


def test_a_dropped_source_project_is_skipped_not_fatal(yearend):
    """폴백 리뷰 S1: 원래 사업이 그 뒤 없어졌으면 그 복귀만 건너뛴다(묶음 검증이 통째로 실패하지 않게)."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        bench, _ = _pick(c, ws, "J019")
        p = bench["person_id"]
        ws = _apply(c, ws, _entries(ws) + [{"person_id": p, "project_id": "J019", "alloc": 1.0}])
        ws = _apply(c, ws, [e for e in _entries(ws) if not (e["person_id"] == p and e["project_id"] == "J019")]
                    + [{"person_id": p, "project_id": "J018", "alloc": 1.0}])
        second = ws["history"][0]
        ws = c.delete("/api/workspace/projects/J019", params={"dataset_version": ws["dataset_version"]}).json()["workspace"]
        view = _preview(c, ws, second).json()
        assert view["ok"] is True and any("지금 데이터에 없어" in line for line in view["skipped"])
        ws = _cancel(c, ws, second).json()["workspace"]
        assert p not in _team(ws, "J018") and p in {b["person_id"] for b in ws["bench"]}


def test_cancel_is_remembered_and_undo_of_the_cancel_forgets_it(yearend):
    """폴백 리뷰 S3·S4: 취소는 이력 밖 기록에도 남고, 취소 판을 되돌리면 지운다(앞 확정을 '되돌려짐'으로 남기지 않는다)."""
    from api.main import app
    from api.review_docs import cancelled_records, history_state, undone_versions
    with TestClient(app) as c:
        ws = _ws(c)
        bench, _ = _pick(c, ws)
        ws = _apply(c, ws, _entries(ws) + [{"person_id": bench["person_id"], "project_id": "J018", "alloc": 1.0}])
        confirm = _confirm_entry(ws, "apply_manual")
        ws = _cancel(c, ws, confirm).json()["workspace"]
        assert (confirm["version"], confirm["at"]) in cancelled_records()
        # 이력 밖으로 밀린 뒤에도 잠긴다
        assert history_state([], {"version": confirm["version"], "at": confirm["at"]}) == "reverted"
        from api.review_docs import confirm_cancelled
        assert confirm_cancelled([], confirm["version"]) is True                  # 꼬리표도 "확정 취소됨"
        ws = c.post("/api/workspace/undo", json={"dataset_version": ws["dataset_version"]}).json()["workspace"]
        assert (confirm["version"], confirm["at"]) not in cancelled_records()
        assert not undone_versions()                    # 꺼낸 것은 취소 판 -- 어떤 확정도 "되돌려짐"이 아니다


def test_a_plan_computed_on_an_older_version_is_not_committed(yearend, monkeypatch):
    """폴백 리뷰 M2: 계산하는 동안 판이 바뀌면 옛 판 위의 취소를 켜지 않는다."""
    import api.routes.workspace as w
    from api.main import app
    real = w.cancel_plan
    monkeypatch.setattr(w, "cancel_plan", lambda *a: {**real(*a), "version": "older"})
    with TestClient(app) as c:
        ws = _ws(c)
        bench, _ = _pick(c, ws)
        ws = _apply(c, ws, _entries(ws) + [{"person_id": bench["person_id"], "project_id": "J018", "alloc": 1.0}])
        r = _cancel(c, ws, _confirm_entry(ws, "apply_manual"))
        assert r.status_code == 409
        assert _ws(c)["history"][0]["kind"] == "apply_manual"
