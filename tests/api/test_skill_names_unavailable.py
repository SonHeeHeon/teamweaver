"""낯선 기술 이름 조회(GET /api/skills/unknown)는 읽기라 원천 묶음을 고칠 수 없는 데이터(고정 데모)에서도 200 + 까닭이다
(2026-10-22 -- 409가 데이터 탭을 열 때마다 브라우저 콘솔 오류로 남아 느린 E2E가 실패했다)."""
from fastapi.testclient import TestClient


def test_unknown_skills_on_fixed_demo_data_is_empty_with_reason():
    from api.main import app
    with TestClient(app) as c:
        if c.get("/api/workspace").json().get("editable"):
            raise AssertionError("기본 데이터가 CSV 묶음이다 -- 이 시험의 전제(고정 데모)가 바뀌었다")
        r = c.get("/api/skills/unknown")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"] == [] and body["unavailable"]
