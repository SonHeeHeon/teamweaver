"""K4: PDF 생성이 요청 Host를 믿지 않고, 자원 상한을 지키는지 (빠른 테스트).

브라우저를 띄우지 않는다 -- `render_report_pdf`를 가짜로 바꿔 라우트가 그것을
*어떤 origin으로* 부르는지, 상한 초과를 어떤 상태코드로 끊는지만 본다.
실제 Chromium이 외부로 나가지 않는지는 tests/api/test_pdf_report.py(slow)가 본다.
"""
import asyncio

import pytest

import api.routes.report as report_route
from api import pdf
from api.pdf import OriginUnavailable, PdfSettings, canonical_origin, resolve_internal_origin

_PAYLOAD = {
    "plan_label": "A",
    "entries": [{"person_id": "p000", "project_id": "j00", "alloc": 1.0}],
    "objective": 1.0, "fulfillment": 0.5, "optimization_ratio": 0.9,
}


# --- origin 결정 ---------------------------------------------------------

def test_origin_comes_from_bound_socket_not_host_header():
    scope = {"server": ("127.0.0.1", 54321),
             "headers": [(b"host", b"attacker.example:80")]}
    assert resolve_internal_origin(scope, override=None) == "http://127.0.0.1:54321"


def test_ipv6_bound_address_is_bracketed():
    assert resolve_internal_origin({"server": ("::1", 8000)}, None) == "http://[::1]:8000"


def test_override_wins_over_bound_socket():
    scope = {"server": ("127.0.0.1", 54321)}
    assert resolve_internal_origin(scope, "http://10.0.0.5:9000") == "http://10.0.0.5:9000"


def test_dual_stack_socket_ipv4_client_maps_to_ipv4_origin():
    """`uvicorn --host ::`(듀얼스택)에 IPv4로 접속하면 getsockname이
    ::ffff:127.0.0.1을 준다 -- 그대로 쓰면 브라우저 origin과 어긋나 PDF가 막힌다."""
    assert resolve_internal_origin({"server": ("::ffff:127.0.0.1", 8000)}, None) \
        == "http://127.0.0.1:8000"


@pytest.mark.parametrize("url", ["http://127.1:8000", "http://2130706433",
                                 "http://0x7f.0.0.1", "http://foo.123",
                                 "http://[fe80::1%25en0]:8000",
                                 "http://보고서.example:8000",   # IDNA2003/UTS46 차이 회피
                                 "http://straße.de"])
def test_non_canonical_ip_forms_are_rejected(url):
    with pytest.raises(ValueError):
        canonical_origin(url)


def test_zone_id_bind_address_is_unavailable_not_500():
    with pytest.raises(OriginUnavailable):
        resolve_internal_origin({"server": ("fe80::1%en0", 8000)}, None)


def test_unix_socket_without_override_is_unavailable():
    with pytest.raises(OriginUnavailable):
        resolve_internal_origin({"server": ("/tmp/tw.sock", None)}, None)
    with pytest.raises(OriginUnavailable):
        resolve_internal_origin({}, None)


@pytest.mark.parametrize("url, expected", [
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
    ("HTTP://LocalHost:80", "http://localhost"),           # 기본 포트는 location.origin처럼 생략
    ("https://report.internal:443", "https://report.internal"),
    ("http://[::1]:8000/report?x=1", "http://[::1]:8000"),  # 요청 URL -> origin 비교용
    # 리뷰 실측: Chromium의 location.origin과 같은 정규형이어야 비교가 맞는다.
    ("http://[::ffff:127.0.0.1]:8000", "http://127.0.0.1:8000"),
    ("http://[0:0:0:0:0:0:0:1]:80", "http://[::1]"),
    ("http://xn--299ax81ai7c.example:8000", "http://xn--299ax81ai7c.example:8000"),
    ("http://123.example", "http://123.example"),          # 숫자 label이어도 마지막이 아니면 도메인
    ("http://0xdeadbeef.com", "http://0xdeadbeef.com"),
])
def test_canonical_origin_matches_browser_location_origin(url, expected):
    assert canonical_origin(url) == expected


