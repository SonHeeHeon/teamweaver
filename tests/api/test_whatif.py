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
                                        "alternatives": ["테스트 대안"], "evidence": []}
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


def test_whatif_rejects_incoming_person_already_on_the_project(client):
    """이전 계약은 in_person이 이미 같은 프로젝트에 있어도 200을 주고, 그 사람을
    teammates에서 빼서 delta가 불변이게 했다. 2026-10-04 K1에서 What-if를 전체 목적
    재평가로 바꾸면서, 교체 결과가 같은 (사람, 프로젝트)를 두 번 갖는 배치가 되는
    입력은 MILP로 표현할 수 없는 상태이므로 422로 거절한다."""
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        project = client.get("/api/meta").json()["projects"][0]["id"]
        body = {
            "entries": [{"person_id": "p000", "project_id": project, "alloc": 1.0},
                        {"person_id": "p052", "project_id": project, "alloc": 0.5}],
            "swap": {"out_person_id": "p000", "in_person_id": "p052", "project_id": project},
            "weights": {},
        }
        assert client.post("/api/whatif", json=body).status_code == 422
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


def test_whatif_reports_swap_that_exceeds_the_concurrent_project_limit(client):
    """C6: 평가기(core/evaluate)가 아직 동시 프로젝트 상한을 모른다 -- API가 위반으로 덧붙여, 상한을
    넘는 교체가 "위반 없음"으로 보이지 않게 한다(C6 리뷰 MUST)."""
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        meta = client.get("/api/meta").json()
        projects = meta["projects"]
        j1, j2 = next((a, b) for a in projects for b in projects
                      if a["id"] < b["id"] and a["start_month"] <= b["end_month"] and b["start_month"] <= a["end_month"])
        p_in, p_out = meta["people"][0]["id"], meta["people"][1]["id"]
        body = {
            "entries": [{"person_id": p_in, "project_id": j1["id"], "alloc": 0.3},
                        {"person_id": p_out, "project_id": j2["id"], "alloc": 0.3}],
            "swap": {"out_person_id": p_out, "in_person_id": p_in, "project_id": j2["id"]},
            "weights": {}, "milp_params": {"max_concurrent_projects": 1},
        }
        res = client.post("/api/whatif", json=body)
        assert res.status_code == 200, res.text
        out = res.json()
        assert "concurrent_projects" in {v["code"] for v in out["new_violations"]}
        assert out["feasible"] is False
        body["milp_params"] = {"max_concurrent_projects": 2}
        out2 = client.post("/api/whatif", json=body).json()
        assert "concurrent_projects" not in {v["code"] for v in out2["new_violations"]}
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)
