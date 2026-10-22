"""빼 온 자리 결정(2026-10-21 사용자 "빼간 자리를 매꿔야한다는게 있어야할거같네. 못메꾼다면 메꾸지 않는다라는 표기를 통해 더이상 그건
컨트롤 안해도 되는걸로" -- 추천대로: 모든 확정 경로, 예산 그대로): 확정으로 새로 생긴 빈자리는 같은 확정에서 메우거나 "메우지 않음"(사유)으로
그 등급 정원을 줄여야 확정된다. 미리 계산(긴급 투입·신규 사업·확정 창)과 확정 검사가 같은 함수다."""
from fastapi.testclient import TestClient

from core.evaluate.seat_holes import new_holes, same_holes, waived_need
from tests.scenarios.test_workspace_api import _entries, _ws


def _seats(ws, pid):
    return next(p for p in ws["pipeline"] + ws["ongoing"] if p["project_id"] == pid)["seats"]


def _pull_pick(c, v):
    """J007 긴급 투입에서 다른 사업에서 빼 오는 후보 하나(위반 없음)."""
    plan = c.post("/api/emergency/plan", json={"dataset_version": v, "project_id": "J007", "n": 1, "pull_budget": 1}).json()
    pl = next(r for r in plan["candidates"]["pull"] if not r["new_violations"] and len(r["pulled_from"]) == 1)
    return plan, {"person_id": pl["person_id"], "alloc": pl["alloc"], "pulled_from": pl["pulled_from"], "grant": False}, pl


def _preview(c, v, pick, fills=()):
    r = c.post("/api/emergency/preview", json={"dataset_version": v, "project_id": "J007", "picks": [pick],
                                                "fills": list(fills)})
    assert r.status_code == 200, r.text
    return r.json()["draft"]


def _apply(c, v, d, **kw):
    return c.post("/api/workspace/apply", json={"dataset_version": v, "entries": d["entries"], "source": "emergency",
                                                "note": "J007 긴급 투입", **d["apply_hint"], **kw})


