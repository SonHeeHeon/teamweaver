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
    "swap_violations": ["경고 마커 XYZZY-VIOLATION"],
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
    assert "XYZZY-VIOLATION" in text                            # What-if 교체 경고
    assert "현행 점수 기준" in text                              # delta가 참고값임을 표기


@pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")
def test_report_page_is_served_at_the_client_route(live_server):
    """Playwright가 들어갈 /report가 실제로 index.html을 돌려주는지 확인한다.
    StaticFiles(html=True) 폴백이 깨지면 PDF는 빈 페이지가 된다."""
    res = httpx.get(f"{live_server}/report", timeout=30.0)
    assert res.status_code == 200
    assert "<div id=\"root\"" in res.text



@pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")
def test_report_shows_applied_swaps_recomputed_by_server(live_server):
    """K10: 서버가 다시 계산한 적용 교체 목록이 실제 PDF에 찍힌다."""
    version = httpx.get(f"{live_server}/api/meta", timeout=30.0).json()["dataset_version"]
    # 서버가 원 명단(base_entries)에서 교체를 다시 적용한다 -- 클라이언트가 보낸 경고 문구나
    # 수치는 PDF에 쓰이지 않는다. 투입률 1.0인 p000을 p001로 바꾸면 p001의 가용률·예산에
    # 따라 경고가 생길 수 있다; 여기서는 이력·이름이 서버 계산으로 찍히는지만 본다.
    payload = {**_PAYLOAD, "dataset_version": version,
               "base_entries": [{"person_id": "p000", "project_id": "j00", "alloc": 1.0}],
               "applied_swaps": [{"out_person_id": "p000", "in_person_id": "p001",
                                  "project_id": "j00"}]}
    res = httpx.post(f"{live_server}/api/report", json=payload, timeout=120.0)
    assert res.status_code == 200, res.text
    text = _extract_text(res.content)
    assert "적용된 교체 1건" in text
    assert "김나윤 → 서지훈" in text
    assert "최적화가 고른 명단이" in text

# --- K4: Host를 믿지 않고 외부로 나가지 않는다 -------------------------------

class _Decoy:
    """'공격자 서버' 역할의 미끼. 받은 요청 경로를 모두 기록한다."""

    def __init__(self) -> None:
        import http.server

        hits = self.hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                     # noqa: N802
                hits.append(self.path)
                body = b"<html><body>decoy</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET                      # noqa: N815

            def log_message(self, *args):         # 테스트 출력 오염 방지
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def decoy():
    d = _Decoy()
    yield d
    d.close()


@pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")
def test_forged_host_never_sends_browser_to_that_host(live_server, decoy):
    """Host를 미끼 서버로 위조해도 PDF 브라우저는 서버 자신에게만 간다.
    예전 코드(request.base_url 사용)에서는 브라우저가 미끼의 /report를 열고
    거기서 __REPORT_READY__를 기다리다 실패했다(결함 주입으로 확인 -- 리포트 참고)."""
    res = httpx.post(f"{live_server}/api/report", json=_PAYLOAD, timeout=120.0,
                     headers={"Host": f"127.0.0.1:{decoy.port}"})
    assert res.status_code == 200, res.text
    assert decoy.hits == [], f"브라우저가 위조 Host로 나갔다: {decoy.hits}"
    text = _extract_text(res.content)
    assert "XYZZY-VIOLATION" in text            # 정상 리포트가 내부 origin에서 렌더됐다


@pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")
def test_browser_blocks_every_request_outside_internal_origin(live_server, decoy):
    """route 가드가 fetch·이미지·페이지 이동·웹소켓을 내부 origin 밖으로 못 내보낸다.
    가드 없는 컨텍스트에서는 같은 fetch가 미끼에 닿는다(대조군) -- 미끼에 원래
    닿을 수 없어서 0건인 것이 아님을 보인다."""
    import asyncio

    from playwright.async_api import async_playwright

    from api.pdf import _restrict_to_origin, canonical_origin, report_data_script

    origin = canonical_origin(live_server)

    async def run() -> tuple[list[str], dict, tuple]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                # 대조군: 가드 없음 -> 미끼에 닿는다.
                open_ctx = await browser.new_context()
                open_page = await open_ctx.new_page()
                # 데이터 주입 스크립트도 함께 건다: 내부 origin에서만 값이 생겨야 한다.
                await open_page.add_init_script(report_data_script({"k": "SECRET"}, origin))
                await open_page.goto(f"{live_server}/report")
                data_inside = await open_page.evaluate("window.__REPORT_DATA__?.k ?? null")
                await open_page.evaluate(
                    "u => fetch(u, {mode: 'no-cors'}).then(() => true)",
                    f"{decoy.origin}/control")
                await open_page.goto(f"{decoy.origin}/page")   # 가드 없으니 이동된다
                data_outside = await open_page.evaluate("window.__REPORT_DATA__ ?? null")
                await open_ctx.close()

                blocked: list[str] = []
                ctx = await browser.new_context(service_workers="block")
                await _restrict_to_origin(ctx, origin, blocked)
                page = await ctx.new_page()
                await page.goto(f"{live_server}/report")
                out = await page.evaluate("""async (d) => {
                    const r = {};
                    try { await fetch(d + '/fetch', {mode: 'no-cors'}); r.fetch = 'ok'; }
                    catch (e) { r.fetch = 'blocked'; }
                    r.img = await new Promise(res => {
                        const i = new Image();
                        i.onload = () => res('ok'); i.onerror = () => res('blocked');
                        i.src = d + '/img.png';
                    });
                    r.ws = await new Promise(res => {
                        const ws = new WebSocket(d.replace('http', 'ws') + '/ws');
                        ws.onopen = () => res('open');
                        ws.onclose = () => res('closed'); ws.onerror = () => res('closed');
                    });
                    // 같은 origin 요청은 통과해야 한다(가드가 전부 막는 게 아님).
                    r.same = (await fetch('/api/meta')).status;
                    return r;
                }""", decoy.origin)
                try:
                    await page.goto(f"{decoy.origin}/nav")
                    out["nav"] = "ok"
                except Exception:                  # noqa: BLE001
                    out["nav"] = "blocked"
                return blocked, out, (data_inside, data_outside)
            finally:
                await browser.close()

    blocked, out, (data_inside, data_outside) = asyncio.run(run())
    assert decoy.hits == ["/control", "/page"], f"가드 아래에서 미끼에 닿았다: {decoy.hits}"
    # 심층 방어: 가드가 없어 다른 origin 문서가 열려도 리포트 데이터는 주입되지 않는다.
    assert data_inside == "SECRET"
    assert data_outside is None
    assert out == {"fetch": "blocked", "img": "blocked", "ws": "closed",
                   "same": 200, "nav": "blocked"}, out
    assert any(u.endswith("/fetch") for u in blocked)
    assert any(u.endswith("/nav") for u in blocked)


@pytest.mark.skipif(_dist_missing(), reason="playwright chromium 필요")
def test_redirect_out_of_origin_is_detected_and_gets_no_data(decoy):
    """알려진 한계를 고정한다: route()는 리다이렉트 첫 요청만 보므로 내부 origin이
    302로 외부를 가리키면 그 요청은 나간다. 대신 (1) escaped에 기록돼
    render_report_pdf가 실패하고 (2) 그 문서에는 리포트 데이터가 없다."""
    import asyncio
    import http.server

    from playwright.async_api import async_playwright

    from api.pdf import _restrict_to_origin, canonical_origin, report_data_script

    target = decoy.origin

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                         # noqa: N802
            if self.path.startswith("/redir"):
                self.send_response(302)
                self.send_header("Location", f"{target}/landed")
                self.end_headers()
                return
            body = b"<html><body>origin</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = canonical_origin(f"http://127.0.0.1:{srv.server_address[1]}")

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                blocked, escaped = [], []
                ctx = await browser.new_context(service_workers="block")
                await _restrict_to_origin(ctx, origin, blocked, escaped)
                page = await ctx.new_page()
                await page.add_init_script(report_data_script({"k": "SECRET"}, origin))
                await page.goto(f"{origin}/page")
                await page.goto(f"{origin}/redir")         # 302 -> decoy
                leaked = await page.evaluate("window.__REPORT_DATA__ ?? null")
                return escaped, leaked, page.url
            finally:
                await browser.close()

    try:
        escaped, leaked, final_url = asyncio.run(run())
    finally:
        srv.shutdown()
        srv.server_close()
    assert final_url.startswith(target)                  # 한계: 실제로 나갔다
    assert decoy.hits == ["/landed"]
    assert escaped == [f"{target}/landed"]               # 탐지된다 -> 렌더 실패 처리
    assert leaked is None                                # 데이터는 주입되지 않았다
