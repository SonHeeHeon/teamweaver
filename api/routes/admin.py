"""GET /api/admin -- 화면이 관리자 토큰 입력칸을 보여 줄지 판단한다(값은 노출하지 않는다)."""
from fastapi import APIRouter

from api.admin import admin_token_required

router = APIRouter()


@router.get("/api/admin")
def admin_status() -> dict:
    return {"token_required": admin_token_required()}
