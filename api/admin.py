"""관리자 로그인(K14 → 2026-10-18 앱 전체 로그인): 아이디·비밀번호로 들어가는 관리자 세션.

2026-10-18 사용자 지시("관리자 로그인 화면 만들자 … admin,admin1234", 범위: 앱 전체): 로그인하지 않으면
`/api/*`를 하나도 쓸 수 없다(상태 확인·로그인·로그아웃만 열림, `AppLoginGate`). 직원별 계정·SSO는 배포 환경이
정해진 뒤의 일이다(관리자 한 명 모델).

- 아이디: TEAMWEAVER_ADMIN_ID(기본 admin).
- 비밀번호: TEAMWEAVER_ADMIN_PASSWORD_HASH(scrypt, scripts/hash_admin_password.py로 만든다) →
  TEAMWEAVER_ADMIN_PASSWORD(평문, 로컬 개발용) → 둘 다 없으면 **기본 admin1234**(약한 값 -- 상태 응답의
  default_credentials와 서버 로그가 알린다. 다른 사람이 접속하는 주소로 띄우면 반드시 바꾼다).
- TEAMWEAVER_LOGIN=off: 비밀번호를 따로 설정하지 않았을 때 로그인을 끈다(시험·개발용, 예전 "열린 시제품"과 같다).
  비밀번호를 설정하면 off여도 로그인이 켜진다.
- 세션: 쿠키 tw_admin = "<만료 epoch>.<HMAC(세션 키, 만료·비밀번호 지문)>". HttpOnly·SameSite=Strict.
  비밀번호를 바꾸면 지문이 달라져 기존 세션이 모두 무효다. 세션 키는 데이터 폴더 session_secret.
- 로그아웃은 서버 쪽 세대 번호(session_epoch 파일)를 올려 그때까지 발급한 세션을 모두 무효화한다
  (관리자가 한 명이라 "모든 세션"이 곧 그 관리자다). 쿠키 사본을 들고 있어도 로그아웃 뒤엔 못 쓴다.
- 무차별 대입: 클라이언트 주소별 연속 실패 5회면 60초 잠금. 주소마다 동시 시도는 하나만 받는다
  (scrypt 계산 중에 몰아 보낸 요청이 모두 잠금 검사를 통과하던 문제). 잠금은 프로세스마다 따로라
  worker가 N개면 실제 한도는 N×5회다. 같은 주소를 쓰는 사용자(프록시 설정 누락 등)는 잠금을 공유한다.
- 운영 메모: 세션을 모두 끊으려면 session_secret과 session_epoch를 함께 지운다(epoch만 지우면
  0으로 돌아가 첫 로그아웃 전 세션이 만료 전까지 다시 유효해진다).
- 프록시 뒤: uvicorn `--forwarded-allow-ips <프록시 주소>`를 줘야 클라이언트 주소가 맞고, TLS를 프록시가
  받으면 TEAMWEAVER_COOKIE_SECURE=1로 쿠키에 Secure를 강제한다.
- TEAMWEAVER_ADMIN_TOKEN(X-Admin-Token 헤더)은 스크립트용으로 계속 받는다.
"""
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request

from api.storage import data_dir, load_or_create_secret

ADMIN_TOKEN_ENV = "TEAMWEAVER_ADMIN_TOKEN"
ADMIN_ID_ENV = "TEAMWEAVER_ADMIN_ID"
PASSWORD_ENV = "TEAMWEAVER_ADMIN_PASSWORD"
PASSWORD_HASH_ENV = "TEAMWEAVER_ADMIN_PASSWORD_HASH"
LOGIN_ENV = "TEAMWEAVER_LOGIN"
DEFAULT_ID = "admin"
DEFAULT_PASSWORD = "admin1234"         # 사용자 지시(2026-10-18) -- 시연용 기본값
COOKIE = "tw_admin"
SESSION_SECONDS = 8 * 3600
MAX_FAILURES = 5
LOCK_SECONDS = 60
log = logging.getLogger(__name__)


# --- 비밀번호 -------------------------------------------------------------

def hash_password(password: str, *, n: int = 2 ** 14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def _check_hash(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest)))
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except (ValueError, TypeError):
        log.error("%s 형식이 올바르지 않다", PASSWORD_HASH_ENV)
        return False


def _explicit_password() -> tuple[str, str] | None:
    h = os.environ.get(PASSWORD_HASH_ENV, "").strip()
    if h:
        return "hash", h
    p = os.environ.get(PASSWORD_ENV, "")
    return ("plain", p) if p else None


def _login_off() -> bool:
    return os.environ.get(LOGIN_ENV, "").strip().lower() in ("off", "0", "false", "no")


