"""등급 대체(2026-10-21 사용자 결정 "위 아래 모두 허용하고, 사유는 전부 받고, 예산을 넘으면 증액을 함께 적자"):
신규 사업 넣기·긴급 투입에서 다른 등급 빈자리를 메우면 막지 않고 대체로 -- 사유를 받아 정원을 바꾸고, 확정 취소로 함께 돌아간다."""
from fastapi.testclient import TestClient

from tests.scenarios.test_cancel_confirm import _apply, _cancel, _confirm_entry
from tests.scenarios.test_workspace_api import _entries, _ws


def _seats(ws, pid):
    return next(p for p in ws["pipeline"] + ws["ongoing"] if p["project_id"] == pid)["seats"]


def _cands(c, ws, pid, picks=()):
    body = {"dataset_version": ws["dataset_version"],
            "picks": [{"person_id": r["person_id"], "alloc": r["alloc"], "pulled_from": r["pulled_from"]} for r in picks]}
    r = c.post(f"/api/pipeline/{pid}/candidates", json=body) if picks else \
        c.get(f"/api/pipeline/{pid}/candidates", params={"dataset_version": ws["dataset_version"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_second_mid_level_fills_the_senior_seat_with_a_reason_and_cancel_restores_it(yearend):
    """J018 정원은 특·고·중·초 1명씩. 대기 중급 둘을 넣으면 둘째는 가장 가까운 빈자리(고급)를 아래 등급으로 대체한다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        assert {g: s["need"] for g, s in _seats(ws, "J018").items()} == {"특급": 1, "고급": 1, "중급": 1, "초급": 1}
        first = next(r for r in _cands(c, ws, "J018")["bench"] if r["grade"] == "중급" and not r["new_violations"])
        assert "grade_swap" not in first                                    # 중급 자리가 비어 있다 -- 대체가 아니다
        cand = _cands(c, ws, "J018", [first])
        second = next(r for r in cand["bench"] if r["grade"] == "중급" and r["person_id"] != first["person_id"])
        # 예전엔 "넣으면 위반(등급 정원 초과)"으로 막혔다 -- 이제 위반 없이 대체 표시, 같은 등급이 맞는 후보보다 뒤
        assert second["new_violations"] == [] and second["grade_swap"] == [{"seat": "고급", "grade": "중급", "count": 1}]
        rows = cand["bench"]
        assert all(not r.get("grade_swap") for r in rows[:rows.index(second)] if not r["new_violations"])

        draft = _cands(c, ws, "J018", [first, second])["draft"]
        assert draft["grade_swaps"] == [{"seat": "고급", "grade": "중급", "count": 1}] and draft["new_violations"] == []
        assert draft["budget_add"] == 0                                    # 아래 등급 대체라 비용이 줄어든다
        assert "고급" not in draft["seats"] and draft["seats"]["중급"] == {"need": 2, "filled": 2}   # 확정 뒤 정원(0명 등급은 안 보임)

        entries = _entries(ws) + [{"person_id": r["person_id"], "project_id": "J018", "alloc": 1.0} for r in (first, second)]
        swap = {"project_id": "J018", "seat": "고급", "grade": "중급", "count": 1}
        no_swap = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual"})
        assert no_swap.status_code == 422 and no_swap.json()["detail"]["expected"] == [{"seat": "고급", "grade": "중급", "count": 1}]
        blank = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual",
                                                     "grade_swaps": [{**swap, "reason": "  "}]})
        assert blank.status_code == 422 and "사유" in blank.json()["detail"]
        wrong = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual",
                                                     "grade_swaps": [{**swap, "seat": "특급", "reason": "아무 자리나"}]})
        assert wrong.status_code == 422                                    # 서버 계산(고급 자리)과 다르면 받지 않는다

        ws = _apply(c, ws, entries, grade_swaps=[{**swap, "reason": "범위상 중급으로 충분, 발주처와 협의함"}])
        assert {g: s["need"] for g, s in _seats(ws, "J018").items() if s["need"]} == {"특급": 1, "중급": 2, "초급": 1}
        confirm = _confirm_entry(ws, "apply_manual")
        assert confirm["summary"]["grade_swaps"] == [{**swap, "reason": "범위상 중급으로 충분, 발주처와 협의함"}]

        r = _cancel(c, ws, confirm)                                        # 확정 취소가 정원도 함께 돌린다(b의 revert_ops "grade")
        assert r.status_code == 200, r.text
        assert {g: s["need"] for g, s in _seats(r.json()["workspace"], "J018").items()} == {"특급": 1, "고급": 1, "중급": 1, "초급": 1}


def test_upward_swap_raises_the_budget_by_the_overrun(yearend):
    """특급 둘: 둘째가 고급 자리를 위 등급으로 대체 -- 비용이 예산을 넘으면 넘친 만큼 증액(사용자 결정)."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        tops = [r for r in _cands(c, ws, "J018")["bench"] if r["grade"] == "특급" and not r["new_violations"]]
        if len(tops) < 2:
            tops = tops + [r for r in _cands(c, ws, "J018", tops[:1])["bench"] if r["grade"] == "특급"
                           and r["person_id"] != tops[0]["person_id"]][:1]
        draft = _cands(c, ws, "J018", tops[:2])["draft"]
        assert draft["grade_swaps"] == [{"seat": "고급", "grade": "특급", "count": 1}]
        assert draft["new_violations"] == []                               # 예산을 넘어도 증액을 함께 적으니 위반이 아니다
        entries = _entries(ws) + [{"person_id": r["person_id"], "project_id": "J018", "alloc": 1.0} for r in tops[:2]]
        before_budget = next(p for p in ws["pipeline"] if p["project_id"] == "J018")["monthly_budget"]
        ws = _apply(c, ws, entries, grade_swaps=[{"project_id": "J018", "seat": "고급", "grade": "특급", "count": 1,
                                                  "reason": "고급이 모두 다른 사업에 투입 중"}],
                    **({"budget_add": {"J018": draft["budget_add"]}} if draft["budget_add"] else {}))
        item = next(p for p in ws["pipeline"] if p["project_id"] == "J018")
        assert item["monthly_budget"] == before_budget + draft["budget_add"]
        assert {g: s["need"] for g, s in item["seats"].items() if s["need"]} == {"특급": 2, "중급": 1, "초급": 1}


def test_emergency_hint_swaps_the_short_seat_before_adding_one(yearend):
    """긴급 투입: 모자란 등급 자리가 있으면 넘친 등급 정원을 늘리지 않고 그 자리를 대체한다(예전엔 늘려서 빈자리가 남았다)."""
    from api.main import app
    from api.routes.scenarios import _apply_hint
    from core.optimize.types import AssignEntry
    with TestClient(app) as c:
        ws = _ws(c)
        graph = app.state.dataset.graph
        grade_of = {p.id: p.grade.value for p in graph.people}
        entries = [AssignEntry(**e) for e in _entries(ws)]
        # 진행 사업 하나에서 한 등급 한 명을 빼 빈자리를 만들고, 대기 인력 중 다른 등급 한 명을 넣는다
        ongoing = next(o for o in ws["ongoing"] if len({grade_of[t["person_id"]] for t in o["team"]}) >= 2)
        pid = ongoing["project_id"]
        gone = ongoing["team"][0]["person_id"]
        bench = next(b["person_id"] for b in ws["bench"] if grade_of[b["person_id"]] != grade_of[gone]
                     and grade_of[b["person_id"]] in {g.value for g in graph.projects[graph.project_index[pid]].grade_headcount})
        before = [e for e in entries if not (e.person_id == gone and e.project_id == pid)]
        after = before + [AssignEntry(person_id=bench, project_id=pid, alloc=1.0)]
        need = graph.projects[graph.project_index[pid]].grade_headcount
        hint = _apply_hint(graph, pid, before, after, [], 1)
        assert hint["grade_swaps"] == {pid: [{"seat": grade_of[gone], "grade": grade_of[bench], "count": 1}]}
        # 대체한 만큼은 증원하지 않는다(그 등급이 원래부터 넘쳐 있지 않았다면 증원 0)
        g = grade_of[bench]
        placed = sum(1 for e in before if e.project_id == pid and grade_of[e.person_id] == g)
        was_over = placed > next(n for gg, n in need.items() if gg.value == g)
        assert was_over or g not in (hint["headcount_add"].get(pid) or {})


def test_review_doc_fact_for_a_downward_swap_is_to_check():
    from api.review_docs import confirms, grade_swap_facts
    hist = [{"version": "v0", "content_version": "c0", "kind": "base"},
            {"version": "v1", "content_version": "c1", "kind": "apply_manual", "at": "t", "note": "n",
             "summary": {"grade_swaps": [{"project_id": "J018", "seat": "고급", "grade": "중급", "count": 1, "reason": "범위상 충분"},
                                         {"project_id": "J019", "seat": "중급", "grade": "고급", "count": 1, "reason": "중급 없음"}]}}]
    confirm = confirms(hist)[0]
    down = grade_swap_facts(confirm, "J018")
    assert len(down) == 1 and down[0]["kind"] == "MOVE_GRADE" and down[0]["adverse"] is True
    assert "고급 자리 1개를 중급으로 채움(아래 등급), 사유 범위상 충분" in down[0]["text"]
    up = grade_swap_facts(confirm, "J019")
    assert up[0]["adverse"] is False and "(위 등급)" in up[0]["text"]
    assert grade_swap_facts({"grade_swaps": []}, "J018") == []
