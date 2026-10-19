def test_meta_returns_people_projects_skills_review_items(client):
    res = client.get("/api/meta")
    assert res.status_code == 200
    body = res.json()
    assert len(body["people"]) == 100 and len(body["projects"]) == 20  # 동결 fixture
    assert "Java" in body["skills"]
    assert len(body["review_items"]) > 0
    person = body["people"][0]
    assert {"id", "name", "grade", "skills"} <= person.keys()


def test_meta_includes_cowork_edges_for_the_network_graph(client):
    """교체 검토 관계 목록(2026-10-20, 예전 협업 관계망)은 협업 엣지가 있어야 그린다. 동결 fixture는 196개를 갖는다.
    review = 서로 남긴 동료 평가 극성 평균(-1~1, 없으면 null)."""
    body = client.get("/api/meta").json()
    assert len(body["coworks"]) == 196
    edge = body["coworks"][0]
    assert set(edge.keys()) == {"a_id", "b_id", "co_months", "project_count", "review"}
    assert all(e["review"] is None or -1 <= e["review"] <= 1 for e in body["coworks"])
    ids = {p["id"] for p in body["people"]}
    for e in body["coworks"]:
        assert e["a_id"] in ids and e["b_id"] in ids, "엣지 끝점이 people에 없다"


def test_pair_review_words_only_for_synthetic_data(client):
    """평가의 좋고 나쁨(교체 검토 관계 목록)은 가상 데이터만 -- 실데이터는 평가 원문 비공개(K5)와 같은 선(2026-10-20)."""
    from api.main import app
    from api.routes.meta import _cowork_edges
    g = app.state.dataset.graph
    assert all(e.review is None for e in _cowork_edges(g, False))
    assert any(e.review is not None for e in _cowork_edges(g, True))
