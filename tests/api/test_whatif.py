def test_whatif_swap_returns_delta_and_briefing(client):
    meta = client.get("/api/meta").json()
    project = meta["projects"][0]
    out_person = meta["people"][0]["id"]
    in_person = meta["people"][1]["id"]

    body = {
        "entries": [{"person_id": out_person, "project_id": project["id"], "alloc": 1.0}],
        "swap": {"out_person_id": out_person, "in_person_id": in_person,
                "project_id": project["id"]},
        "weights": {},
    }
    res = client.post("/api/whatif", json=body)
    assert res.status_code == 200
    body_out = res.json()
    assert "objective_delta" in body_out
    assert set(body_out["briefing"].keys()) == {"rationale", "risks", "alternatives"}
    assert isinstance(body_out["fallback_used"], bool)


def test_whatif_uses_fallback_when_llm_unavailable(client, monkeypatch):
    """OPENAI_API_KEY가 없으면(또는 LLM 호출이 실패하면) fallback_used=True로
    결정론적 브리핑을 반환해야 한다 -- 발표 중 API 장애에도 데모가 죽지 않는다는
    요구사항의 직접 증거.

    patch 대상은 `api.deps.get_openai_client_or_none`이 아니라
    `api.routes.whatif.get_openai_client_or_none`이다 -- whatif.py가
    `from api.deps import get_openai_client_or_none`로 임포트해 whatif 모듈
    자신의 네임스페이스에 이름을 바인딩하므로, api.deps 쪽을 patch해도
    whatif.py가 부르는 참조는 바뀌지 않는다. 또한 실제 구현은 클라이언트
    생성 실패를 삼켜 None을 반환하지 raise하지 않으므로(그래야 whatif 라우트가
    500 없이 폴백으로 넘어간다), 대체 함수도 raise가 아니라 None을 반환해야
    한다."""
    import api.routes.whatif as mod
    monkeypatch.setattr(mod, "get_openai_client_or_none", lambda: None)
    meta = client.get("/api/meta").json()
    project = meta["projects"][0]
    body = {
        "entries": [{"person_id": meta["people"][0]["id"], "project_id": project["id"], "alloc": 1.0}],
        "swap": {"out_person_id": meta["people"][0]["id"], "in_person_id": meta["people"][1]["id"],
                "project_id": project["id"]},
        "weights": {},
    }
    res = client.post("/api/whatif", json=body)
    assert res.json()["fallback_used"] is True
