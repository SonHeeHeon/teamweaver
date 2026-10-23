"""신규 사업 후보 응답의 요구 기술별 충족 인원(2026-10-23 사용자 "넣기 하면 … 해당 인력이 요구 기술 중 만족하는 게 있으면 요구 기술의 항목의
배경 색깔이 칠해진다던지"): `coverage`는 지금 팀, `draft.coverage`는 "넣기"로 고른 사람까지 친 팀이다(데이터는 바뀌지 않는다)."""
from fastapi.testclient import TestClient

from tests.scenarios.test_workspace_api import _ws


def test_candidates_carry_skill_coverage_and_picks_move_it(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = _ws(c)
        cand = c.get("/api/pipeline/J018/candidates", params={"dataset_version": ws["dataset_version"]}).json()
        cov = {r["skill"]: r for r in cand["coverage"]}
        assert set(cov) == {"금융 업무", "비정형 문서 처리(OCR)", "ETL", "메타데이터 관리"}      # J018 요구 기술(생성기 v4 연말형)
        assert all(r["met"] == 0 and r["met_by"] == [] for r in cov.values())               # 아직 팀이 없다
        assert cov["ETL"]["headcount"] == 2 and cov["ETL"]["min_level"] >= 1
        pick = next(r for r in cand["bench"] if not r["new_violations"])
        after = c.post("/api/pipeline/J018/candidates", json={"dataset_version": ws["dataset_version"], "picks": [
            {"person_id": pick["person_id"], "alloc": pick["alloc"], "pulled_from": []}]}).json()
        assert {r["skill"]: r["met"] for r in after["coverage"]} == {k: 0 for k in cov}     # 지금 팀은 그대로
        drafted = after["draft"]["coverage"]
        assert sum(r["met"] for r in drafted) >= 1                                            # 1순위 대기 인력은 요구 기술 하나 이상을 채운다
        assert all(r["met_by"] in ([], [pick["name"]]) for r in drafted)                      # 이름으로 온다
        assert all(r["met"] == len(r["met_by"]) and pick["name"] not in (set(r["met_by"]) & set(r["below_by"])) for r in drafted)
        assert c.get("/api/workspace").json()["dataset_version"] == ws["dataset_version"]     # 미리 계산은 데이터를 바꾸지 않는다
