"""연간 계획 "사업 조건"에 사업별 요구 기술을 보인다(2026-10-21 사용자 결정 -- 기술 가중치를 숨기고 그 자리에)."""


def test_meta_projects_carry_their_required_skills(client):
    from api.main import app
    meta = client.get("/api/meta").json()
    graph = app.state.dataset.graph if getattr(app.state, "dataset", None) else None
    first = meta["projects"][0]
    assert first["requirements"], "요구 기술이 비어 있다"
    assert set(first["requirements"][0]) == {"skill", "min_level", "headcount"}
    if graph is not None:
        j = graph.projects[graph.project_index[first["id"]]]
        assert [(r["skill"], r["min_level"], r["headcount"]) for r in first["requirements"]] == \
            [(r.skill, r.min_level, r.headcount) for r in j.requirements]
