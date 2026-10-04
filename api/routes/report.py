"""POST /api/report -> PDF.

async def 인 것이 중요하다: Playwright가 이 서버 자신에게 /report와 정적
에셋을 요청하므로, 핸들러가 이벤트 루프를 막으면 자기 요청을 받지 못해
교착한다.

자원 상한(K4): 본문 크기는 ReportBodyLimit 미들웨어가 JSON 파싱 전에 끊고(413),
동시 생성 수(429)와 전체 시간(504)은 여기서 건다. 브라우저 하나가 수백 MB를
쓰므로, 상한이 없으면 요청 몇 개로 서버 메모리를 다 쓸 수 있다.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.pdf import (DEFAULT_MAX_BODY_BYTES, PDF_SLOTS, BrowserLaunchError, OriginUnavailable, PdfSettings,
                     render_report_pdf, resolve_internal_origin)
from api.schemas import ReportRequest
from core.config import REPO_ROOT

router = APIRouter()
log = logging.getLogger(__name__)

REPORT_PATH = "/api/report"
_DIST_INDEX = REPO_ROOT / "web" / "dist" / "index.html"


def _settings() -> PdfSettings:
    try:
        return PdfSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"PDF 설정 오류: {exc}") from exc


class ReportBodyLimit:
    """/api/report 요청 본문을 TEAMWEAVER_PDF_MAX_BODY_BYTES로 자른다.

    순수 ASGI 미들웨어라 FastAPI가 본문 전체를 메모리에 모아 JSON으로 파싱하기
    *전에* 끊는다. Content-Length가 있으면 바로 거절하고, 없거나(청크 전송)
    거짓이어도 receive 스트림을 세다가 넘는 순간 413을 던진다. 이 예외는
    Starlette HTTPException이라 FastAPI가 "본문 파싱 오류(400)"로 바꾸지 않고
    그대로 413 응답이 된다(fastapi/routing.py의 `except HTTPException: raise`)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # uvicorn은 scope["path"]에 root_path를 붙여 주고(`--root-path /tw` →
        # "/tw/api/report"), 라우터는 그것을 떼고 매칭한다. 같은 방식으로 떼지
        # 않으면 하위 경로 배포에서 상한이 조용히 꺼진다(리뷰 실측: 413 → 422).
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):]
        if scope["type"] != "http" or path != REPORT_PATH:
            await self.app(scope, receive, send)
            return
        try:
            limit = PdfSettings.from_env().max_body_bytes
        except ValueError:
            # 설정 오류는 라우트가 503으로 알린다. 여기서는 더 엄격한 기본값으로 막는다.
            limit = DEFAULT_MAX_BODY_BYTES
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > limit:
                    await _send_413(send, limit)
                    return

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(status_code=413, detail=_too_large(limit))
            return message

        await self.app(scope, counted, send)


def _too_large(limit: int) -> str:
    return f"요청 본문이 PDF 상한 {limit:,}바이트를 넘는다(TEAMWEAVER_PDF_MAX_BODY_BYTES)."


async def _send_413(send: Send, limit: int) -> None:
    body = json.dumps({"detail": _too_large(limit)}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})


@router.post(REPORT_PATH)
async def report(req: ReportRequest, request: Request) -> Response:
    try:
        import playwright                           # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="playwright가 설치돼 있지 않다. `uv add --dev playwright && "
                   "uv run playwright install chromium` 후 다시 시도할 것.")

    # web/dist가 없으면 마운트도 /report 라우트도 없다(api/main.py) -- 그
    # 상태로 그냥 진행하면 Playwright가 404를 받고 wait_for_function이
    # 타임아웃을 다 태운 뒤에야 불친절한 500이 난다. 원인을 즉시 알 수 있게
    # 여기서 먼저 걸러낸다.
    if not _DIST_INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail="web/dist 없음 -- `cd web && npm run build` 먼저 실행할 것")

    settings = _settings()
    try:
        # request.base_url(= Host 헤더)이 아니라 바인드 주소/설정값만 쓴다.
        origin = resolve_internal_origin(request.scope, settings.origin_override)
    except OriginUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not PDF_SLOTS.try_acquire(settings.max_concurrency):
        raise HTTPException(
            status_code=429, headers={"Retry-After": "10"},
            detail=f"PDF 생성이 이미 {settings.max_concurrency}건 진행 중이다. 잠시 후 다시 시도할 것.")
    try:
        pdf = await asyncio.wait_for(
            render_report_pdf(req.model_dump(), origin, settings.timeout_s),
            timeout=settings.timeout_s)
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:                        # noqa: BLE001
        # 예외 원문(Playwright call log에 내부 origin URL이 들어 있다)은 로그에만 남긴다.
        # asyncio.wait_for의 TimeoutError와 Playwright 내부 대기의 TimeoutError
        # (playwright._impl._errors.TimeoutError, 내장 TimeoutError가 아님)를
        # 같은 504로 묶는다 -- 둘 다 같은 timeout_s 상한에서 나온다.
        if not (isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"):
            log.exception("PDF 생성 실패")
            raise HTTPException(status_code=500,
                                detail=f"PDF 생성 실패({type(exc).__name__}). 서버 로그를 확인할 것.") from exc
        raise HTTPException(
            status_code=504,
            detail=f"PDF 생성이 {settings.timeout_s:g}초 안에 끝나지 않았다"
                   "(TEAMWEAVER_PDF_TIMEOUT_S).") from exc
    finally:
        PDF_SLOTS.release()

    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="teamweaver-plan-{req.plan_label}.pdf"'})
