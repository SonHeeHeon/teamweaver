"""관리자 로그인(K14): 비밀번호 하나로 들어가는 관리자 세션.

설정 저장·데이터셋 업로드·되돌리기 같은 서버 전체에 영향을 주는 동작만 지킨다. 계산·교체
검토·PDF는 지금처럼 누구나 쓴다. 직원별 계정·SSO는 배포 환경이 정해진 뒤의 일이다.

- 비밀번호: TEAMWEAVER_ADMIN_PASSWORD_HASH(scrypt, scripts/hash_admin_password.py로 만든다) 또는
  TEAMWEAVER_ADMIN_PASSWORD(평문, 로컬 개발용). 둘 다 없고 TEAMWEAVER_ADMIN_TOKEN도 없으면
  관리자 동작을 열어 둔다(로컬 시제품) -- 화면이 경고를 띄운다.
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
PASSWORD_ENV = "TEAMWEAVER_ADMIN_PASSWORD"
PASSWORD_HASH_ENV = "TEAMWEAVER_ADMIN_PASSWORD_HASH"
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


def _configured_password() -> tuple[str, str] | None:
    """("hash"|"plain", 값). 설정이 없으면 None."""
    h = os.environ.get(PASSWORD_HASH_ENV, "").strip()
    if h:
        return "hash", h
    p = os.environ.get(PASSWORD_ENV, "")
    return ("plain", p) if p else None


def password_login_enabled() -> bool:
    return _configured_password() is not None


def admin_token_required() -> bool:
    return bool(os.environ.get(ADMIN_TOKEN_ENV, "").strip())


def admin_protected() -> bool:
    return password_login_enabled() or admin_token_required()


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
    raw = f"{cfg[0]}:{cfg[1]}" if cfg else ""
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
    if not exp_s.isdigit():
        return None
    expires = int(exp_s)
    if expires <= (now if now is not None else time.time()):
        return None
    return expires if hmac.compare_digest(sig, _sign(expires)) else None


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
    """서버 시작 때 부른다: 비밀번호·토큰이 모두 없으면 관리자 동작이 공개라는 사실을 로그에 남긴다."""
    if not admin_protected():
        log.warning("관리자 비밀번호(%s)·토큰(%s)이 없어 배치 설정·데이터 전환이 누구에게나 열려 있다",
                    PASSWORD_HASH_ENV, ADMIN_TOKEN_ENV)


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
