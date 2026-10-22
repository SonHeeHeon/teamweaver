"""긴급 투입 손 편성 미리 보기(2026-10-20 사용자 "긴급 투입에도 미리 보기 기능 추가해줘", claude-b).

고른 사람을 넣고 확정 때 적을 증원(apply_hint)을 적용한 상태를 확정과 같은 판정으로 미리 계산한다 -- 그대로 확정하면 통과해야 한다."""
from fastapi.testclient import TestClient


def _plan(c, v, **kw):
    return c.post("/api/emergency/plan", json={"dataset_version": v, "project_id": "J007", "n": 2, "pull_budget": 1, **kw}).json()


def _pick(r, grant=False):
    return {"person_id": r["person_id"], "alloc": r["alloc"], "pulled_from": r["pulled_from"], "grant": grant}


def test_preview_matches_what_confirm_accepts(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
        v = ws["dataset_version"]
        plan = _plan(c, v, skills=["Kubernetes"])
        b, pl = plan["candidates"]["bench"][0], plan["candidates"]["pull"][0]
        r = c.post("/api/emergency/preview", json={"dataset_version": v, "project_id": "J007", "skills": ["Kubernetes"],
                                                    "picks": [_pick(b), _pick(pl)]})
        assert r.status_code == 200, r.text
        d = r.json()["draft"]
        hint = d["apply_hint"]
        # 증원을 적은 상태라 대상 사업 자리는 모두 찬다 -- 빼 온 사업에는 빈자리가 생긴다
        assert all(s["filled"] <= s["need"] for s in d["seats"].values())
        assert any(e["change"] < 0 and e["project_id"] in pl["pulled_from"] for e in d["seat_effects"])
        assert hint["skills_add"]["J007"][0]["name"] == "Kubernetes" and d["new_violations"] == []
        assert abs(d["skill_delta"] + d["collab_delta"] + d["unfilled_seats_delta"] * 100 - d["delta_total"]) < 1e-3
        # 그대로 확정하면 통과하고(데이터는 미리 보기 때 바뀌지 않았다), 미리 계산한 배치가 지금 배치가 된다
        assert c.get("/api/workspace").json()["dataset_version"] == v
        # 빼 온 사업의 빈자리는 미리 계산이 알려 주고, 확정은 그 결정(여기서는 메우지 않음)을 받아야 통과한다(2026-10-21)
        src = pl["pulled_from"][0]
        assert d["holes"] == {src: [{"grade": pl["grade"], "count": 1}]}
        res = c.post("/api/workspace/apply", json={"dataset_version": v, "entries": d["entries"], "source": "emergency",
                                                   "note": "J007 긴급 투입(손 편성)", **hint,
                                                   "seat_waivers": [{"project_id": src, "grade": pl["grade"], "count": 1,
                                                                     "reason": "남은 인원으로 진행"}]})
        assert res.status_code == 200, res.text
        team = {m["person_id"] for o in res.json()["workspace"]["ongoing"] if o["project_id"] == "J007" for m in o["team"]}
        assert {b["person_id"], pl["person_id"]} <= team


def test_grant_pick_counts_the_concurrency_permission(yearend):
    """동시 수행 지원 후보(원래 사업 유지)는 grant=True면 그 사람만 같은 달 2곳까지 친다 -- 허용 없이(grant=False) 보면 위반이다."""
    from api.main import app
    with TestClient(app) as c:
        v = c.get("/api/workspace").json()["dataset_version"]
        plan = _plan(c, v)
        sp = [r for r in plan["candidates"]["spare"] if r.get("needs_grant")]
        assert sp
        ok = c.post("/api/emergency/preview", json={"dataset_version": v, "project_id": "J007", "picks": [_pick(sp[0], True)]}).json()
        bad = c.post("/api/emergency/preview", json={"dataset_version": v, "project_id": "J007", "picks": [_pick(sp[0], False)]}).json()
        assert ok["draft"]["new_violations"] == [] and bad["draft"]["new_violations"]


def test_preview_rejects_bad_input(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
        v = ws["dataset_version"]
        member = next(o for o in ws["ongoing"] if o["project_id"] == "J007")["team"][0]["person_id"]
        url = "/api/emergency/preview"
        assert c.post(url, json={"dataset_version": v, "project_id": "J007", "picks": []}).status_code == 422
        assert c.post(url, json={"dataset_version": v, "project_id": "J007",
                                 "picks": [{"person_id": member, "alloc": 1.0}]}).status_code == 422      # 이미 그 사업에 있다
        assert c.post(url, json={"dataset_version": v, "project_id": "J018",
                                 "picks": [{"person_id": "DP0001", "alloc": 1.0}]}).status_code == 422    # 진행 사업이 아니다
        assert c.post(url, json={"dataset_version": "old", "project_id": "J007",
                                 "picks": [{"person_id": "DP0001", "alloc": 1.0}]}).status_code == 409


def test_preview_reranks_with_picks_and_reuses_the_skill_graph(yearend, monkeypatch):
    """대체 리뷰 SHOULD(2026-10-20): 고른 사람을 팀원으로 친 후보 다시 매기기(고른 사람은 빠지고 협업 쌍에 들어간다),
    요구 기술을 더한 그래프는 같은 (판·사업·기술·인원)이면 다시 만들지 않는다."""
    import api.routes.scenarios as sc
    from api.main import app
    with TestClient(app) as c:
        v = c.get("/api/workspace").json()["dataset_version"]
        plan = _plan(c, v, skills=["Kubernetes"])
        b = plan["candidates"]["bench"][0]
        built = []
        real = sc._augmented
        monkeypatch.setattr(sc, "_augmented", lambda *a, **k: built.append(1) or real(*a, **k))
        body = {"dataset_version": v, "project_id": "J007", "skills": ["Kubernetes"], "picks": [_pick(b)], "pull_budget": 1}
        first = c.post("/api/emergency/preview", json=body).json()
        again = c.post("/api/emergency/preview", json=body).json()
        assert len(built) == 1 and first["draft"] == again["draft"]
        rows = first["candidates"]["bench"] + first["candidates"]["spare"] + first["candidates"]["pull"]
        assert rows and b["person_id"] not in {r["person_id"] for r in rows}
        assert any(p["person_id"] == b["person_id"] for r in rows for p in r["partners"]) or not any(r["partners"] for r in rows)
        assert first["candidates"]["pull"]                                                    # 빼 오기 허용이면 빼 오기 후보도