def test_pulled_seat_must_be_filled_or_waived_with_a_reason(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        v = ws["dataset_version"]
        _, pick, pl = _pull_pick(c, v)
        src, grade = pl["pulled_from"][0], pl["grade"]
        d = _preview(c, v, pick)
        assert d["holes"] == {src: [{"grade": grade, "count": 1}]} and d["fills"] == []
        opts = d["fill_options"][src]
        assert opts and opts[0]["grade"] == grade                       # 같은 등급이 먼저

        r = _apply(c, v, d)                                              # 아무것도 정하지 않으면 거절
        assert r.status_code == 422 and "메우지 않음" in r.text
        waiver = {"project_id": src, "grade": grade, "count": 1, "reason": " "}
        assert _apply(c, v, d, seat_waivers=[waiver]).status_code == 422   # 사유가 비면 거절

        need = _seats(ws, src)[grade]["need"]
        r = _apply(c, v, d, seat_waivers=[{**waiver, "reason": "남은 기간은 지금 인원으로 충분"}])
        assert r.status_code == 200, r.text
        ws2 = r.json()["workspace"]
        assert _seats(ws2, src).get(grade, {"need": 0})["need"] == need - 1     # 정원 −1(예산 그대로)
        top = ws2["history"][0]
        assert top["summary"]["seat_waivers"] == [{**waiver, "reason": "남은 기간은 지금 인원으로 충분"}]
        # 줄인 뒤에는 빈자리가 아니다 -- 지금 배치 그대로 미리 계산하면 새 빈자리 없음
        p = c.post("/api/workspace/preview", json={"dataset_version": ws2["dataset_version"], "entries": _entries(ws2)}).json()
        assert p["holes"] == {}

        # 검토 자료(데려온 사업 J007)에 메우지 않음과 사유가 잠긴 사실로
        r = c.post(f"/api/review-docs/{ws2['dataset_version']}/J007")
        assert r.status_code == 200, r.text
        texts = [f["text"] for f in r.json()["facts"] if f["kind"] == "MOVE_FILL"]
        assert any("메우지 않기로 함" in t and "남은 기간은 지금 인원으로 충분" in t for t in texts)


def test_filling_the_pulled_seat_in_the_same_confirm_needs_no_waiver(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        v = ws["dataset_version"]
        _, pick, pl = _pull_pick(c, v)
        src = pl["pulled_from"][0]
        opt = _preview(c, v, pick)["fill_options"][src][0]
        d = _preview(c, v, pick, [{"person_id": opt["person_id"], "project_id": src, "alloc": opt["alloc"]}])
        assert d["holes"] == {} and d["new_violations"] == []
        assert [(f["project_id"], f["person_id"], f["from_bench"]) for f in d["fills"]] == [(src, opt["person_id"], True)]
        r = _apply(c, v, d)
        assert r.status_code == 200, r.text
        team = {m["person_id"] for o in r.json()["workspace"]["ongoing"] if o["project_id"] == src for m in o["team"]}
        assert opt["person_id"] in team
        # 빼 온 사업이 아닌 곳에 메울 사람을 넣으면 거절
        bad = c.post("/api/emergency/preview", json={"dataset_version": r.json()["workspace"]["dataset_version"],
                                                      "project_id": "J007", "picks": [pick],
                                                      "fills": [{"person_id": opt["person_id"], "project_id": "J001"}]})
        assert bad.status_code == 422


def test_best_combination_shows_who_fills_the_pulled_seat(yearend):
    """최선 조합은 빼 온 자리를 같은 조합에서 메운다 -- 예전엔 화면에 줄이 없었다(검토 자료에만)."""
    from api.main import app
    with TestClient(app) as c:
        v = _ws(c)["dataset_version"]
        best = c.post("/api/emergency/plan", json={"dataset_version": v, "project_id": "J004", "n": 1, "pull_budget": 1}).json()
        best = best["best"]
        assert best["accepted"] and best["holes"] == {}
        moved = {m["from"] for m in best["diff"]["moved"]} - {"J004"}
        assert moved and {f["project_id"] for f in best["fills"]} == moved and all(f["name"] for f in best["fills"])


def test_confirm_preview_and_manual_confirm_use_the_same_rule(yearend):
    """What-if·연간 계획 확정 창(`/api/workspace/preview`)도 같은 규칙: 사람을 빼기만 하면 그 자리가 새 빈자리다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        base = _entries(ws)
        grade = {p.id: p.grade.value for p in c.app.state.dataset.graph.people}
        out = next(e for o in ws["ongoing"] for t in o["team"] if not t.get("locked")
                   for e in base if e["person_id"] == t["person_id"] and e["project_id"] == o["project_id"]
                   and grade[e["person_id"]] in _seats(ws, o["project_id"]))
        entries = [e for e in base if e is not out]
        p = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": entries}).json()
        assert p["holes"] == {out["project_id"]: [{"grade": grade[out["person_id"]], "count": 1}]}
        assert p["fill_options"][out["project_id"]]
        body = {"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual"}
        assert c.post("/api/workspace/apply", json=body).status_code == 422
        r = c.post("/api/workspace/apply", json={**body, "seat_waivers": [
            {"project_id": out["project_id"], "grade": grade[out["person_id"]], "count": 1, "reason": "과제 축소"}]})
        assert r.status_code == 200, r.text


def test_new_holes_counts_only_what_this_confirm_opened():
    """이 확정 전부터 비어 있던 자리·정원 밖 등급은 새 빈자리가 아니다. 다른 등급으로 메우면 대체(정원이 바뀐 그래프)라 빈자리가 아니다."""
    from core.domain.models import Dataset, Grade, Person, Project, ProjectPhase, Sector
    from core.evaluate.grade_swap import substitution
    from core.graph.memory_graph import MemoryGraph

    def person(pid, g):
        return Person(id=pid, name=pid, grade=g, monthly_rate=1_000, skills={}, availability=[1.0] * 6)
    people = [person("a", Grade.MID), person("b", Grade.MID), person("c", Grade.SENIOR), person("d", Grade.JUNIOR),
              person("e", Grade.SENIOR)]
    proj = Project(id="J", name="J", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=0, end_month=5,
                   grade_headcount={Grade.MID: 3, Grade.SENIOR: 1}, requirements=[], monthly_budget=10_000)
    g = MemoryGraph.build(Dataset(people=people, projects=[proj], coworks=[], reviews=[]), parsed=[])
    cur = [{"person_id": p, "project_id": "J", "alloc": 1.0} for p in ("a", "b", "d", "e")]
    assert new_holes(g, g, cur, cur[1:]) == {"J": [{"grade": "중급", "count": 1}]}     # 원래 빈 1석은 빼고, a가 빠진 1석
    assert new_holes(g, g, cur, [x for x in cur if x["person_id"] != "d"]) == {}      # 정원 밖 등급(초급)은 빈자리가 없다
    after = cur[1:] + [{"person_id": "c", "project_id": "J", "alloc": 1.0}]           # 중급 자리를 고급이 메움 → 대체
    assert new_holes(g, substitution(g, cur, after)[0], cur, after) == {}
    assert waived_need({"중급": 3}, [{"grade": "중급", "count": 1}]) == {"중급": 2}
    assert same_holes([{"grade": "중급", "count": 1}], [{"grade": "중급", "count": 1, "reason": "x"}])


def test_a_new_project_still_being_staffed_keeps_its_open_seats(yearend):
    """편성 중인 신규 사업(J018)에서 사람을 빼면 시작 전 빈자리일 뿐 -- "메우지 않음"(정원 줄이기)을 묻지 않는다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        cand = c.get("/api/pipeline/J018/candidates", params={"dataset_version": ws["dataset_version"]}).json()
        p = next(r["person_id"] for r in cand["bench"] if not r["new_violations"] and not r.get("grade_swap"))
        entries = _entries(ws) + [{"person_id": p, "project_id": "J018", "alloc": 1.0}]
        r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries})
        assert r.status_code == 200, r.text
        ws = r.json()["workspace"]
        back = [e for e in _entries(ws) if e["person_id"] != p or e["project_id"] != "J018"]
        pv = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": back}).json()
        assert pv["holes"] == {}
        assert c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": back}).status_code == 200