@pytest.mark.parametrize("bad", [
    "127.0.0.1:8000",                 # scheme 없음
    "ftp://127.0.0.1",
    "http://127.0.0.1:8000/report",   # 경로
    "http://127.0.0.1:8000?x=1",
    "http://user:pw@127.0.0.1:8000",  # 사용자 정보
    "http://127.0.0.1:99999",
    "http://",
])
def test_invalid_origin_override_is_rejected(monkeypatch, bad):
    monkeypatch.setenv("TEAMWEAVER_PDF_ORIGIN", bad)
    with pytest.raises(ValueError):
        PdfSettings.from_env()


@pytest.mark.parametrize("name, value", [
    ("TEAMWEAVER_PDF_MAX_BODY_BYTES", "0"),
    ("TEAMWEAVER_PDF_MAX_CONCURRENCY", "-1"),
    ("TEAMWEAVER_PDF_TIMEOUT_S", "abc"),
    ("TEAMWEAVER_PDF_TIMEOUT_S", "inf"),      # 상한이 사라진다
    ("TEAMWEAVER_PDF_TIMEOUT_S", "nan"),
])
def test_invalid_limit_env_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        PdfSettings.from_env()


# --- 라우트: Host 무시 ---------------------------------------------------

@pytest.fixture
def ready(monkeypatch, tmp_path):
    """web/dist 존재 검사를 통과시키고, 가짜 렌더러가 받은 인자를 기록한다."""
    index = tmp_path / "index.html"
    index.write_text("<div id=\"root\"></div>")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    calls: list[dict] = []

    async def fake_render(payload, origin, timeout_s):
        calls.append({"payload": payload, "origin": origin, "timeout_s": timeout_s})
        return b"%PDF-fake"

    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    return calls


def test_forged_host_header_does_not_reach_renderer(client, ready):
    res = client.post("/api/report", json=_PAYLOAD,
                      headers={"Host": "attacker.example:8080"})
    assert res.status_code == 200, res.text
    assert len(ready) == 1
    # TestClient의 scope["server"]는 ("testserver", 80)이다 -- Host 헤더 값이 아니다.
    assert ready[0]["origin"] == "http://testserver"
    assert "attacker" not in ready[0]["origin"]


def test_bad_origin_setting_is_503_not_silent_fallback(client, ready, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_ORIGIN", "http://127.0.0.1:8000/report")
    res = client.post("/api/report", json=_PAYLOAD)
    assert res.status_code == 503
    assert "TEAMWEAVER_PDF_ORIGIN" in res.json()["detail"]
    assert ready == []


# --- 본문 크기 ----------------------------------------------------------

def test_body_over_limit_is_413_by_content_length(client, ready, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "1000")
    big = dict(_PAYLOAD, unfilled=["x" * 2000])
    res = client.post("/api/report", json=big)
    assert res.status_code == 413
    assert ready == []


def test_chunked_body_over_limit_is_413(client, ready, monkeypatch):
    """Content-Length 없이(청크 전송) 보내도 스트림을 세어 끊는다."""
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "1000")
    chunks = [b'{"plan_label": "A", "unfilled": ["', b"x" * 3000, b'"]}']
    res = client.post("/api/report", content=iter(chunks),
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 413
    assert ready == []


def test_body_limit_holds_under_root_path(ready, monkeypatch):
    """`uvicorn --root-path /tw`이면 scope["path"]가 "/tw/api/report"가 된다.
    라우터는 root_path를 떼고 매칭하므로 미들웨어도 떼야 한다(리뷰 실측 413→422)."""
    from fastapi.testclient import TestClient

    from api.main import app

    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "1000")
    big = dict(_PAYLOAD, unfilled=["x" * 2000])
    # TestClient는 uvicorn과 달리 scope["path"]에 root_path를 붙이지 않는다 --
    # uvicorn과 같은 scope("/tw/api/report", root_path "/tw")를 만들려고 경로에 직접 붙인다.
    with TestClient(app, root_path="/tw") as c:
        res = c.post("/tw/api/report", json=big)
        chunked = c.post("/tw/api/report", content=iter([b'{"a": "', b"x" * 3000, b'"}']),
                         headers={"Content-Type": "application/json"})
        ok = c.post("/tw/api/report", json=_PAYLOAD)
    assert res.status_code == 413
    assert chunked.status_code == 413
    assert ok.status_code == 200          # root_path 아래에서도 라우트 자체는 맞는다
    assert len(ready) == 1


