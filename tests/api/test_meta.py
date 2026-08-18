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
    """네트워크 그래프는 협업 엣지 없이는 그릴 수 없다. 동결 fixture는 196개를 갖는다."""
    body = client.get("/api/meta").json()
    assert len(body["coworks"]) == 196
    edge = body["coworks"][0]
    assert set(edge.keys()) == {"a_id", "b_id", "co_months", "project_count"}
    ids = {p["id"] for p in body["people"]}
    for e in body["coworks"]:
        assert e["a_id"] in ids and e["b_id"] in ids, "엣지 끝점이 people에 없다"
