"""월별 투입률의 API 경로: 최적화 결과에 달별 값이 실리고, 교체는 그 값을 이어받고, 서명은 그 값까지 묶는다."""
import json

from api.deps import get_openai_client_or_none
from api.main import app
from api.plan_token import sign_plan, verify_plan
from core.optimize.milp import MilpParams


def _stream(client, body):
    with client.stream("POST", "/api/optimize", json=body) as res:
        return [json.loads(l[len("data:"):].strip()) for l in res.iter_lines() if l.startswith("data:")]


def test_monthly_mode_returns_monthly_entries(small_graph_client):
    events = _stream(small_graph_client, {"weights": {}, "n_alternatives": 0,
                                          "milp_params": {"allocation_mode": "monthly", "min_alloc": 0.2}})
    plan = next(e for e in events if e.get("label") == "A")
    assert plan["entries"]
    for e in plan["entries"]:
        if "monthly_alloc" in e:
            vals = list(e["monthly_alloc"].values())
            assert len(set(vals)) > 1 and abs(e["alloc"] - sum(vals) / len(vals)) < 1e-6


def test_fixed_mode_entries_have_no_monthly_key(small_graph_client):
    events = _stream(small_graph_client, {"weights": {}, "n_alternatives": 0})
    plan = next(e for e in events if e.get("label") == "A")
    assert all("monthly_alloc" not in e for e in plan["entries"])


def test_swap_inherits_the_monthly_profile(client):
    from api.routes.whatif import _swapped_entries
    from api.schemas import EntryIn, SwapIn
    g = client.app.state.dataset.graph
    j = next(p for p in g.projects if len(p.months) >= 2)
    m0, m1 = j.months[0], j.months[1]
    entries = [EntryIn(person_id=g.people[0].id, project_id=j.id, alloc=0.5, monthly_alloc={m0: 0.3, m1: 0.7})]
    _, after = _swapped_entries(g, entries, SwapIn(out_person_id=g.people[0].id, in_person_id=g.people[1].id,
                                                    project_id=j.id))
    assert after[-1].person_id == g.people[1].id and after[-1].monthly_alloc == {m0: 0.3, m1: 0.7}


def test_plan_token_binds_monthly_values_and_ignores_empty_ones():
    p = MilpParams()
    plain = [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}]
    monthly = [{"person_id": "p0", "project_id": "j0", "alloc": 0.5, "monthly_alloc": {"0": 0.3, "1": 0.7}}]
    other = [{"person_id": "p0", "project_id": "j0", "alloc": 0.5, "monthly_alloc": {"0": 0.7, "1": 0.3}}]
    t_plain, t_monthly = sign_plan("v", "A", plain, {}, p), sign_plan("v", "A", monthly, {}, p)
    assert t_plain != t_monthly
    assert verify_plan(t_monthly, "v", "A", monthly, {}, p)
    assert not verify_plan(t_monthly, "v", "A", other, {}, p)     # 달별 값을 바꾸면 서명이 깨진다
    # 월별 값이 없는 항목은 칸 없음/None/{} 어느 쪽이든 같은 서명(같은 params 안에서)
    assert verify_plan(t_plain, "v", "A", [{**plain[0], "monthly_alloc": None}], {}, p)


def test_whatif_on_a_valid_monthly_roster_reports_no_false_violations(client):
    """정상 월별 명단의 교체 검토는 '교체 전에도 위반'이 아니어야 한다(평가기가 달별로 검사)."""
    meta = client.get("/api/meta").json()
    g = client.app.state.dataset.graph
    pa, pb = [p for p in g.projects if len(p.months) >= 2][:2]
    # pa 진행 달 앞 절반 0.2·뒤 0.6. 그 달별 값을 감당할 가용률이 있는 같은 등급 두 사람을 고른다.
    months = list(pa.months)
    prof = {m: (0.2 if k < len(months) // 2 else 0.6) for k, m in enumerate(months)}
    fits = [p for p in g.people if all(p.availability[m] >= prof[m] for m in months)]
    grade = next(gr for gr in {p.grade for p in fits} if sum(p.grade == gr for p in fits) >= 2)
    same = [p.id for p in fits if p.grade == grade]
    mean = sum(prof.values()) / len(prof)
    body = {"entries": [{"person_id": same[0], "project_id": pa.id, "alloc": mean, "monthly_alloc": prof}],
            "swap": {"out_person_id": same[0], "in_person_id": same[1], "project_id": pa.id},
            "weights": {}, "milp_params": {"allocation_mode": "monthly", "min_alloc": 0.2}}
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    try:
        res = client.post("/api/whatif", json=body)
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["new_violations"] == [] and out["feasible"] is True
    bad = {**body, "entries": [{**body["entries"][0], "alloc": 0.9}]}
    assert client.post("/api/whatif", json=bad).status_code == 422          # 평균이 안 맞는 입력
