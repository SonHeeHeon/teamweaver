"""POST /api/report -> PDF.

async def 인 것이 중요하다: Playwright가 이 서버 자신에게 /report와 정적
에셋을 요청하므로, 핸들러가 이벤트 루프를 막으면 자기 요청을 받지 못해
교착한다.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.pdf import BrowserLaunchError, render_report_pdf
from api.schemas import ReportRequest
from core.config import REPO_ROOT

router = APIRouter()

_DIST_INDEX = REPO_ROOT / "web" / "dist" / "index.html"


@router.post("/api/report")
async def report(req: ReportRequest, request: Request) -> Response:
    try:
        import playwright                           # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="playwright가 설치돼 있지 않다. `uv add --dev playwright && "
                   "uv run playwright install chromium` 후 다시 시도할 것.")

    # web/dist가 없으면 마운트도 /report 라우트도 없다(api/main.py) -- 그
    # 상태로 그냥 진행하면 Playwright가 404를 받고 wait_for_function이 30초
    # 타임아웃을 다 태운 뒤에야 "Timeout 30000ms exceeded"라는 불친절한 500이
    # 난다. 원인을 즉시 알 수 있게 여기서 먼저 걸러낸다.
    if not _DIST_INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail="web/dist 없음 -- `cd web && npm run build` 먼저 실행할 것")

    base_url = str(request.base_url).rstrip("/")
    try:
        pdf = await render_report_pdf(req.model_dump(), base_url)
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {exc}") from exc

    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="teamweaver-plan-{req.plan_label}.pdf"'})
