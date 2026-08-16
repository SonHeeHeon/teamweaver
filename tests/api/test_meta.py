def test_meta_returns_people_projects_skills_review_items(client):
    res = client.get("/api/meta")
    assert res.status_code == 200
    body = res.json()
    assert len(body["people"]) == 100 and len(body["projects"]) == 20  # 동결 fixture
    assert "Java" in body["skills"]
    assert len(body["review_items"]) > 0
    person = body["people"][0]
    assert {"id", "name", "grade", "skills"} <= person.keys()
