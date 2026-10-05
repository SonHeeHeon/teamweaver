"""사람별 달별 투입률 조정(2026-10-05): 기본은 기간 내내 한 비율, 필요한 사람만 달마다 바꾼다.
적용은 교체와 같은 계약(전체 재평가·위반은 경고)이고, 저장분 재생·PDF 재생에 같이 실린다."""
from api.main import app
from api.plan_token import sign_plan
from api.settings import PlacementSettings


def _project_with_months(client, n=2):
    g = client.app.state.dataset.graph
    return next(p for p in g.projects if len(p.months) >= n)


def _entry(client, project, alloc=0.5):
    g = client.app.state.dataset.graph
    person = next(p for p in g.people if all(p.availability[m] >= 1.0 for m in project.months)) \
        if any(all(p.availability[m] >= 1.0 for m in project.months) for p in g.people) else g.people[0]
    return {"person_id": person.id, "project_id": project.id, "alloc": alloc}


def test_apply_alloc_turns_one_entry_into_a_monthly_profile(client):
    j = _project_with_months(client)
    e = _entry(client, j)
    months = list(j.months)
    profile = {m: (0.3 if k == 0 else 0.6) for k, m in enumerate(months)}
    res = client.post("/api/plans/apply-alloc", json={
        "entries": [e], "change": {"person_id": e["person_id"], "project_id": j.id, "monthly_alloc": profile}})
    assert res.status_code == 200, res.text
    out = res.json()
    changed = out["entries"][0]
    assert {int(k): v for k, v in changed["monthly_alloc"].items()} == profile
    assert abs(changed["alloc"] - sum(profile.values()) / len(profile)) < 1e-5
    assert out["optimization_ratio"] is None or out["optimization_ratio"] <= 1 + 1e-9


def test_equal_months_collapse_to_a_plain_entry(client):
    j = _project_with_months(client)
    e = _entry(client, j)
    res = client.post("/api/plans/apply-alloc", json={
        "entries": [e], "change": {"person_id": e["person_id"], "project_id": j.id,
                                   "monthly_alloc": {m: 0.4 for m in j.months}}})
    changed = res.json()["entries"][0]
    assert "monthly_alloc" not in changed and changed["alloc"] == 0.4


def test_months_must_match_the_project(client):
    j = _project_with_months(client)
    e = _entry(client, j)
    wrong = {m: 0.5 for m in range(6) if m not in j.months} or {0: 0.5}
    res = client.post("/api/plans/apply-alloc", json={
        "entries": [e], "change": {"person_id": e["person_id"], "project_id": j.id, "monthly_alloc": wrong}})
    assert res.status_code == 422


def test_overload_in_one_month_is_a_warning_not_a_refusal(client):
    """한 달이 가용률을 넘으면 적용은 되고 위반·경고가 돌아온다(결정은 사람이 한다)."""
    g = client.app.state.dataset.graph
    j = _project_with_months(client)
    person = min(g.people, key=lambda p: min(p.availability[m] for m in j.months))
    low_month = min(j.months, key=lambda m: person.availability[m])
    if person.availability[low_month] >= 1.0:
        import pytest
        pytest.skip("이 데이터엔 가용률 1 미만인 달이 없다")
    profile = {m: (1.0 if m == low_month else 0.3) for m in j.months}
    res = client.post("/api/plans/apply-alloc", json={
        "entries": [{"person_id": person.id, "project_id": j.id, "alloc": 0.3}],
        "change": {"person_id": person.id, "project_id": j.id, "monthly_alloc": profile}})
    out = res.json()
    assert res.status_code == 200 and out["feasible"] is False and out["warnings"]


