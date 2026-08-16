import json
from unittest.mock import MagicMock

from api.deps import get_openai_client_or_none
from api.main import app


def test_whatif_swap_returns_delta_and_briefing(client):
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "rationale": "테스트 근거",
            "risks": ["테스트 리스크"],
            "alternatives": ["테스트 대안"],
        })))])
    app.dependency_overrides[get_openai_client_or_none] = lambda: fake_client
    try:
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
        assert body_out["fallback_used"] is False
        assert body_out["briefing"] == {"rationale": "테스트 근거", "risks": ["테스트 리스크"],
                                        "alternatives": ["테스트 대안"]}
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)


def test_whatif_uses_fallback_when_llm_unavailable(client):
    """OPENAI_API_KEY가 없으면(또는 LLM 호출이 실패하면) fallback_used=True로
    결정론적 브리핑을 반환해야 한다 -- 발표 중 API 장애에도 데모가 죽지 않는다는
    요구사항의 직접 증거. get_openai_client_or_none이 Depends()로 주입되므로
    app.dependency_overrides로 깨끗하게 오버라이드한다(모듈 속성 monkeypatch 불필요)."""
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
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
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)


def test_whatif_excludes_incoming_person_from_teammates_list(client):
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
    entries에 포함되든 안 되든 같은 delta를 반환한다.

    get_openai_client_or_none이 Depends()로 주입되므로 (Finding 1 수정 이후)
    app.dependency_overrides로 fallback 경로를 강제한다 -- 이 테스트는
    objective_delta 불변성만 확인하고 briefing 내용은 보지 않으므로 None으로
    고정해 실제 네트워크 호출을 피한다(.env에 실제 OPENAI_API_KEY가 있으면
    모듈 속성 monkeypatch로는 더 이상 이 호출을 가로챌 수 없다 -- Depends()가
    함수 참조를 라우트 등록 시점에 이미 캡처하기 때문)."""
    from core.config import FIXTURES_DIR
    from core.datagen.fixtures_io import load_fixtures
    from core.graph.memory_graph import MemoryGraph
    from core.scoring.engine import ScoringEngine

    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
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
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)


def test_whatif_unknown_in_person_id_returns_404(client):
    """swap.in_person_id가 graph.pid_index에 없으면 KeyError 대신 명시적
    404를 반환해야 한다 (Finding 4d)."""
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        meta = client.get("/api/meta").json()
        project = meta["projects"][0]
        out_person = meta["people"][0]["id"]
        body = {
            "entries": [{"person_id": out_person, "project_id": project["id"], "alloc": 1.0}],
            "swap": {"out_person_id": out_person, "in_person_id": "no-such-person",
                    "project_id": project["id"]},
            "weights": {},
        }
        res = client.post("/api/whatif", json=body)
        assert res.status_code == 404
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)


def test_whatif_unknown_project_id_returns_404(client):
    """swap.project_id가 graph.project_index에 없으면 KeyError 대신 명시적
    404를 반환해야 한다 (Finding 4d). entries의 유일한 항목도 같은 존재하지
    않는 project_id를 참조하게 해, project_id 검사가 my_entry 조회보다 먼저
    실행됨을 함께 확인한다."""
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        meta = client.get("/api/meta").json()
        out_person = meta["people"][0]["id"]
        in_person = meta["people"][1]["id"]
        body = {
            "entries": [{"person_id": out_person, "project_id": "no-such-project", "alloc": 1.0}],
            "swap": {"out_person_id": out_person, "in_person_id": in_person,
                    "project_id": "no-such-project"},
            "weights": {},
        }
        res = client.post("/api/whatif", json=body)
        assert res.status_code == 404
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)


def test_whatif_missing_entry_for_swap_out_person_returns_422(client):
    """swap.out_person_id/project_id에 해당하는 entry가 entries에 없으면
    StopIteration 대신 명시적 422를 반환해야 한다 (Finding 4d)."""
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        meta = client.get("/api/meta").json()
        project = meta["projects"][0]
        out_person = meta["people"][0]["id"]
        in_person = meta["people"][1]["id"]
        body = {
            "entries": [],
            "swap": {"out_person_id": out_person, "in_person_id": in_person,
                    "project_id": project["id"]},
            "weights": {},
        }
        res = client.post("/api/whatif", json=body)
        assert res.status_code == 422
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)
