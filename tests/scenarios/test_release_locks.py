"""연간 계획 "고정 풀고 다시 배치"로 짠 안의 확정(2026-10-21): release_locks면 빠지는 고정 배치를 함께 풀고 이력에 남긴다.
연간 계획 안 확정(source plan)에서만 -- 다른 확정은 예전처럼 고정을 빼지 못한다."""
from fastapi.testclient import TestClient

from tests.scenarios.test_workspace_api import _entries, _ws


def _swap_out_lock(c, ws):
    """고정된 사람 한 명을 같은 등급의 대기 인력으로 바꾼 명단(새 위반·빈자리 없음)과 그 고정 배치."""
    graph = c.app.state.dataset.graph
    grade = {p.id: p.grade.value for p in graph.people}
    base = _entries(ws)
    for o in ws["ongoing"]:
        for t in o["team"]:
            if not t.get("locked"):
                continue
            for b in ws["bench"]:
                if grade[b["person_id"]] != grade[t["person_id"]] or b.get("unavailable"):
                    continue
                entries = [e for e in base if (e["person_id"], e["project_id"]) != (t["person_id"], o["project_id"])]
                entries.append({"person_id": b["person_id"], "project_id": o["project_id"], "alloc": 1.0})
                p = c.post("/api/workspace/preview", json={"dataset_version": ws["dataset_version"], "entries": entries}).json()
                if not p["new_violations"] and not p["grade_swaps"]:
                    return entries, f"{t['person_id']}@{o['project_id']}"
    raise AssertionError("바꿀 고정 배치를 찾지 못했다")


def test_plan_confirm_releases_the_dropped_lock_only_when_asked(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        entries, lock = _swap_out_lock(c, ws)
        body = {"dataset_version": ws["dataset_version"], "entries": entries, "source": "plan"}
        r = c.post("/api/workspace/apply", json=body)
        assert r.status_code == 422 and "고정한 배치는 뺄 수 없다" in r.text
        r = c.post("/api/workspace/apply", json={**body, "source": "manual", "release_locks": True})
        assert r.status_code == 422 and "연간 계획 안 확정에서만" in r.text
        r = c.post("/api/workspace/apply", json={**body, "release_locks": True})
        assert r.status_code == 200, r.text
        assert r.json()["changes"]["locks_released"] == [lock]
        top = r.json()["workspace"]["history"][0]                                   # 최근 것이 위
        assert top["kind"] == "apply_plan" and top["summary"]["locks_released"] == [lock]
