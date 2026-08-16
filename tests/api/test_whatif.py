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


def test_whatif_excludes_incoming_person_from_teammates_list(client, monkeypatch):
    """버그: teammates 리스트가 swap.out_person_id는 제외하지만,
    swap.in_person_id는 제외하지 않으면 in_person_id의 자체 시너지
    diagonal(=0)이 계산에 포함된다. 이 테스트는 in_person_id가 이미
    같은 프로젝트에 entry를 가지고 있는 현실적인 시나리오를 재현해,
    그것이 delta 결과에 영향을 주지 않음을 확인한다.

    고정된 동작: entries에 out_person과 in_person 모두 같은 프로젝트에
    포함된 경우와 in_person의 entry를 제외한 경우 두 호출 모두 같은
    objective_delta를 반환해야 한다."""
    import api.routes.whatif as mod
    monkeypatch.setattr(mod, "get_openai_client_or_none", lambda: None)
    meta = client.get("/api/meta").json()
    project = meta["projects"][0]
    out_person = meta["people"][0]["id"]
    in_person = meta["people"][1]["id"]

    # Case 1: entries에 out_person과 in_person 모두 포함
    entries_with_both = [
        {"person_id": out_person, "project_id": project["id"], "alloc": 1.0},
        {"person_id": in_person, "project_id": project["id"], "alloc": 0.5},
    ]
    body_with_both = {
        "entries": entries_with_both,
        "swap": {"out_person_id": out_person, "in_person_id": in_person,
                "project_id": project["id"]},
        "weights": {},
    }
    res_with_both = client.post("/api/whatif", json=body_with_both)
    assert res_with_both.status_code == 200
    delta_with_both = res_with_both.json()["objective_delta"]

    # Case 2: entries에 out_person만 포함 (in_person의 entry 제외)
    entries_without_in = [
        {"person_id": out_person, "project_id": project["id"], "alloc": 1.0},
    ]
    body_without_in = {
        "entries": entries_without_in,
        "swap": {"out_person_id": out_person, "in_person_id": in_person,
                "project_id": project["id"]},
        "weights": {},
    }
    res_without_in = client.post("/api/whatif", json=body_without_in)
    assert res_without_in.status_code == 200
    delta_without_in = res_without_in.json()["objective_delta"]

    # 두 delta가 같아야 한다 -- in_person의 자신의 entry 유무가 영향을 주지 않음을 증명
    assert delta_with_both == delta_without_in
