"""신규 사업 편성 화면 요청 2·3·4·5번(사용자 2026-10-20, claude-b).

- 2: "넣기"로 고른 사람을 확정 전에 팀원으로 쳐서 자리·점수·위반을 미리 계산한다(POST /api/pipeline/{id}/candidates의 draft).
- 4: 정원 변화는 사업별(seat_effects) -- 합은 예전 한 숫자(unfilled_seats_delta)와 같다.
- 5: 협업은 두 사람 사이 값이다 -- 후보 행은 기술·협업 몫과 함께하게 될 사람과의 쌍(partners)을 낸다. 고른 사람도 팀원으로 센다.
- 3: K 최대 5 -- 미리 계산·밤 배치 행은 요청 K 목록이 그 앞부분이면 잘라 준다.
"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.routes.operating import OPERATING_KS


def _rows(cand):
    return cand["bench"] + cand["ending"] + cand["pull"]


def test_seat_effects_split_the_single_number_and_skill_collab_add_up(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = c.get("/api/workspace").json()["dataset_version"]
        cand = c.post("/api/pipeline/J019/candidates", json={"dataset_version": v}).json()
        assert cand["draft"] is None
        rows = _rows(cand)
        assert rows
        for r in rows:
            assert sum(e["change"] for e in r["seat_effects"]) == r["unfilled_seats_delta"]
            assert abs(r["skill_delta"] + r["collab_delta"] - r["delta_excl_unfilled"]) < 1e-3
        pulled = [r for r in cand["pull"] if r["seat_effects"]]
        # 빼 오는 사람: 원래 사업에 빈자리(음수)가 사업 이름과 함께 보인다
        assert any(e["change"] < 0 and e["project_id"] in r["pulled_from"] and e["name"] for r in pulled for e in r["seat_effects"])
        # GET도 같은 칸(고른 사람 없음)
        got = c.get("/api/pipeline/J019/candidates", params={"dataset_version": v}).json()
        assert [r["person_id"] for r in _rows(got)] == [r["person_id"] for r in rows]


def test_picks_count_as_teammates_and_the_draft_previews_seats(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
        v = ws["dataset_version"]
        item = next(p for p in ws["pipeline"] if p["project_id"] == "J019")
        first = c.post("/api/pipeline/J019/candidates", json={"dataset_version": v}).json()
        assert all(r["partners"] == [] for r in _rows(first)) or item["team"]          # 빈 팀이면 쌍이 없다
        top = first["bench"][0]
        pick = {"person_id": top["person_id"], "alloc": top["alloc"], "pulled_from": top["pulled_from"]}
        after = c.post("/api/pipeline/J019/candidates", json={"dataset_version": v, "picks": [pick]}).json()
        rows = _rows(after)
        assert top["person_id"] not in {r["person_id"] for r in rows}                    # 고른 사람은 표에서 빠진다
        # 고른 사람과의 쌍이 다음 후보의 협업 칸에 보인다(계산에 들어가는 쌍만)
        assert all(p["person_id"] == top["person_id"] for r in rows for p in r["partners"])
        d = after["draft"]
        g = top["grade"]
        assert d["seats"][g]["filled"] == item["seats"][g]["filled"] + 1
        assert sum(e["change"] for e in d["seat_effects"]) == d["unfilled_seats_delta"] == top["unfilled_seats_delta"]
        assert abs(d["delta_total"] - top["delta_total"]) < 1e-3 and d["new_violations"] == []
        # 화면에 남은 데이터는 바뀌지 않았다(미리 계산일 뿐)
        assert c.get("/api/workspace").json()["dataset_version"] == v


def test_bad_picks_are_rejected(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
        v = ws["dataset_version"]
        member = ws["ongoing"][0]["team"][0]["person_id"]
        bad = [
            [{"person_id": "NOPE", "alloc": 1.0}],
            [{"person_id": member, "alloc": 1.0, "pulled_from": ["J019"]}],                 # 그 사업에 있지 않다
            [{"person_id": member, "alloc": 1.0}, {"person_id": member, "alloc": 1.0}],     # 두 번
        ]
        for picks in bad:
            assert c.post("/api/pipeline/J019/candidates", json={"dataset_version": v, "picks": picks}).status_code == 422
        assert c.post("/api/pipeline/J019/candidates", json={"dataset_version": "old", "picks": []}).status_code == 409


def test_precomputed_rows_serve_a_prefix_of_their_k_list():
    from api.demo_precomputed import operating_key, operating_rows
    from core.optimize.milp import MilpParams
    params = MilpParams()
    have = list(OPERATING_KS)
    rows = [{"k": k} for k in have]
    app = SimpleNamespace(state=SimpleNamespace(precomputed_operating={"key": operating_key(params, have, "v1"), "ks": have,
                                                                       "rows": rows, "computed_at": "t"}))
    assert [r["k"] for r in operating_rows(app, params, [0, 1, 2, 3], "v1")[0]] == [0, 1, 2, 3]
    assert [r["k"] for r in operating_rows(app, params, have, "v1")[0]] == have
    assert operating_rows(app, params, [0, 2], "v1") is None                # 앞부분이 아니면(K=2가 K=1을 이어받는다) 쓰지 않는다
    assert operating_rows(app, params, [1, 2], "v1") is None
    assert operating_rows(app, params, [0, 1], "v2") is None                # 다른 데이터
    assert operating_rows(app, MilpParams(time_limit=7), [0, 1], "v1") is None


def test_nightly_and_screen_default_use_the_same_k_list():
    from api.nightly import DEFAULT_KS
    from api.routes.operating import CompareRequest
    assert tuple(DEFAULT_KS) == OPERATING_KS == tuple(CompareRequest().ks) and OPERATING_KS[-1] == 5


def test_new_or_worse_counts_a_growing_existing_violation():
    """Codex 리뷰(2026-10-20): 미리 계산이 (종류, 위치)만 비교해 기존 예산 초과가 50→150으로 커져도 "위반 없음"이라 했다 -- 확정과 같은 판정."""
    from api.violation_diff import new_or_worse
    V = lambda code, loc, actual, limit: SimpleNamespace(code=code, location=loc, actual=actual, limit=limit)   # noqa: E731
    before = [V("budget", "J1", 100, 50), V("alloc_range", "p:J2", 0.2, 0.3)]
    assert new_or_worse(before, [V("budget", "J1", 150, 50)])[0].actual == 150                 # 커짐
    assert new_or_worse(before, [V("budget", "J1", 100, 50)]) == []                             # 그대로
    assert new_or_worse(before, [V("budget", "J1", 80, 50)]) == []                              # 줄어듦
    assert len(new_or_worse(before, [V("alloc_range", "p:J2", 0.1, 0.3)])) == 1                # 하한 위반은 모자란 만큼
    assert len(new_or_worse(before, [V("budget", "J9", 60, 50)])) == 1                          # 새로 생김


def test_draft_and_candidates_flag_a_worsened_budget(yearend, monkeypatch):
    """J019 예산을 지금 팀 비용보다 낮춘 것처럼(기존 위반) 만들고 한 명 더 고르면 -- draft·후보 모두 위반으로 보인다(확정이 거절할 것)."""
    import core.evaluate.plan_eval as pe
    real = pe.evaluate_plan

    def tight(graph, S, C, params, entries):
        j = graph.projects[graph.project_index["J019"]]
        squeezed = graph.projects[:graph.project_index["J019"]] + [j.model_copy(update={"monthly_budget": 1})] \
            + graph.projects[graph.project_index["J019"] + 1:]
        from dataclasses import replace
        return real(replace(graph, projects=squeezed), S, C, params, entries)
    from api.main import app
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
        v = ws["dataset_version"]
        first = c.post("/api/pipeline/J019/candidates", json={"dataset_version": v}).json()
        a, b = first["bench"][0], first["bench"][1]
        monkeypatch.setattr(pe, "evaluate_plan", tight)
        one = {"person_id": a["person_id"], "alloc": a["alloc"], "pulled_from": []}
        two = {"person_id": b["person_id"], "alloc": b["alloc"], "pulled_from": []}
        # 한 명을 넣은 상태를 "지금"으로 보는 대신: 두 명 draft는 지금(0명, 위반 없음) 대비 예산 위반이 생긴다
        d = c.post("/api/pipeline/J019/candidates", json={"dataset_version": v, "picks": [one, two]}).json()
        assert d["draft"]["new_violations"]
        # 한 명을 고른 상태의 후보 행은 "이미 예산을 넘긴 사업에 더 넣는" 경우 -- 커진 위반으로 잡혀야 한다
        rows = _rows(c.post("/api/pipeline/J019/candidates", json={"dataset_version": v, "picks": [one]}).json())
        assert rows and all(any(x[0] == "budget" for x in r["new_violations"]) for r in rows if r["grade"])


def test_emergency_rows_get_a_grown_person_violation(monkeypatch):
    """Opus 대체 리뷰 S1(2026-10-20): 긴급 투입 후보·최선 조합·대기 명단도 커진 기존 사람 단위 위반을 보인다(확정과 같은 판정).
    동시 수행 지원 행(needs_grant)의 자기 동시 수행 상한은 확정 때 허용을 함께 적으므로 빼고 본다."""
    import core.evaluate.plan_eval as pe
    from api.routes.scenarios import with_worse
    V = lambda code, loc, actual, limit: SimpleNamespace(code=code, location=loc, actual=actual, limit=limit)   # noqa: E731
    base = [SimpleNamespace(person_id="P1", project_id="J1", alloc=1.0)]
    graph = SimpleNamespace()

    def fake(graph, S, C, params, entries):
        grown = len(entries) > len(base)
        return SimpleNamespace(violations=[V("availability", "P1:month0", 2.0 if grown else 1.5, 1.0),
                                           V("budget", "J2", 999, 1)] + (
            [V("concurrent_projects", "P2:month0", 2, 1)] if grown else []))
    monkeypatch.setattr(pe, "evaluate_plan", fake)
    rows = [{"person_id": "P1", "alloc": 1.0, "pulled_from": [], "new_violations": []},
            {"person_id": "P2", "alloc": 1.0, "pulled_from": [], "new_violations": [], "needs_grant": True}]
    out = with_worse(graph, None, None, None, base, "J2", rows)
    assert ("availability", "P1:month0") in out[0]["new_violations"]                       # 1.5 → 2.0 커짐
    assert all(code != "budget" for code, _ in out[0]["new_violations"])                     # 사업 예산은 apply_hint 몫
    assert ("concurrent_projects", "P2:month0") not in out[1]["new_violations"]             # 허용을 함께 적는 지원 행


def test_workspace_reports_the_precomputed_k_list(yearend):
    """Opus 대체 리뷰 S2: 화면이 기본 "최대 K"를 미리 계산된 K 목록에 맞추도록 작업 데이터가 그 목록을 싣는다(없으면 빈 목록)."""
    from api.main import app
    with TestClient(app) as c:
        assert c.get("/api/workspace").json()["operating_ks"] == []
        app.state.precomputed_operating = {"key": "x", "ks": [3, 0, 2, 1], "rows": [], "computed_at": "t"}
        assert c.get("/api/workspace").json()["operating_ks"] == [0, 1, 2, 3]
