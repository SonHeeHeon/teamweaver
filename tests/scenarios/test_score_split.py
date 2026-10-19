"""후보 점수 변화 나눠 보이기(사용자 2026-10-19 "점수 변화는 나눠 보이도록 하자"): 빈자리 몫(자리 수)과 그 밖의 변화(기술·협업)를
따로 내고, 둘을 더하면 원래 합계(delta_total)가 된다. 순위·합계는 그대로다."""
import pytest
from fastapi.testclient import TestClient

from api.routes.scenarios import split_unfilled
from core.optimize.milp import MilpParams

PENALTY = MilpParams().slack_penalty          # 설정 화면에 없는 값 -- 서비스도 기본값을 쓴다


@pytest.mark.parametrize("row, seats, rest", [
    ({"delta_total": 101.2, "delta": {"total": 101.2, "unfilled": 100.0}}, 1, 1.2),        # 신규 사업 빈자리 하나를 메움
    ({"delta_total": 0.5, "delta": {"total": 0.5, "unfilled": 0.0}}, 0, 0.5),              # 빼 와서 메운 만큼 원래 사업에 생김
    ({"delta_total": -98.0, "delta": {"total": -98.0, "unfilled": -100.0}}, -1, 2.0),      # 빈자리가 새로 생김
    ({"delta": {"total": 201.3, "unfilled": 200.0}}, 2, 1.3),                              # 최선 조합: delta_total 없이 delta만
    ({"delta_total": 1.0}, 0, 1.0),                                                        # 나눌 재료가 없으면 모두 나머지
])
def test_split_unfilled_adds_up(row, seats, rest):
    out = split_unfilled(row, 100.0)
    assert out["unfilled_seats_delta"] == seats and out["delta_excl_unfilled"] == pytest.approx(rest)
    assert {k: out[k] for k in row} == row                                                 # 원래 칸은 그대로


def test_split_unfilled_without_a_penalty_counts_no_seats():
    assert split_unfilled({"delta_total": 3.0, "delta": {"unfilled": 0.0}}, 0)["unfilled_seats_delta"] == 0


def _adds_up(r):
    return abs(r["delta_total"] - (r["unfilled_seats_delta"] * PENALTY + r["delta_excl_unfilled"])) < 1e-3


def test_pipeline_and_emergency_rows_carry_the_split(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = c.get("/api/workspace").json()["dataset_version"]
        cand = c.get("/api/pipeline/J019/candidates", params={"dataset_version": v}).json()
        rows = cand["bench"] + cand["ending"] + cand["pull"]
        assert rows and all(_adds_up(r) for r in rows)
        # 신규 사업 빈자리를 메우는 대기 인력은 자리 1, 나머지는 사람끼리의 작은 차이만 남는다
        filling = [r for r in cand["bench"] if r["unfilled_seats_delta"] == 1]
        assert filling and all(abs(r["delta_excl_unfilled"]) < 50 for r in filling)

        plan = c.post("/api/emergency/plan", json={"dataset_version": v, "project_id": "J007", "n": 1,
                                                   "pull_budget": 1}).json()
        groups = plan["candidates"]["bench"] + plan["candidates"]["spare"] + plan["candidates"]["pull"]
        assert groups and all(_adds_up(r) for r in groups)
        # 빼 오면 원래 사업에 빈자리가 생긴다(긴급 투입은 대상 사업 자리를 늘려 채점 -- 메움 0, 생김 −1)
        assert all(r["unfilled_seats_delta"] <= 0 for r in plan["candidates"]["pull"])
        assert plan["standby"] and all(_adds_up(r) for r in plan["standby"])
        best = plan["best"]
        # 빼 오는 사람은 이름과 함께 온다(부서장 점검 f7 -- 화면이 "DP0075(J013→J007)"처럼 사번을 보였다)
        names = {p["id"]: p["name"] for p in c.get("/api/meta").json()["people"]}
        assert best["diff"]["moved"] and all(m["name"] == names[m["person_id"]] != m["person_id"] for m in best["diff"]["moved"])
        assert best["accepted"] and abs(best["delta"]["total"]
                                        - (best["unfilled_seats_delta"] * PENALTY + best["delta_excl_unfilled"])) < 1e-3

        live = c.get("/api/emergency/standby", params={"dataset_version": v, "project_id": "J007"}).json()
        assert live["source"] == "live" and live["projects"]["J007"]
        assert all(_adds_up(r) for r in live["projects"]["J007"])
