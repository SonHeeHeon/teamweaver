"""관리자 동작(설정 저장·데이터셋 전환)의 최소 접근 통제.

시스템 전체 인증은 아직 없다(로드맵 6단계, 사용자 결정 사항). 그래도 실데이터를 올리고
조직 규칙을 바꾸는 동작은 서버 전체에 영향을 주므로, 환경변수 TEAMWEAVER_ADMIN_TOKEN이
있으면 그 값을 X-Admin-Token 헤더로 요구한다. 없으면(로컬 시제품 기본) 열어 둔다 --
화면은 GET /api/admin으로 토큰이 필요한지 알아내 입력칸을 보여 준다.
"""
import hmac
import logging
import os

from fastapi import HTTPException, Request

ADMIN_TOKEN_ENV = "TEAMWEAVER_ADMIN_TOKEN"
log = logging.getLogger(__name__)


def admin_token_required() -> bool:
    return bool(os.environ.get(ADMIN_TOKEN_ENV, "").strip())


def require_admin(request: Request) -> None:
    expected = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
    if not expected:
        return
    given = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        log.warning("관리자 토큰 불일치: %s %s", request.method, request.url.path)
        raise HTTPException(status_code=401, detail="관리자 토큰이 필요하다(X-Admin-Token).")
