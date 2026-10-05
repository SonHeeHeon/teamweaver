"""부팅 사전계산이 실패해도 서버는 뜬다(C0·C1 main 통합 리뷰 MUST).

C0 이후 Plan A가 독립 검증에 거절되면 solve가 예외를 낸다. 짧은 time_limit 설정이나 업로드
데이터 하나로 그렇게 될 수 있고, 둘 다 저장되므로 예외가 lifespan까지 올라가면 재기동해도
매번 부팅이 막혔다."""
from fastapi.testclient import TestClient

import api.main as main_module
from api.main import app


def test_server_boots_without_cache_when_warmup_fails(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_SKIP_WARM", "0")

    def boom(*a, **k):
        raise RuntimeError("binary_domain: rejected by independent validation")

    monkeypatch.setattr(main_module, "generate_plans", boom)
    with TestClient(app) as c:
        assert c.get("/api/meta").status_code == 200
        assert len(c.app.state.cache._store) == 0