def test_saved_edits_replay_mixed_swaps_and_alloc_changes(client):
    """저장분(교체 + 달별 조정)을 서버가 순서대로 다시 적용해 돌려준다. 예전 교체 기록(kind 없음)도 읽는다."""
    meta = client.get("/api/meta").json()
    version = meta["dataset_version"]
    g = client.app.state.dataset.graph
    j = _project_with_months(client)
    grade = g.people[0].grade
    same = [p.id for p in g.people if p.grade == grade]
    base = [{"person_id": same[0], "project_id": j.id, "alloc": 0.5}]
    params = PlacementSettings().model_dump()
    token = sign_plan(version, "A", base, {}, PlacementSettings().to_milp_params())
    profile = {str(m): (0.3 if k == 0 else 0.5) for k, m in enumerate(j.months)}
    steps = [{"out_person_id": same[0], "in_person_id": same[1], "project_id": j.id},
             {"kind": "alloc", "person_id": same[1], "project_id": j.id, "monthly_alloc": profile}]
    put = client.put(f"/api/plans/edits/{token}", json={
        "plan_label": "A", "base_entries": base, "weights": {}, "milp_params": params,
        "dataset_version": version, "swaps": steps, "expected_revision": 0})
    assert put.status_code == 200, put.text
    got = client.get(f"/api/plans/edits/{token}").json()
    assert [s.get("kind", "swap") for s in got["swaps"]] == ["swap", "alloc"]
    assert "kind" not in got["swaps"][0]                                    # 교체는 예전 모양 그대로
    last = got["steps"][-1]["entries"][0]
    assert last["person_id"] == same[1] and "monthly_alloc" in last


def test_report_replays_an_alloc_change(client, monkeypatch, tmp_path):
    """PDF는 적용 이력(교체·달별 조정)을 원 플랜에서 서버가 다시 적용한 명단으로 찍는다."""
    import api.routes.report as report_route
    index = tmp_path / "index.html"
    index.write_text("x")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    seen = []

    async def fake_render(payload, *a, **k):
        seen.append(payload)
        return b"%PDF-fake"

    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    j = _project_with_months(client)
    e = _entry(client, j)
    profile = {str(m): (0.3 if k == 0 else 0.6) for k, m in enumerate(j.months)}
    res = client.post("/api/report", json={
        "plan_label": "A", "entries": [e], "objective": 0, "fulfillment": 0, "optimization_ratio": None,
        "base_entries": [e], "milp_params": PlacementSettings().model_dump(),
        "applied_swaps": [{"kind": "alloc", "person_id": e["person_id"], "project_id": j.id,
                           "monthly_alloc": profile}]})
    assert res.status_code == 200, res.text
    payload = seen[0]
    assert payload["applied_swaps"][0]["kind"] == "alloc"
    assert {str(k): v for k, v in payload["entries"][0]["monthly_alloc"].items()} == profile



def test_zero_month_is_applied_with_a_range_violation(client):
    """그 달에 빠지는 것(0%)은 지원하지 않는다 -- 적용은 되지만 범위 위반 경고가 붙는다."""
    j = _project_with_months(client)
    e = _entry(client, j)
    profile = {m: (0.0 if k == 0 else 0.5) for k, m in enumerate(j.months)}
    out = client.post("/api/plans/apply-alloc", json={
        "entries": [e], "change": {"person_id": e["person_id"], "project_id": j.id, "monthly_alloc": profile}}).json()
    assert out["feasible"] is False and any("허용 범위" in v["message"] for v in out["evaluation"]["violations"])


def test_missing_entry_and_unknown_project(client):
    j = _project_with_months(client)
    e = _entry(client, j)
    other = client.app.state.dataset.graph.people[-1].id
    r1 = client.post("/api/plans/apply-alloc", json={
        "entries": [e], "change": {"person_id": other, "project_id": j.id, "monthly_alloc": {m: 0.5 for m in j.months}}})
    r2 = client.post("/api/plans/apply-alloc", json={
        "entries": [e], "change": {"person_id": e["person_id"], "project_id": "nope", "monthly_alloc": {0: 0.5}}})
    assert r1.status_code == 422 and r2.status_code == 404
