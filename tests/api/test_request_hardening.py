"""이상한 요청이 500이 아니라 정상 거절이 되는가(claude-a 점검 2026-10-12 (4)·(5)·(6))."""
import pytest

from api import admin
from api.plan_token import sign_plan, verify_plan
from core.optimize.milp import MilpParams

ENTRIES = [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}]


def test_non_ascii_plan_tokens_are_simply_wrong_not_a_crash():
    # hmac.compare_digest(str, str)는 ASCII가 아닌 글자가 있으면 TypeError -- 서명 검사가 500을 냈다
    good = sign_plan("v" * 64, "A", ENTRIES, {}, MilpParams())
    assert verify_plan(good, "v" * 64, "A", ENTRIES, {}, MilpParams())
    assert verify_plan("가" * 64, "v" * 64, "A", ENTRIES, {}, MilpParams()) is False
    assert verify_plan(good[:-1] + "é", "v" * 64, "A", ENTRIES, {}, MilpParams()) is False


def test_non_ascii_session_cookies_are_rejected_not_a_crash(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_PASSWORD_HASH", admin.hash_password("correct horse 9"))
    value, expires = admin.issue_session()
    assert admin.session_expiry(value) == expires
    assert admin.session_expiry(f"{expires}.{'é' * 64}") is None          # 쿠키 값은 latin-1로 풀려 들어온다
    assert admin.session_expiry(f"{'²' * 3}.{value.split('.')[1]}") is None   # isdigit은 참이지만 int()는 실패


def test_plan_edits_with_a_non_ascii_token_are_422(client):
    meta = client.get("/api/meta").json()
    body = {"plan_label": "A", "base_entries": ENTRIES, "dataset_version": meta["dataset_version"],
            "swaps": [], "expected_revision": 0}
    res = client.put("/api/plans/edits/" + "가" * 64, json=body)
    assert res.status_code == 422


def test_report_with_a_non_ascii_token_is_422(client):
    meta = client.get("/api/meta").json()
    body = {"plan_label": "A", "entries": ENTRIES, "objective": 0.0, "fulfillment": 0.0, "optimization_ratio": 0.0,
            "unfilled": [], "dataset_version": meta["dataset_version"], "plan_token": "가" * 64}
    from api.routes.report import ReportRequest, _plan_provenance
    with pytest.raises(Exception) as err:
        _plan_provenance(ReportRequest(**body), meta["dataset_version"])
    assert getattr(err.value, "status_code", None) == 422


@pytest.mark.parametrize("label,expected", [
    ("A", "teamweaver-plan-A.pdf"), ("K12", "teamweaver-plan-K12.pdf"),
    ('안 "A"', "teamweaver-plan-A.pdf"), ("가나다", "teamweaver-plan-plan.pdf"),
    ("ABCDEFGHIJK", "teamweaver-plan-ABCDEFGH.pdf"), ('x"; filename=evil.exe', "teamweaver-plan-xfilenam.pdf"),
])
def test_pdf_file_name_keeps_only_safe_label_characters(label, expected):
    # 서명 없는 요청의 한글·따옴표 라벨이면 PDF를 다 만든 뒤 헤더 인코딩에서 500이 났다(latin-1 밖 글자)
    from api.routes.report import pdf_filename
    assert pdf_filename(label) == expected


def test_json_bodies_over_the_limit_are_413_on_every_api_write(client, monkeypatch):
    # 예전엔 /api/report에만 본문 상한이 있었다(점검 2026-10-12 (6))
    monkeypatch.setenv("TEAMWEAVER_MAX_JSON_BODY_BYTES", "2000")
    big = {"entries": [{"person_id": f"p{i}", "project_id": "j0", "alloc": 0.5} for i in range(200)],
           "swap": {"out_person_id": "p0", "in_person_id": "p1", "project_id": "j0"}}
    for path in ("/api/whatif", "/api/plans/apply-swap", "/api/staffing/simulate"):
        res = client.post(path, json=big)
        assert res.status_code == 413, path
        assert "TEAMWEAVER_MAX_JSON_BODY_BYTES" in res.json()["detail"]
    assert client.get("/api/meta").status_code == 200                       # 읽기 요청은 그대로


def test_chunked_json_body_is_cut_while_streaming(monkeypatch):
    """Content-Length 없이(청크) 보내도 받은 만큼 세다가 넘는 순간 413."""
    import asyncio
    from fastapi import HTTPException
    from api.routes.report import ReportBodyLimit

    monkeypatch.setenv("TEAMWEAVER_MAX_JSON_BODY_BYTES", "100")
    chunks = [{"type": "http.request", "body": b"x" * 60, "more_body": True},
              {"type": "http.request", "body": b"x" * 60, "more_body": False}]

    async def inner(scope, receive, send):
        while (await receive()).get("more_body"):
            pass

    async def receive():
        return chunks.pop(0)

    async def send(message):
        raise AssertionError("응답을 보내기 전에 끊겨야 한다")

    scope = {"type": "http", "path": "/api/whatif", "method": "POST", "headers": []}
    with pytest.raises(HTTPException) as err:
        asyncio.run(ReportBodyLimit(inner)(scope, receive, send))
    assert err.value.status_code == 413


def test_dataset_upload_keeps_its_own_limit(monkeypatch):
    """업로드(zip)는 자체 상한을 쓴다 -- JSON 상한으로 막지 않는다."""
    import asyncio
    from api.routes.report import ReportBodyLimit

    monkeypatch.setenv("TEAMWEAVER_MAX_JSON_BODY_BYTES", "10")
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])

    scope = {"type": "http", "path": "/api/datasets", "method": "POST", "headers": [(b"content-length", b"5000")]}
    asyncio.run(ReportBodyLimit(inner)(scope, None, None))
    assert seen == ["/api/datasets"]


@pytest.mark.parametrize("path,extra", [
    ("/api/whatif", {"swap": {"out_person_id": "p0", "in_person_id": "p1", "project_id": "j0"}}),
    ("/api/plans/apply-alloc", {"change": {"kind": "alloc", "person_id": "p0", "project_id": "j0", "alloc": [0.5] * 6}}),
])
def test_entry_lists_have_a_count_limit(client, path, extra, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_MAX_JSON_BODY_BYTES", str(50 * 1024 * 1024))    # 개수 상한만 보게 본문 상한을 연다
    entries = [{"person_id": "p0", "project_id": "j0", "alloc": 0.5}] * 5001
    res = client.post(path, json={"entries": entries, **extra})
    assert res.status_code == 422
    assert any(e.get("type") == "too_long" for e in res.json()["detail"])
