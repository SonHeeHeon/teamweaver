"""Playwright headless로 SPA의 /report 라우트를 인쇄해 PDF를 만든다.

데이터는 URL 쿼리(길이 한계)나 서버 임시 저장(stateless 위반)이 아니라
add_init_script로 window.__REPORT_DATA__에 주입한다 -- 페이지 스크립트보다
먼저 실행되므로 React가 마운트될 때 이미 값이 있다.

리포트 페이지는 레이아웃이 끝나면 window.__REPORT_READY__ = true를 세우고,
여기서는 그것을 기다린다. 고정 sleep은 느리면 깨지고 빠르면 낭비다.

**브라우저가 갈 주소는 요청의 Host 헤더로 만들지 않는다(K4, 감사 [A-P1]).**
Host는 요청자가 마음대로 쓰는 값이라, 예전처럼 `request.base_url`을 쓰면
`Host: attacker`인 요청 하나로 서버 안의 Chromium이 공격자 페이지를 열고
init script가 배치 명단을 그 페이지에 넘겼다. 이제 주소는 (1) 운영자 설정
TEAMWEAVER_PDF_ORIGIN 또는 (2) 이 연결을 실제로 받은 로컬 소켓 주소
(ASGI scope["server"], uvicorn이 getsockname으로 채움)에서만 온다. 그리고
브라우저의 하위 요청을 그 origin으로 제한한다(리다이렉트 한계는 _restrict_to_origin 참고).
"""
import ipaddress
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# 기본 상한은 300명/60프로젝트 합성 데이터 실측(2026-10-05, 맥미니, 3회 반복)에
# 여유를 둔 값이다. 환경변수로 덮어쓸 수 있다.
# - 본문: 1인 최대 5건(최소 투입 0.2 × 가용률 ≤ 1) = 1,500건 + 큰 브리핑 = 143KB → 2 MiB(~14배)
# - 시간: 그 최악치 렌더 0.74초(중앙값), Greedy 218건 0.52초 → 60초(느린 서버 대비)
# - 동시성: headless 브라우저 1개가 ~0.4GB, 2건 동시 0.85초 → 2건
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_CONCURRENCY = 2
DEFAULT_TIMEOUT_S = 60.0

log = logging.getLogger(__name__)


class BrowserLaunchError(RuntimeError):
    """chromium 실행 파일이 없어 launch()가 실패했을 때만 던진다. report.py가
    이 경우(설치 미완료)를 503으로, 그 밖의 렌더링 실패(타임아웃 등)를 500으로
    구분해 돌려줄 수 있게 신호를 분리한다."""


class OriginUnavailable(RuntimeError):
    """설정도 없고 바인드 주소에서 TCP origin을 만들 수도 없을 때(유닉스 소켓 등)."""