def _configured_password() -> tuple[str, str] | None:
    """("hash"|"plain", 값). 로그인이 꺼져 있으면 None. 스크립트 토큰만 설정한 서버(운영자가 비밀값을 따로 둔 배포)에는
    약한 기본 비밀번호를 열지 않는다(폴백 리뷰 M2) -- 그 서버는 예전처럼 토큰으로만 관리자 동작을 지킨다."""
    explicit = _explicit_password()
    if explicit is not None:
        return explicit
    if _login_off() or admin_token_required():
        return None
    return ("plain", DEFAULT_PASSWORD)


def admin_id() -> str:
    return os.environ.get(ADMIN_ID_ENV, "").strip() or DEFAULT_ID


def default_credentials() -> bool:
    """기본 아이디·비밀번호(admin/admin1234)로 열려 있는가 -- 화면이 바꾸라고 안내한다."""
    return password_login_enabled() and _explicit_password() is None


def password_login_enabled() -> bool:
    return _configured_password() is not None


def admin_token_required() -> bool:
    return bool(os.environ.get(ADMIN_TOKEN_ENV, "").strip())


def admin_protected() -> bool:
    return password_login_enabled() or admin_token_required()


def verify_credentials(username: str, password: str) -> bool:
    """아이디와 비밀번호를 둘 다 확인한다(어느 쪽이 틀렸는지 알려 주지 않는다)."""
    id_ok = hmac.compare_digest(username.strip().encode("utf-8"), admin_id().encode("utf-8"))
    return verify_password(password) and id_ok


def verify_password(password: str) -> bool:
    cfg = _configured_password()
    if cfg is None:
        return False
    kind, value = cfg
    if kind == "hash":
        return _check_hash(password, value)
    return hmac.compare_digest(password.encode("utf-8"), value.encode("utf-8"))


# --- 세션 ---------------------------------------------------------------

_secret_cache: dict[str, bytes] = {}


def _session_key() -> bytes:
    path = data_dir() / "session_secret"
    key = str(path)
    if key in _secret_cache:
        return _secret_cache[key]
    try:
        value = load_or_create_secret(path)          # 여러 워커가 동시에 불러도 같은 키
    except (OSError, ValueError) as exc:
        log.warning("세션 키를 %s에 고정하지 못해 임시 키를 쓴다(재기동하면 로그아웃된다): %s", path, exc)
        value = secrets.token_bytes(32)
    _secret_cache[key] = value
    return value


def _fingerprint() -> str:
    cfg = _configured_password()
    raw = f"{admin_id()}:{cfg[0]}:{cfg[1]}" if cfg else ""      # 아이디·비밀번호를 바꾸면 기존 세션 무효
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _epoch_path():
    return data_dir() / "session_epoch"


