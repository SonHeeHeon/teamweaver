"""POST /api/report -> PDF.

async def 인 것이 중요하다: Playwright가 이 서버 자신에게 /report와 정적
에셋을 요청하므로, 핸들러가 이벤트 루프를 막으면 자기 요청을 받지 못해
교착한다.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.pdf import render_report_pdf
from api.schemas import ReportRequest

router = APIRouter()


@router.post("/api/report")
async def report(req: ReportRequest, request: Request) -> Response:
    try:
        import playwright                           # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="playwright가 설치돼 있지 않다. `uv add --dev playwright && "
                   "uv run playwright install chromium` 후 다시 시도할 것.")

    base_url = str(request.base_url).rstrip("/")
    try:
        pdf = await render_report_pdf(req.model_dump(), base_url)
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {exc}") from exc

    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="teamweaver-plan-{req.plan_label}.pdf"'})