def canonical_origin(url: str) -> str:
    """URL을 브라우저 `location.origin`과 같은 모양으로 줄인다.

    scheme·host는 소문자, 기본 포트(http 80, https 443)는 생략, IPv6는 [].
    origin 비교(route 허용 판정)와 init script의 비교 문자열이 모두 이 함수
    하나에서 나오므로, 둘이 서로 다른 표기로 어긋날 수 없다.

    IP 주소는 Chromium처럼 정규화한다: IPv6는 압축형(`0:..:1` → `::1`),
    IPv4-mapped(`::ffff:127.0.0.1`)는 IPv4로 푼다. 듀얼스택(`--host ::`)
    소켓에 IPv4로 접속하면 getsockname이 mapped 주소를 주는데, 이를 그대로
    쓰면 Chromium의 location.origin(`[::ffff:7f00:1]`)과 어긋나 PDF가 전부
    실패한다(리뷰 실측). zone id(`fe80::1%en0`)는 URL로 표현할 수 없어 거부한다.
    비ASCII 도메인은 거부한다: Python idna 코덱(IDNA2003)과 Chromium(UTS46)이
    `ß` 같은 글자를 다르게 바꿔, 의도와 다른 호스트로 가서 데이터를 넣을 수
    있다(리뷰 실측 `straße.de` → `strasse.de`). punycode로 직접 쓰면 된다."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname                      # 소문자, [] 제거
    if scheme not in ("http", "https") or not host:
        raise ValueError(f"http(s) origin이 아니다: {url!r}")
    port = parts.port                          # 범위 밖이면 ValueError
    host = _canonical_host(host)
    if ":" in host:
        host = f"[{host}]"
    default = 80 if scheme == "http" else 443
    return f"{scheme}://{host}" if port in (None, default) else f"{scheme}://{host}:{port}"


def _canonical_host(host: str) -> str:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not host.isascii():
            raise ValueError(f"비ASCII 호스트는 punycode(xn--...)로 적을 것: {host!r}") from None
        # WHATWG URL은 마지막 label이 숫자나 0x 숫자면 호스트 전체를 IPv4로 푼다
        # (`127.1`, `2130706433`, `0x7f.0.0.1`, `foo.123`). 정규형이 아니면
        # Chromium과 비교가 어긋나므로 추측하지 않고 거부한다.
        last = host.rstrip(".").rsplit(".", 1)[-1]
        if last.isdigit() or re.fullmatch(r"0x[0-9a-f]*", last):
            raise ValueError(f"정규형이 아닌 IP 표기: {host!r}") from None
        return host
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            raise ValueError(f"zone id가 있는 IPv6 주소는 쓸 수 없다: {host!r}")
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
    return str(ip)


def _parse_origin_setting(value: str) -> str:
    parts = urlsplit(value)
    if parts.path not in ("", "/") or parts.query or parts.fragment \
            or parts.username is not None or parts.password is not None:
        raise ValueError(
            f"TEAMWEAVER_PDF_ORIGIN은 scheme://host[:port] 형식이어야 한다(경로·쿼리·"
            f"사용자 정보 불가): {value!r}")
    try:
        return canonical_origin(value)
    except ValueError as exc:
        raise ValueError(f"TEAMWEAVER_PDF_ORIGIN이 올바르지 않다: {value!r} ({exc})") from exc


def _positive(name: str, default, cast):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name}은 양수여야 한다: {raw!r}") from exc
    if not (value > 0 and math.isfinite(value)):
        raise ValueError(f"{name}은 양수여야 한다: {raw!r}")
    return value


@dataclass(frozen=True)
class PdfSettings:
    origin_override: str | None
    max_body_bytes: int
    max_concurrency: int
    timeout_s: float

    @classmethod
    def from_env(cls) -> "PdfSettings":
        """요청마다 읽는다(값싼 연산이고, 재기동 없이 테스트에서 바꿀 수 있다).
        잘못된 값은 ValueError -- 조용히 기본값으로 돌아가면 운영자가 건
        상한·주소가 무시된 채 동작하므로 실패시킨다."""
        raw_origin = os.environ.get("TEAMWEAVER_PDF_ORIGIN", "").strip()
        return cls(
            origin_override=_parse_origin_setting(raw_origin) if raw_origin else None,
            max_body_bytes=_positive("TEAMWEAVER_PDF_MAX_BODY_BYTES",
                                     DEFAULT_MAX_BODY_BYTES, int),
            max_concurrency=_positive("TEAMWEAVER_PDF_MAX_CONCURRENCY",
                                      DEFAULT_MAX_CONCURRENCY, int),
            timeout_s=_positive("TEAMWEAVER_PDF_TIMEOUT_S", DEFAULT_TIMEOUT_S, float),
        )


def resolve_internal_origin(scope: dict, override: str | None) -> str:
    """브라우저가 접속할 origin. Host 헤더는 보지 않는다.

    scope["server"]는 이 연결을 받은 로컬 소켓의 (주소, 포트)다. 0.0.0.0에
    바인드해도 수락된 소켓은 구체 주소를 돌려주므로 서버 자신을 가리킨다.
    프록시 헤더 미들웨어도 client·scheme만 바꾸고 server는 건드리지 않는다.
    TLS를 uvicorn이 직접 종단하거나 다른 내부 주소를 써야 하면 설정으로 준다."""
    if override:
        return override
    server = scope.get("server")
    if not server or server[1] is None:
        raise OriginUnavailable(
            "PDF 브라우저가 접속할 내부 주소를 알 수 없다(TCP가 아닌 바인드). "
            "TEAMWEAVER_PDF_ORIGIN=http://<host>:<port> 로 지정할 것.")
    host, port = server
    try:
        return canonical_origin(f"http://{f'[{host}]' if ':' in host else host}:{port}")
    except ValueError as exc:                   # 예: zone id가 붙은 링크로컬 IPv6 바인드
        raise OriginUnavailable(
            f"바인드 주소 {host!r}로 내부 origin을 만들 수 없다({exc}). "
            "TEAMWEAVER_PDF_ORIGIN=http://<host>:<port> 로 지정할 것.") from exc


class _Slots:
    """동시 PDF 생성 수 상한. 이벤트 루프 하나에서만 쓰이고 try_acquire와
    release 사이에 await가 없으므로 락이 필요 없다. 대기열 없이 즉시
    거절한다 -- 대기열을 두면 브라우저 수는 묶여도 대기 연결이 무한히 쌓인다.

    한계: 프로세스마다 따로 센다(`uvicorn --workers N`이면 실제 상한은 N배).
    슬롯은 본문 파싱 뒤에 잡으므로, 본문을 받는 중인 연결 수는 이 상한이 아니라
    본문 상한(연결당 MAX_BODY_BYTES)으로만 묶인다."""

    def __init__(self) -> None:
        self.active = 0

    def try_acquire(self, limit: int) -> bool:
        if self.active >= limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


PDF_SLOTS = _Slots()


def _same_origin(url: str, origin: str) -> bool:
    try:
        return canonical_origin(url) == origin
    except ValueError:
        return False


async def _restrict_to_origin(context, origin: str, blocked: list[str],
                              escaped: list[str] | None = None) -> None:
    """origin이 정확히 같은 요청만 통과시키고 나머지(외부 이미지·스크립트·fetch·
    페이지 이동)는 끊는다. 웹소켓은 route()가 잡지 않으므로 따로 막는다 --
    핸들러가 connect_to_server()를 부르지 않으면 실제 서버로 연결되지 않는다.

    **한계: route()는 리다이렉트의 첫 요청만 본다.** 내부 origin이 302로 외부
    주소를 돌려주면 그 다음 요청은 막지 못한다(리뷰 실측: fetch·img·iframe·
    window.open·goto 모두 도달). route.fetch(max_redirects=0)로 바꿔도
    top-level 이동과 popup은 여전히 샜다. 그래서 (1) 앱에 열린 리다이렉트가
    없다는 전제(Starlette slash·StaticFiles 리다이렉트는 내부 origin으로만
    간다)에 기대고, (2) 그런 요청을 `escaped`에 기록해 렌더를 실패시키며,
    (3) init script의 origin 검사로 그 문서에는 데이터를 넣지 않는다."""

    async def guard(route) -> None:
        url = route.request.url
        if _same_origin(url, origin):
            await route.continue_()
        else:
            blocked.append(url)
            await route.abort("blockedbyclient")

    async def refuse_ws(ws) -> None:
        blocked.append(ws.url)
        await ws.close()

    def watch(request) -> None:
        if request.redirected_from is not None and not _same_origin(request.url, origin):
            if escaped is not None:
                escaped.append(request.url)

    await context.route("**/*", guard)
    await context.route_web_socket(re.compile(".*"), refuse_ws)
    context.on("request", watch)


def report_data_script(payload: dict, origin: str) -> str:
    """init script 본문. 내부 origin 문서에서만 __REPORT_DATA__를 세운다."""
    return (f"if (window.location.origin === {json.dumps(origin)}) "
            f"window.__REPORT_DATA__ = {json.dumps(payload, ensure_ascii=False)};")


def _fail_if_escaped(escaped: list[str]) -> None:
    if escaped:
        log.error("PDF: 리다이렉트로 내부 origin 밖 요청 발생 %s", escaped[:5])
        raise RuntimeError("리포트 페이지가 내부 origin 밖으로 리다이렉트했다")


async def render_report_pdf(payload: dict, origin: str, timeout_s: float) -> bytes:
    """origin은 resolve_internal_origin()의 결과(정규형)여야 한다.
    전체 시간 상한은 호출자가 asyncio.wait_for로 건다. 여기서는 개별 대기가
    그 상한을 넘지 않게 기본 timeout을 맞춰 둘 뿐이다."""
    from playwright.async_api import async_playwright

    timeout_ms = timeout_s * 1000
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as exc:                        # noqa: BLE001
            raise BrowserLaunchError(
                "chromium 실행 파일을 찾을 수 없다. `uv run playwright install chromium` "
                "후 다시 시도할 것.") from exc
        blocked: list[str] = []
        escaped: list[str] = []
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900}, service_workers="block")
            context.set_default_timeout(timeout_ms)
            await _restrict_to_origin(context, origin, blocked, escaped)
            page = await context.new_page()
            # add_init_script는 CDP의 Page.addScriptToEvaluateOnNewDocument로
            # 전달된다 -- HTML 파서를 거치지 않으므로 payload 안에 "</script>"가
            # 있어도 스크립트가 끊기지 않고, U+2028/2029(ES2019+에서는 문자열
            # 리터럴 안에서 합법)도 안전하다. 이 f-string을 나중에 HTML의
            # 인라인 <script> 태그로 옮기면 이 안전성이 사라지므로 XSS를
            # 새로 만들지 않도록 이스케이프를 다시 검토할 것.
            # init script는 모든 문서(하위 프레임 포함)에서 돈다. route 차단이
            # 뚫리더라도 내부 origin이 아닌 문서에는 데이터를 넣지 않는다.
            await page.add_init_script(report_data_script(payload, origin))
            await page.goto(f"{origin}/report", wait_until="load")
            try:
                await page.wait_for_function("window.__REPORT_READY__ === true",
                                             timeout=timeout_ms)
            except Exception:
                if blocked:
                    # URL 원문은 응답이 아니라 로그에만 남긴다(내부 주소 노출 방지).
                    log.warning("PDF: 내부 origin 밖 요청 %d건 차단, 첫 요청 %s",
                                len(blocked), blocked[0][:200])
                raise
            _fail_if_escaped(escaped)
            pdf = await page.pdf(
                format="A4", print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
            _fail_if_escaped(escaped)           # 인쇄 도중 생긴 리다이렉트도 본다
            return pdf
        finally:
            await browser.close()
