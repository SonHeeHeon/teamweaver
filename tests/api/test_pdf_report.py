"""PDF 산출 스모크.

**TestClient를 쓸 수 없다.** Playwright가 실제 HTTP로 /report와 정적 에셋을
가져와야 하는데 TestClient는 in-process ASGI 전송이라 리스닝 포트가 아예
없다(base_url이 http://testserver라 DNS부터 실패한다). 그래서 진짜 uvicorn을
백그라운드 스레드에 띄우고 그 주소로 요청한다.

빌드 산출물(web/dist)이 없으면 인쇄할 페이지 자체가 없으므로 건너뛴다 --
이 경우는 실패가 아니라 '아직 빌드 안 함'이며, 그 둘을 구분하지 않으면
CI에서 잘못된 안심을 준다.

**크기 임계값만으로는 부족하다.** 최종 리뷰에서 실측된 사실: /report에
__REPORT_DATA__를 주입하지 않고 그냥 열어도(당시 코드는 __REPORT_READY__를
meta만으로 세웠다) "리포트 데이터가 없다"는 빈 페이지가 18,448바이트짜리
PDF로 나왔고, `len(body) > 10_000`은 이걸 통과시켰다. 그래서 여기서는
pypdf로 실제 텍스트를 뽑아 payload에만 있는 마커(plan_label, 배치된 인력
이름, 브리핑 rationale, fallback 배지 문구)가 PDF 안에 박혔는지 확인한다.
크기만 재는 것과 실제로 다르다는 것은 fault injection으로 증명했다 --
tests/api/test_pdf_report.py의 커밋 메시지 참고: __REPORT_DATA__를 일부러
주입하지 않고 만든 PDF는 크기 임계값은 통과하지만 이 텍스트 단언들은 실패한다.
"""
import io
import os
import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from pypdf import PdfReader

from api.main import app
from core.config import REPO_ROOT

pytestmark = pytest.mark.slow

_PAYLOAD = {
    "plan_label": "A",
    "entries": [{"person_id": "p000", "project_id": "j00", "alloc": 1.0}],
    "objective": 12.3,
    "fulfillment": 0.71,
    "optimization_ratio": 0.928,
    "unfilled": [],
    "briefing": {"rationale": "테스트 근거 마커 XYZZY-RATIONALE",
                 "risks": ["r1"], "alternatives": ["a1"]},
    "fallback_used": True,
    "swap": {"out_person_id": "p000", "in_person_id": "p001", "project_id": "j00"},
    "objective_delta": -0.25,
}


def _extract_text(pdf_bytes: bytes) -> str:
    """pypdf가 뽑아내는 텍스트는 자간 때문에 단어 사이에 여러 칸 공백이
    섞인다(예: "테스트  근거  마커") -- 연속 공백을 하나로 접어 비교를
    안정시킨다."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"[ \t]+", " ", raw)


def _norm(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s)


def _dist_missing() -> bool:
    return not (REPO_ROOT / "web" / "dist" / "index.html").exists()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def live_server():
    """실제 포트를 여는 uvicorn 인스턴스.

    conftest.py의 _skip_warm autouse 픽스처는 함수 스코프 monkeypatch라
    모듈 스코프인 이 픽스처의 기동 시점을 덮지 못한다 -- 여기서 직접
    환경변수를 세워 워밍을 건너뛴다(PDF 검증에 워밍은 무관하다).
    """
    prev = os.environ.get("TEAMWEAVER_SKIP_WARM")
    os.environ["TEAMWEAVER_SKIP_WARM"] = "1"
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):                       # 최대 ~20초
            if server.started:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn이 기동하지 않았다")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if prev is None:
            os.environ.pop("TEAMWEAVER_SKIP_WARM", None)
        else:
            os.environ["TEAMWEAVER_SKIP_WARM"] = prev


@pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")
def test_report_returns_a_real_pdf(live_server):
    res = httpx.post(f"{live_server}/api/report", json=_PAYLOAD, timeout=120.0)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    body = res.content
    assert body.startswith(b"%PDF-"), "PDF 매직 바이트가 아니다"
    assert len(body) > 10_000, f"PDF가 비정상적으로 작다({len(body)}바이트) -- 빈 페이지 의심"

    # 크기만으로는 빈 페이지("리포트 데이터가 없다")도 통과한다(실측
    # 18,448바이트). payload에만 있는 값들이 실제로 페이지에 렌더됐는지
    # 텍스트로 직접 확인한다.
    text = _extract_text(body)
    assert "리포트 데이터가 없다" not in text
    assert "Plan A" in text                                    # plan_label
    assert "김나윤" in text                                     # 배치 인력(p000) 이름
    assert _norm(_PAYLOAD["briefing"]["rationale"]) in text     # 브리핑 rationale
    assert "규칙 기반" in text                                  # fallback_used=True 배지 문구
    assert "서지훈" in text                                     # swap.in_person_id(p001) 이름


@pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")
def test_report_page_is_served_at_the_client_route(live_server):
    """Playwright가 들어갈 /report가 실제로 index.html을 돌려주는지 확인한다.
    StaticFiles(html=True) 폴백이 깨지면 PDF는 빈 페이지가 된다."""
    res = httpx.get(f"{live_server}/report", timeout=30.0)
    assert res.status_code == 200
    assert "<div id=\"root\"" in res.text
