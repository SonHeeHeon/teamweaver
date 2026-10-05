"""K14: 관리자 로그인 -- 비밀번호 하나, HttpOnly 세션 쿠키, 잠금, 보호 엔드포인트."""
import time

import pytest

from api import admin
from api.settings import PlacementSettings

PUT = lambda: {"settings": PlacementSettings().model_dump(), "based_on": None}   # noqa: E731


@pytest.fixture
def pw(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_PASSWORD_HASH", admin.hash_password("correct horse 9"))
    return "correct horse 9"


def test_without_password_admin_actions_stay_open_and_status_says_so(client):
    st = client.get("/api/admin").json()
    assert st["login_required"] is False and st["protected"] is False
    assert client.put("/api/settings", json=PUT()).status_code == 200


def test_login_sets_a_strict_httponly_cookie_and_unlocks_admin_actions(client, pw):
    assert client.put("/api/settings", json=PUT()).status_code == 401
    assert client.get("/api/admin").json() == {"login_required": True, "protected": True,
                                               "logged_in": False, "expires_at": None,
                                               "token_required": False}
    res = client.post("/api/admin/login", json={"password": pw})
    assert res.status_code == 200
    cookie = res.headers["set-cookie"]
    assert "tw_admin=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Max-Age=28800" in cookie
    assert client.get("/api/admin").json()["logged_in"] is True
    assert client.put("/api/settings", json=PUT()).status_code == 200
    assert client.post("/api/datasets/reset", json={}).status_code == 200


def test_wrong_password_is_401_and_does_not_set_cookie(client, pw):
    res = client.post("/api/admin/login", json={"password": "nope"})
    assert res.status_code == 401 and "set-cookie" not in res.headers


def test_logout_clears_the_cookie(client, pw):
    client.post("/api/admin/login", json={"password": pw})
    client.post("/api/admin/logout")
    assert client.get("/api/admin").json()["logged_in"] is False
    assert client.put("/api/settings", json=PUT()).status_code == 401


def test_five_failures_lock_the_client_for_a_while(client, pw):
    for _ in range(admin.MAX_FAILURES):
        assert client.post("/api/admin/login", json={"password": "x"}).status_code == 401
    locked = client.post("/api/admin/login", json={"password": pw})
    assert locked.status_code == 429 and "Retry-After" in locked.headers
    assert "너무 잦다" in locked.json()["detail"]


def test_forged_expired_or_old_password_sessions_are_rejected(pw, monkeypatch):
    value, expires = admin.issue_session()
    assert admin.session_expiry(value) == expires
    assert admin.session_expiry(f"{expires + 3600}.{value.split('.')[1]}") is None   # 만료 위조
    assert admin.session_expiry(value, now=expires + 1) is None                        # 만료
    assert admin.session_expiry("garbage") is None
    monkeypatch.setenv("TEAMWEAVER_ADMIN_PASSWORD_HASH", admin.hash_password("new password 1"))
    assert admin.session_expiry(value) is None                                         # 비밀번호 변경


def test_session_survives_restart_with_the_same_key(client, pw):
    from fastapi.testclient import TestClient

    from api.main import app
    client.post("/api/admin/login", json={"password": pw})
    cookie = client.cookies.get("tw_admin")
    admin._secret_cache.clear()                          # 재기동 흉내
    with TestClient(app) as again:
        again.cookies.set("tw_admin", cookie)
        assert again.get("/api/admin").json()["logged_in"] is True


def test_plain_password_for_local_dev(client, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_PASSWORD", "devpass")
    assert client.post("/api/admin/login", json={"password": "devpass"}).status_code == 200


def test_script_token_still_works(client, monkeypatch, pw):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "s3cret")
    assert client.put("/api/settings", json=PUT(), headers={"X-Admin-Token": "s3cret"}).status_code == 200
    assert client.put("/api/settings", json=PUT(), headers={"X-Admin-Token": "bad"}).status_code == 401


def test_login_without_configured_password_is_409(client):
    assert client.post("/api/admin/login", json={"password": "x"}).status_code == 409


def test_hash_format_and_salting():
    a, b = admin.hash_password("same"), admin.hash_password("same")
    assert a != b and a.startswith("scrypt$")
    assert admin._check_hash("same", a) and not admin._check_hash("other", a)
    assert admin._check_hash("x", "not-a-hash") is False


def test_cors_allows_credentials_only_for_the_dev_origins(client):
    ok = client.options("/api/admin/login", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-credentials") == "true"
    evil = client.options("/api/admin/login", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert evil.headers.get("access-control-allow-origin") is None


def test_throttle_unlocks_after_the_lock_window():
    t = admin.LoginThrottle()
    now = time.time()
    for _ in range(admin.MAX_FAILURES):
        t.failed("1.2.3.4", now=now)
    assert t.locked_for("1.2.3.4", now=now) > 0
    assert t.locked_for("1.2.3.4", now=now + admin.LOCK_SECONDS + 1) == 0


# --- 리뷰 반영(1라운드) ----------------------------------------------------

def test_parallel_guesses_cannot_bypass_the_lock(client, pw, monkeypatch):
    """같은 주소의 병렬 시도는 하나만 비밀번호 검증까지 간다(나머지는 429)."""
    from concurrent.futures import ThreadPoolExecutor
    calls = []
    real = admin.verify_password

    def slow_verify(p):
        calls.append(1)
        time.sleep(0.05)
        return real(p)

    monkeypatch.setattr("api.routes.admin.verify_password", slow_verify)
    with ThreadPoolExecutor(20) as ex:
        codes = list(ex.map(lambda _: client.post("/api/admin/login",
                                                  json={"password": "x"}).status_code, range(20)))
    assert len(calls) <= admin.MAX_FAILURES, f"잠금 전 검증 {len(calls)}회"
    assert codes.count(429) >= 20 - admin.MAX_FAILURES


def test_logout_revokes_copied_session(client, pw):
    """쿠키 사본이 있어도 로그아웃 뒤에는 못 쓴다(서버 세대 번호)."""
    from fastapi.testclient import TestClient

    from api.main import app
    client.post("/api/admin/login", json={"password": pw})
    stolen = client.cookies.get("tw_admin")
    client.post("/api/admin/logout")
    with TestClient(app) as other:
        other.cookies.set("tw_admin", stolen)
        assert other.put("/api/settings", json=PUT()).status_code == 401


def test_logout_without_session_does_not_revoke_others(client, pw):
    from fastapi.testclient import TestClient

    from api.main import app
    client.post("/api/admin/login", json={"password": pw})
    with TestClient(app) as anon:
        anon.post("/api/admin/logout")                      # 로그인 안 한 사람의 로그아웃
    assert client.put("/api/settings", json=PUT()).status_code == 200


def test_cookie_secure_can_be_forced_behind_a_tls_proxy(client, pw, monkeypatch):
    assert "Secure" not in client.post("/api/admin/login", json={"password": pw}).headers["set-cookie"]
    monkeypatch.setenv("TEAMWEAVER_COOKIE_SECURE", "1")
    assert "Secure" in client.post("/api/admin/login", json={"password": pw}).headers["set-cookie"]


@pytest.mark.parametrize("bad", ["not-a-hash", "scrypt$3$8$1$00$00", "scrypt$x$8$1$zz$00", "md5$1$2$3$4$5"])
def test_malformed_hash_setting_keeps_admin_locked(client, monkeypatch, bad):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_PASSWORD_HASH", bad)
    assert client.post("/api/admin/login", json={"password": "anything"}).status_code == 401
    assert client.put("/api/settings", json=PUT()).status_code == 401


def test_unprotected_server_logs_a_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="api.admin"):
        admin.warn_if_unprotected()
    assert "누구에게나 열려" in caplog.text


def test_wrong_token_with_valid_session_still_passes(client, pw, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "s3cret")
    client.post("/api/admin/login", json={"password": pw})
    assert client.put("/api/settings", json=PUT(), headers={"X-Admin-Token": "bad"}).status_code == 200
