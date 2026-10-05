"""관리자 로그인 API(K14). 설계: api/admin.py docstring."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.admin import (COOKIE, SESSION_SECONDS, THROTTLE, admin_protected, admin_token_required,
                       cookie_secure, issue_session, password_login_enabled, revoke_sessions,
                       session_expiry, verify_password)

router = APIRouter()


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/admin")
def admin_status(request: Request) -> dict:
    """화면이 로그인 화면·경고를 정하는 데 쓴다(비밀번호·토큰 값은 내보내지 않는다)."""
    expires = session_expiry(request.cookies.get(COOKIE))
    return {"login_required": password_login_enabled(),
            "protected": admin_protected(),
            "logged_in": expires is not None,
            "expires_at": expires,
            # 하위 호환(K9 화면): 토큰만 설정된 서버
            "token_required": admin_token_required() and not password_login_enabled()}


@router.post("/api/admin/login")
def login(body: LoginIn, request: Request) -> JSONResponse:
    if not password_login_enabled():
        raise HTTPException(status_code=409, detail="관리자 비밀번호가 설정되지 않은 서버다.")
    who = _client(request)
    wait = THROTTLE.begin(who)
    if wait is not None:
        raise HTTPException(status_code=429, headers={"Retry-After": str(int(wait) + 1)},
                            detail=f"로그인 시도가 너무 잦다(비밀번호를 여러 번 틀렸거나 처리 중). "
                                   f"{int(wait) + 1}초 뒤 다시 시도할 것.")
    try:
        ok = verify_password(body.password)
        if not ok:
            THROTTLE.failed(who)
            raise HTTPException(status_code=401, detail="비밀번호가 맞지 않는다.")
        THROTTLE.succeeded(who)
    finally:
        THROTTLE.end(who)
    value, expires = issue_session()
    res = JSONResponse({"logged_in": True, "expires_at": expires})
    res.set_cookie(COOKIE, value, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                   secure=cookie_secure(request), path="/")
    return res


@router.post("/api/admin/logout")
def logout(request: Request) -> JSONResponse:
    # 로그인한 세션에서만 서버 세대를 올린다(아무나 남의 세션을 끊지 못하게).
    if session_expiry(request.cookies.get(COOKIE)) is not None:
        revoke_sessions()
    res = JSONResponse({"logged_in": False})
    res.delete_cookie(COOKIE, path="/", samesite="strict", httponly=True)
    return res
