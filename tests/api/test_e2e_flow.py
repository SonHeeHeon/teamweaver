import pytest


@pytest.mark.slow
def test_full_flow_meta_optimize_whatif(client):
    """요건 설정(암묵) -> optimize -> swap -> whatif 전체 플로우.
    실제 CBC를 돌리므로 느리다(pytest.mark.slow, 일상 실행에서 제외)."""
    meta = client.get("/api/meta").json()

    plan_a = None
    with client.stream("POST", "/api/optimize",
                       json={"weights": {}, "n_alternatives": 0}) as res:
        for line in res.iter_lines():
            if line.startswith("data:"):
                import json
                evt = json.loads(line[len("data:"):].strip())
                if evt.get("label") == "A":
                    plan_a = evt
                    break
    assert plan_a is not None and len(plan_a["entries"]) > 0

    entry = plan_a["entries"][0]
    other_person = next(p["id"] for p in meta["people"] if p["id"] != entry["person_id"])
    body = {"entries": plan_a["entries"],
           "swap": {"out_person_id": entry["person_id"], "in_person_id": other_person,
                    "project_id": entry["project_id"]},
           "weights": {}}
    res = client.post("/api/whatif", json=body)
    assert res.status_code == 200
    assert "briefing" in res.json()