def test_413_carries_cors_headers_for_dev_frontend(client, ready, monkeypatch):
    """dev(:5173 → :8000)에서 413 안내를 읽으려면 CORS 헤더가 붙어야 한다."""
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "1000")
    big = dict(_PAYLOAD, unfilled=["x" * 2000])
    res = client.post("/api/report", json=big, headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 413
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_body_limit_applies_only_to_report(client, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "10")
    assert client.get("/api/meta").status_code == 200


def test_body_under_limit_passes(client, ready, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "100000")
    assert client.post("/api/report", json=_PAYLOAD).status_code == 200


# --- 동시성 ------------------------------------------------------------

def test_concurrency_over_limit_is_429_and_slot_is_released(client, ready, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_CONCURRENCY", "1")
    # 다른 요청이 슬롯을 쥐고 있는 상태를 흉내 낸다.
    assert pdf.PDF_SLOTS.try_acquire(1)
    try:
        res = client.post("/api/report", json=_PAYLOAD)
        assert res.status_code == 429
        assert "Retry-After" in res.headers
        assert ready == []
    finally:
        pdf.PDF_SLOTS.release()
    assert client.post("/api/report", json=_PAYLOAD).status_code == 200
    assert pdf.PDF_SLOTS.active == 0


def test_slot_is_released_when_render_fails(client, ready, monkeypatch):
    async def boom(payload, origin, timeout_s):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(report_route, "render_report_pdf", boom)
    res = client.post("/api/report", json=_PAYLOAD)
    assert res.status_code == 500
    # 예외 원문(Playwright call log에는 내부 URL이 있다)은 응답에 싣지 않는다.
    assert "render exploded" not in res.text
    assert pdf.PDF_SLOTS.active == 0


# --- 시간 제한 ----------------------------------------------------------

def test_render_over_time_limit_is_504(client, ready, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_TIMEOUT_S", "0.2")
    cancelled = []

    async def slow(payload, origin, timeout_s):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return b"%PDF-late"

    monkeypatch.setattr(report_route, "render_report_pdf", slow)
    res = client.post("/api/report", json=_PAYLOAD)
    assert res.status_code == 504
    assert cancelled == [True], "시간 초과 시 렌더 작업을 취소해야 브라우저가 정리된다"
    assert pdf.PDF_SLOTS.active == 0


def test_declared_oversize_is_rejected_before_reading_body(monkeypatch):
    """Content-Length가 상한을 넘으면 본문을 한 바이트도 읽지 않고 413을 보낸다
    (스트림 카운트는 받은 만큼은 읽는다 -- 둘은 서로 다른 방어다)."""
    from api.routes.report import ReportBodyLimit

    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "1000")

    async def inner(scope, receive, send):
        raise AssertionError("상한 초과 요청이 앱까지 들어갔다")

    async def receive():
        raise AssertionError("본문을 읽었다")

    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": "/api/report", "method": "POST",
             "headers": [(b"content-length", b"5000")]}
    asyncio.run(ReportBodyLimit(inner)(scope, receive, send))
    assert sent[0]["status"] == 413
