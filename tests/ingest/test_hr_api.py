"""인사 시스템 받기(core/ingest/hr_api, 2026-10-19): 설정·쪽 넘김·인증·응답 모양·합치기. 받은 묶음은 CSV 묶음과 같은 데이터가 된다."""
import json
import threading
from pathlib import Path

import httpx
import pytest

from core.ingest import hr_api
from core.ingest.hr_api import HrApiConfig, HrApiError, fetch, merge
from scripts.mock_hr_api import load_tables, make_server, respond

DEMO = Path(__file__).resolve().parents[2] / "demo" / "org-n100"


def _files(root: Path) -> dict[str, str]:
    return {p.name: p.read_text("utf-8-sig") for p in root.iterdir() if p.is_file()}


def _cfg(**kw) -> HrApiConfig:
    return HrApiConfig(base_url="http://hr.test/api", token="t0k", tables=hr_api._tables_of(kw.pop("tables", None)),
                       page_size=kw.pop("page_size", 37))


def _client(tables, token="t0k", tamper=None):
    def handler(req: httpx.Request) -> httpx.Response:
        status, body = respond(tables, token, req.url.path.removeprefix("/api"), req.url.query.decode(),
                               req.headers.get("authorization"))
        if tamper:
            status, body = tamper(req, status, body)
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_config_comes_from_the_environment(tmp_path):
    assert HrApiConfig.from_env({}) is None                       # 주소가 없으면 CSV 모드
    c = HrApiConfig.from_env({"TEAMWEAVER_HR_API_URL": "https://hr.example/api/", "TEAMWEAVER_HR_API_TOKEN": " x "})
    assert c.base_url == "https://hr.example/api" and c.token == "x" and c.host == "hr.example"
    assert set(c.tables) == set(hr_api.HR_TABLES)
    p = tmp_path / "hr.json"
    p.write_text(json.dumps({"tables": {"reviews.csv": None, "review_items.csv": None,
                                        "projects.csv": {"endpoint": "pms/projects"},
                                        "people.csv": {"fields": {"display_name": "name"}}}}), "utf-8")
    c = HrApiConfig.from_env({"TEAMWEAVER_HR_API_URL": "http://hr", "TEAMWEAVER_HR_API_CONFIG": str(p)})
    assert "reviews.csv" not in c.tables and c.tables["projects.csv"].endpoint == "pms/projects"
    assert c.tables["people.csv"] == hr_api.TableSource("people", {"display_name": "name"})
    for bad in ({"tables": {"nope.csv": None}}, {"tables": {"people.csv": {"fields": {"no_col": "x"}}}},
                {"page_size": 0}, [1]):
        p.write_text(json.dumps(bad), "utf-8")
        with pytest.raises(HrApiError):
            HrApiConfig.from_env({"TEAMWEAVER_HR_API_URL": "http://hr", "TEAMWEAVER_HR_API_CONFIG": str(p)})


def test_fetch_follows_pages_and_rejects_bad_answers():
    tables = load_tables(DEMO)
    with _client(tables) as cl:
        got = fetch(_cfg(), cl)
    assert {k: len(v) for k, v in got.items()} == {n: len(tables[ep]) for n, ep in hr_api.DEFAULT_ENDPOINTS.items()}
    assert len(got["person_skills.csv"]) > 37                     # 여러 쪽
    with _client(tables, token="other") as cl, pytest.raises(HrApiError, match="인증"):
        fetch(_cfg(), cl)
    cases = [lambda r, s, b: (500, {}), lambda r, s, b: (200, {"rows": []}),
             lambda r, s, b: (200, {"items": b.get("items", []), "next": "0"})]   # 되풀이 커서
    for tamper in cases:
        with _client(tables, tamper=tamper) as cl, pytest.raises(HrApiError):
            fetch(_cfg(), cl)


def test_fetched_tables_merge_into_the_same_dataset(tmp_path):
    """시연 묶음을 가짜 인사 시스템으로 내보내 받아 합치면 같은 사람·기술·평가가 된다. 사업 표·manifest 단계는 지금 판의 것."""
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    current = _files(DEMO)
    with _client(load_tables(DEMO)) as cl:
        files = merge(current, fetch(_cfg(), cl), _cfg(), as_of="2026-09")
    root = tmp_path / "b"
    root.mkdir()
    for n, text in files.items():
        (root / n).write_text(text, "utf-8")
    b1, r1 = load_bundle(DEMO)
    b2, r2 = load_bundle(root)
    assert r2.ok, r2.summary()
    d1, _ = to_dataset(b1, r1)
    d2, _ = to_dataset(b2, r2)
    assert d1.model_dump() == d2.model_dump()
    m = json.loads(files["manifest.json"])
    before = json.loads(current["manifest.json"])
    assert m["as_of"] == "2026-09" and "files" not in m and "lifecycle" in m
    assert {k: v for k, v in m.items() if k != "as_of"} == {k: v for k, v in before.items() if k not in ("as_of", "files")}
    assert files["projects.csv"] == current["projects.csv"]
    assert hr_api.changed_tables(files, files) == []


def test_a_table_left_out_keeps_the_current_data():
    """평가 서비스가 없는 회사: 평가 표는 설정에서 빼고(null) 지금 데이터(CSV로 올린 것)를 그대로 쓴다."""
    tables = load_tables(DEMO)
    cfg = _cfg(tables={"reviews.csv": None, "review_items.csv": None})
    with _client(tables) as cl:
        got = fetch(cfg, cl)
    assert "reviews.csv" not in got
    current = _files(DEMO)
    assert merge(current, got, cfg, as_of="2026-09")["reviews.csv"] == current["reviews.csv"]
    # 기술 경력을 지금 데이터로 두면 데이터 기준월도 그대로(Codex 리뷰 2026-10-19 -- 받은 달로 바꾸면 경력 늘리기가 줄어든다)
    cfg = _cfg(tables={"person_skills.csv": None})
    with _client(tables) as cl:
        got = fetch(cfg, cl)
    m = json.loads(merge(current, got, cfg, as_of="2026-11")["manifest.json"])
    assert m["as_of"] == json.loads(current["manifest.json"])["as_of"] == "2026-09"


def test_the_mock_server_speaks_http():
    srv = make_server(DEMO, 0, "t0k")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        cfg = HrApiConfig(base_url=f"http://127.0.0.1:{srv.server_address[1]}", token="t0k",
                          tables={"people.csv": hr_api.TableSource("people")}, page_size=40)
        assert len(fetch(cfg)["people.csv"]) == 100
        with pytest.raises(HrApiError, match="인증"):
            fetch(HrApiConfig(base_url=cfg.base_url, token="bad", tables=cfg.tables))
    finally:
        srv.shutdown()
        srv.server_close()