def session_epoch() -> int:
    try:
        return int(_epoch_path().read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        return 0


def revoke_sessions() -> None:
    """로그아웃: 세대 번호를 올려 지금까지 발급한 세션을 모두 무효화한다."""
    from api.storage import atomic_write
    atomic_write(_epoch_path(), str(session_epoch() + 1).encode("ascii"))


def _sign(expires: int) -> str:
    msg = f"{expires}:{_fingerprint()}:{session_epoch()}".encode("ascii")
    return hmac.new(_session_key(), msg, hashlib.sha256).hexdigest()


def issue_session(now: float | None = None) -> tuple[str, int]:
    expires = int((now if now is not None else time.time()) + SESSION_SECONDS)
    return f"{expires}.{_sign(expires)}", expires


def session_expiry(cookie: str | None, now: float | None = None) -> int | None:
    """유효한 세션이면 만료 epoch, 아니면 None."""
    if not cookie or "." not in cookie or not password_login_enabled():
        return None
    exp_s, sig = cookie.split(".", 1)
    # 쿠키 값은 latin-1로 풀려 들어온다 -- ASCII가 아니면 compare_digest(str, str)가 TypeError, "²"는 isdigit이지만 int()가
    # ValueError라 둘 다 500이었다(claude-a 점검 2026-10-12 (4)). 위조 쿠키일 뿐이다.
    if not (exp_s.isascii() and exp_s.isdigit() and sig.isascii()):
        return None
    expires = int(exp_s)
    if expires <= (now if now is not None else time.time()):
        return None
    return expires if hmac.compare_digest(sig.encode("ascii"), _sign(expires).encode("ascii")) else None


# --- 무차별 대입 잠금 -----------------------------------------------------

class LoginThrottle:
    MAX_TRACKED = 10_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}      # 주소 -> (연속 실패, 잠금 해제 시각)
        self._inflight: set[str] = set()

    def locked_for(self, who: str, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        with self._lock:
            _, until = self._state.get(who, (0, 0.0))
            return max(0.0, until - now)

    def begin(self, who: str, now: float | None = None) -> float | None:
        """시도를 예약한다. 잠겼으면 남은 초, 같은 주소의 다른 시도가 진행 중이면 1.0, 통과면 None.
        예약은 잠금 확인과 같은 락 안에서 해서, 병렬 요청이 함께 통과하지 못한다."""
        now = now if now is not None else time.time()
        with self._lock:
            _, until = self._state.get(who, (0, 0.0))
            if until > now:
                return until - now
            if who in self._inflight:
                return 1.0
            self._inflight.add(who)
            return None

    def end(self, who: str) -> None:
        with self._lock:
            self._inflight.discard(who)

    def failed(self, who: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            count, _ = self._state.get(who, (0, 0.0))
            count += 1
            until = now + LOCK_SECONDS if count >= MAX_FAILURES else 0.0
            self._state[who] = (0 if until else count, until)
            if len(self._state) > self.MAX_TRACKED:          # 오래된 기록 정리(메모리 상한)
                for k in [k for k, (_, u) in self._state.items() if u <= now][:len(self._state) // 2]:
                    self._state.pop(k, None)

    def succeeded(self, who: str) -> None:
        with self._lock:
            self._state.pop(who, None)


THROTTLE = LoginThrottle()


# --- 의존성 -------------------------------------------------------------

def cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https" or os.environ.get("TEAMWEAVER_COOKIE_SECURE", "") == "1"


def warn_if_unprotected() -> None:
    """서버 시작 때 부른다: 로그인이 꺼졌거나 기본 비밀번호로 열려 있으면 로그에 남긴다."""
    if not admin_protected():
        log.warning("로그인이 꺼져 있어(%s=off) 모든 화면·설정·데이터 전환이 누구에게나 열려 있다", LOGIN_ENV)
    elif default_credentials():
        log.warning("기본 관리자 계정(%s/%s)으로 로그인을 받는다 -- 다른 사람이 접속하는 주소면 %s로 바꿀 것",
                    DEFAULT_ID, "admin1234", PASSWORD_HASH_ENV)


def has_access(request) -> bool:
    """세션 쿠키나 스크립트 토큰이 맞는가(로그인이 꺼져 있으면 항상 참)."""
    if not admin_protected():
        return True
    token = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
    given = request.headers.get("x-admin-token", "")
    if token and given and hmac.compare_digest(given.encode(), token.encode()):
        return True
    return session_expiry(request.cookies.get(COOKIE)) is not None


# 로그인 없이 부를 수 있는 API(로그인 화면이 쓰는 것뿐)
OPEN_PATHS = frozenset({("GET", "/api/admin"), ("POST", "/api/admin/login"), ("POST", "/api/admin/logout")})


class AppLoginGate:
    """앱 전체 로그인(2026-10-18): 로그인하지 않으면 OPEN_PATHS 밖의 `/api/*`는 401이다. 화면(SPA 정적 파일)은
    막지 않는다 -- 로그인 화면이 그 안에 있다. PDF용 내부 브라우저는 `/report` 정적 페이지만 열고 데이터는 주입받으므로
    (api/pdf.py) API를 부르지 않는다. 순수 ASGI라 SSE 응답을 감싸지 않는다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 하위 경로 배포(uvicorn --root-path /tw)면 scope["path"]가 "/tw/api/…"다 -- 라우터와 같은 규칙으로 뗀 경로를 본다
        # (폴백 리뷰 M1: 원시 경로를 보면 관문이 통째로 꺼졌다). 본문 상한(api/routes/report)과 같은 함수.
        from api.routes.report import _route_path
        path = _route_path(scope) if scope["type"] == "http" else ""
        if scope["type"] == "http" and path.startswith("/api/") \
                and scope["method"] != "OPTIONS" and (scope["method"], path) not in OPEN_PATHS:
            from starlette.requests import Request
            from starlette.responses import JSONResponse
            # 토큰만 설정한 서버(로그인 꺼짐)는 예전처럼 관리자 동작만 require_admin이 지킨다.
            if password_login_enabled() and not has_access(Request(scope)):
                await JSONResponse({"detail": "로그인이 필요하다.", "login_required": True},
                                   status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def require_admin(request: Request) -> None:
    if not admin_protected():
        return
    token = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
    given = request.headers.get("x-admin-token", "")
    if token and given and hmac.compare_digest(given.encode(), token.encode()):
        return
    if session_expiry(request.cookies.get(COOKIE)) is not None:
        return
    log.warning("관리자 인증 실패: %s %s", request.method, request.url.path)
    raise HTTPException(status_code=401, detail="관리자 로그인이 필요하다.")
