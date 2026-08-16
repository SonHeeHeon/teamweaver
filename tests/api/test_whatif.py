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
    swap.in_person_id는 제외하지 않으면 in_person_id 자신이 teammates에
    포함되어 spurious term C[in_i, in_i] - C[out_i, in_i] = 0 - C[out_i, in_i]
    을 더한다. 대각선이 0이라 해도 두 번째 항이 nonzero이면 전체 항이 nonzero다.

    이 테스트는 nonzero synergy를 가진 실제 pair(p000, p052)를 사용해,
    in_person_id가 이미 같은 프로젝트에 entry를 가지고 있는 현실적인
    시나리오를 재현한다. 버그가 있으면 entries_with_both 호출이
    spurious term C[in_i, in_i] - C[out_i, in_i]를 delta에 더하므로
    두 호출의 결과가 달랐을 것이다.

    고정된 동작: in_person이 teammates 리스트에서 제외되므로,
    entries에 포함되든 안 되든 같은 delta를 반환한다."""
    from core.config import FIXTURES_DIR
    from core.datagen.fixtures_io import load_fixtures
    from core.graph.memory_graph import MemoryGraph
    from core.scoring.engine import ScoringEngine

    import api.routes.whatif as mod
    monkeypatch.setattr(mod, "get_openai_client_or_none", lambda: None)
    meta = client.get("/api/meta").json()
    project = meta["projects"][0]

    # 실제 fixture에서 nonzero synergy를 가진 pair를 선택 (p000, p052)
    out_person = "p000"
    in_person = "p052"

    # Precondition check: 이 pair가 실제로 nonzero synergy를 가지는지 확인
    # 만약 fixture가 변경되어 synergy가 0이 되면 테스트가 실패해 명시적으로 알려준다.
    ds, parsed = load_fixtures(FIXTURES_DIR)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    C = eng.synergy_matrix()
    pdx = graph.pid_index
    c_value = C[pdx[out_person], pdx[in_person]]
    assert c_value != 0.0, \
        f"Precondition failed: C[{out_person}, {in_person}] = {c_value} should be nonzero. " \
        "Fixture may have changed; select a different pair with nonzero synergy."

    # Case 1: entries에 out_person과 in_person 모두 같은 프로젝트에 포함
    # (클라이언트가 프로젝트의 모든 사람을 entries에 포함시킬 때 발생하는 현실적 시나리오)
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

    # 두 delta가 같아야 한다.
    # 버그가 있었다면(in_person을 teammates에 포함), entries_with_both 호출이
    # spurious term C[in_i, in_i] - C[out_i, in_i] = 0 - c_value = -c_value를
    # delta에 더했을 것이다. 이 pair의 c_value는 nonzero(약 0.108)이므로
    # 버그 버전은 delta_with_both != delta_without_in을 만들었을 것이다.
    assert delta_with_both == delta_without_in, \
        f"Delta should be invariant to in_person's own entry on the project. " \
        f"with_both={delta_with_both}, without_in={delta_without_in}, " \
        f"diff={delta_with_both - delta_without_in}"
