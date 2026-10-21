"""확정 전 미리 계산 `/api/workspace/preview`(2026-10-21 -- 연간 계획 확정·What-if 확정 창): 확정과 같은 규칙, 데이터는 그대로."""
from fastapi.testclient import TestClient

from tests.scenarios.test_grade_swap_api import _cands
from tests.scenarios.test_workspace_api import _entries, _ws


def _preview(c, ws, entries):
    r = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": entries})
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_tells_moves_swaps_and_locks_without_changing_data(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        base = _entries(ws)
        assert _preview(c, ws, base)["changed"] is False                    # 그대로면 바뀐 것 없음

        first = next(r for r in _cands(c, ws, "J018")["bench"] if r["grade"] == "중급" and not r["new_violations"])
        second = next(r for r in _cands(c, ws, "J018", [first])["bench"] if r["grade"] == "중급" and r["person_id"] != first["person_id"])
        two = base + [{"person_id": r["person_id"], "project_id": "J018", "alloc": 1.0} for r in (first, second)]
        p = _preview(c, ws, two)
        assert [x["person_id"] for x in p["joined"]] == sorted([first["person_id"], second["person_id"]])
        assert p["grade_swaps"] == {"J018": [{"seat": "고급", "grade": "중급", "count": 1}]}   # 확정이 맞춰 볼 대체와 같다
        assert p["new_violations"] == [] and p["locked_dropped"] == [] and p["budget_add"] == {}

        # 진행 사업 사람을 다른 사업으로 옮기면 "옮김"(from → to), 고정한 배치를 빼면 막을 이유
        mover = next(e for e in base if e["project_id"] == "J001")
        moved = [e for e in base if e is not mover] + [{**mover, "project_id": "J018"}]
        p = _preview(c, ws, moved)
        assert p["moved"] == [{"person_id": mover["person_id"], "name": p["moved"][0]["name"], "from": ["J001"], "to": ["J018"]}]
        locked = next((t["person_id"], o["project_id"]) for o in ws["ongoing"] for t in o["team"] if t.get("locked"))
        p = _preview(c, ws, [e for e in base if (e["person_id"], e["project_id"]) != locked])
        assert len(p["locked_dropped"]) == 1 and p["locked_dropped"][0].endswith(f"@{locked[1]}")
        assert _ws(c)["dataset_version"] == ws["dataset_version"]          # 데이터는 그대로


def test_current_whatif_scores_a_cross_grade_swap_as_the_confirm_will(yearend):
    """What-if(지금 배치)는 등급이 다른 교체를 확정과 같은 등급 대체로 평가한다 -- 예전엔 "빈자리 −100"으로 보였는데 확정은 대체로 받았다."""
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        graph = app.state.dataset.graph
        grade = {p.id: p.grade.value for p in graph.people}
        base = _entries(ws)
        # 정원 표에 여러 등급이 있는 진행 사업의 한 사람을, 그 사업 정원 표에 있는 다른 등급의 대기 인력으로
        pick = None
        for o in ws["ongoing"]:
            listed = {g.value for g in graph.projects[graph.project_index[o["project_id"]]].grade_headcount}
            for t in o["team"]:
                b = next((x["person_id"] for x in ws["bench"] if grade[x["person_id"]] != grade[t["person_id"]]
                          and grade[x["person_id"]] in listed and not x.get("unavailable")), None)
                if b and not t.get("locked"):
                    pick = (o["project_id"], t["person_id"], b)
                    break
            if pick:
                break
        jid, out, inn = pick
        body = {"dataset_version": ws["dataset_version"], "entries": base,
                "swap": {"out_person_id": out, "in_person_id": inn, "project_id": jid}}
        plain = c.post("/api/whatif", json=body).json()
        swapped = c.post("/api/whatif", json={**body, "grade_swap": True}).json()
        assert plain["grade_swaps"] == {} and plain["after"]["unfilled"] < plain["before"]["unfilled"]   # 예전 방식: 빈자리 감점
        assert swapped["grade_swaps"] == {jid: [{"seat": grade[out], "grade": grade[inn], "count": 1}]}
        assert swapped["after"]["unfilled"] == swapped["before"]["unfilled"]                            # 대체라 빈자리가 아니다
        assert swapped["briefing"]["risks"][0].startswith(f"등급 대체 — {jid}의 {grade[out]} 자리")
        staged = c.post("/api/plans/apply-swap", json={**body, "grade_swap": True}).json()
        assert staged["grade_swaps"] == swapped["grade_swaps"]
        assert abs(staged["objective_delta"] - swapped["objective_delta"]) < 1e-9                        # 검토와 담기가 같은 값
        p = _preview(c, ws, staged["entries"])
        assert p["grade_swaps"] == swapped["grade_swaps"]                                              # 확정 미리 계산도 같은 대체
