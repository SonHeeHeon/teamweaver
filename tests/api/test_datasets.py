"""K9: CSV 묶음(zip) 업로드 → 검증 리포트 → 통과 시 활성 데이터셋 전환.

검증·변환은 claude-a의 core.ingest를 그대로 쓴다(import만). 여기서는 업로드 경계
(zip 안전성·크기), 전환의 원자성, 캐시 분리, 오류 시 미전환을 본다.
"""
import io
import json
import zipfile
from pathlib import Path

import pytest

from api.cache import ResultCache
from api.datasets import BundleArchiveError, bundle_version, extract_bundle_zip
from core.ingest.synthetic import generate_bundle
from core.optimize.milp import MilpParams

ZIP = {"Content-Type": "application/zip"}


def _zip_dir(root: Path, prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.iterdir()):
            z.write(p, prefix + p.name)
    return buf.getvalue()


@pytest.fixture
def bundle(tmp_path) -> Path:
    return generate_bundle(tmp_path / "bundle", 12, 3, 5)


@pytest.fixture
def bundle_zip(bundle) -> bytes:
    return _zip_dir(bundle)


def _zip_entries(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


# --- zip 해제 ------------------------------------------------------------

def test_extract_accepts_flat_and_single_top_folder(bundle, tmp_path):
    flat = extract_bundle_zip(_zip_dir(bundle), tmp_path / "a")
    nested = extract_bundle_zip(_zip_dir(bundle, "내보내기/"), tmp_path / "b")
    assert (flat / "manifest.json").is_file()
    assert (nested / "manifest.json").is_file()
    assert bundle_version(flat) == bundle_version(nested) == bundle_version(bundle)


def test_extract_ignores_macos_junk(bundle, tmp_path):
    data = _zip_dir(bundle)
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr("__MACOSX/._people.csv", b"junk")
        z.writestr(".DS_Store", b"junk")
    root = extract_bundle_zip(buf.getvalue(), tmp_path / "x")
    assert not (root / ".DS_Store").exists()


@pytest.mark.parametrize("entries, needle", [
    ({"../evil.csv": b"x", "manifest.json": b"{}"}, "경로"),
    ({"/abs/manifest.json": b"{}"}, "경로"),
    ({"manifest.json": b"{}", "notes.txt": b"x"}, "notes.txt"),
    ({"a/manifest.json": b"{}", "b/people.csv": b"x"}, "폴더"),
    ({"manifest.json": b"{}", "sub/people.csv": b"x"}, "폴더"),
    ({}, "비어"),
])
def test_extract_rejects_unsafe_or_unexpected_entries(entries, needle, tmp_path):
    with pytest.raises(BundleArchiveError, match=needle):
        extract_bundle_zip(_zip_entries(entries), tmp_path / "x")


def test_extract_rejects_not_a_zip(tmp_path):
    with pytest.raises(BundleArchiveError, match="zip"):
        extract_bundle_zip(b"PK not really", tmp_path / "x")


def test_extract_rejects_zip_bomb(tmp_path, monkeypatch):
    import api.datasets as d
    monkeypatch.setattr(d, "MAX_UNCOMPRESSED_BYTES", 10_000)
    data = _zip_entries({"manifest.json": b"{}", "people.csv": b"0" * 50_000})
    with pytest.raises(BundleArchiveError, match="풀면"):
        extract_bundle_zip(data, tmp_path / "x")


def test_extract_rejects_symlink_entry(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("people.csv")
        info.external_attr = (0o120777 << 16)          # S_IFLNK
        z.writestr(info, "/etc/passwd")
        z.writestr("manifest.json", b"{}")
    with pytest.raises(BundleArchiveError, match="링크"):
        extract_bundle_zip(buf.getvalue(), tmp_path / "x")


# --- 업로드 → 전환 --------------------------------------------------------

def test_active_dataset_starts_as_fixture(client):
    info = client.get("/api/datasets/active").json()
    assert info["source"] == "fixture"
    assert info["people"] == 100 and info["projects"] == 20
    assert len(info["version"]) == 64


def test_valid_bundle_switches_active_dataset(client, bundle, bundle_zip):
    before = client.get("/api/datasets/active").json()
    res = client.post("/api/datasets", content=bundle_zip, headers=ZIP)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["activated"] is True
    assert body["report"]["errors"] == []
    assert body["report"]["notes"]                       # 변환 가정(대리 레벨 등)이 그대로 온다
    assert body["report"]["row_counts"]["people.csv"] == 12
    info = body["dataset"]
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    assert info["dataset_id"] == manifest["dataset_id"]
    assert info["synthetic"] is True and info["source"] == "upload"
    assert info["version"] == bundle_version(bundle) != before["version"]

    meta = client.get("/api/meta").json()
    assert len(meta["people"]) == 12
    assert client.get("/api/datasets/active").json()["version"] == info["version"]


def test_bundle_with_errors_is_422_and_does_not_switch(client, bundle):
    (bundle / "people.csv").write_text("person_id\nbroken\n", encoding="utf-8")
    before = client.get("/api/datasets/active").json()
    res = client.post("/api/datasets", content=_zip_dir(bundle), headers=ZIP)
    assert res.status_code == 422
    body = res.json()
    assert body["activated"] is False
    assert body["report"]["errors"]
    first = body["report"]["errors"][0]
    assert set(first) == {"level", "file", "row", "column", "message"}
    assert client.get("/api/datasets/active").json() == before
    assert len(client.get("/api/meta").json()["people"]) == 100


def test_archive_problem_is_422_with_message_and_no_switch(client):
    res = client.post("/api/datasets", content=b"not a zip", headers=ZIP)
    assert res.status_code == 422
    assert res.json()["activated"] is False
    assert "zip" in res.json()["detail"]
    assert client.get("/api/datasets/active").json()["source"] == "fixture"


def test_wrong_content_type_is_415(client, bundle_zip):
    res = client.post("/api/datasets", content=bundle_zip,
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 415


def test_upload_over_size_limit_is_413(client, monkeypatch, bundle_zip):
    import api.routes.datasets as route
    monkeypatch.setattr(route, "MAX_UPLOAD_BYTES", 1000)
    res = client.post("/api/datasets", content=bundle_zip, headers=ZIP)
    assert res.status_code == 413
    chunked = client.post("/api/datasets", content=iter([bundle_zip[:800], bundle_zip[800:]]),
                          headers=ZIP)
    assert chunked.status_code == 413


def test_reset_returns_to_fixture(client, bundle_zip):
    fixture = client.get("/api/datasets/active").json()
    client.post("/api/datasets", content=bundle_zip, headers=ZIP)
    res = client.post("/api/datasets/reset")
    assert res.status_code == 200
    assert res.json()["version"] == fixture["version"]
    assert len(client.get("/api/meta").json()["people"]) == 100


def test_extracted_files_do_not_stay_on_disk(client, bundle_zip, monkeypatch):
    """실데이터가 서버 디스크에 남지 않는다 -- 실제 해제 폴더가 요청 끝에 지워진다."""
    import api.routes.datasets as route
    seen = []
    real = route.extract_bundle_zip

    def spy(data, dest):
        seen.append(dest)
        return real(data, dest)

    monkeypatch.setattr(route, "extract_bundle_zip", spy)
    assert client.post("/api/datasets", content=bundle_zip, headers=ZIP).status_code == 200
    assert seen and not seen[0].exists() and not seen[0].parent.exists()


def test_upload_while_another_is_running_is_409(client, bundle_zip, monkeypatch):
    """처리 중이면 기다리지 않고 바로 409. (진짜 락으로 시험하면 검사가 빠졌을 때
    테스트가 실패 대신 영원히 멈추므로, 들어가면 터지는 가짜 락을 쓴다.)"""
    class Busy:
        def locked(self):
            return True

        async def __aenter__(self):
            raise AssertionError("처리 중인데 락을 기다리러 들어갔다")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(client.app.state, "dataset_lock", Busy())
    assert client.post("/api/datasets", content=bundle_zip, headers=ZIP).status_code == 409
    assert client.post("/api/datasets/reset").status_code == 409


# --- 캐시 분리 -----------------------------------------------------------

def test_cache_key_includes_dataset_version():
    p = MilpParams()
    assert ResultCache.key({}, p, 3, "v1") != ResultCache.key({}, p, 3, "v2")


def test_optimize_after_switch_does_not_reuse_fixture_cache(client, bundle_zip, monkeypatch):
    """같은 weights·params라도 데이터셋이 바뀌면 이전 결과를 돌려주지 않는다."""
    calls = []

    def fake_stream(graph, S, C, params, n):
        calls.append(len(graph.people))
        return iter(())

    monkeypatch.setattr("api.routes.optimize.generate_plans_streaming", fake_stream)
    monkeypatch.setattr("api.routes.optimize._skill_relaxation_upper_bound", lambda *a: 1.0)
    req = {"weights": {}, "n_alternatives": 0}

    def run():
        with client.stream("POST", "/api/optimize", json=req) as res:
            list(res.iter_lines())

    run()
    run()                                    # 같은 데이터셋 -> 캐시 히트(빈 결과도 저장된다)
    client.post("/api/datasets", content=bundle_zip, headers=ZIP)
    run()                                    # 데이터셋이 바뀌었다 -> 다시 계산
    assert calls == [100, 12]


# --- Codex 리뷰 1라운드 반영 ----------------------------------------------

def test_sqlite_is_in_memory_and_leaves_no_file(bundle, tmp_path, monkeypatch):
    """이름·리뷰가 든 SQLite가 디스크에 남지 않는다(메모리 DB로 복사 후 임시 파일 삭제)."""
    import tempfile as tf

    from api.datasets import build_active
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle

    made = []
    real = tf.TemporaryDirectory

    def spy(*a, **k):
        t = real(*a, **k)
        made.append(Path(t.name))
        return t

    monkeypatch.setattr("api.datasets.tempfile.TemporaryDirectory", spy)
    b, report = load_bundle(bundle)
    ds, parsed = to_dataset(b, report)
    active = build_active(ds, parsed, dataset_id="x", version="v", source="upload", synthetic=True)
    assert made and not any(p.exists() for p in made)
    db_file = active.sqlite_conn.execute("PRAGMA database_list").fetchone()[2]
    assert db_file == ""                                     # 메모리 DB
    assert active.sqlite_conn.execute("SELECT count(*) FROM person").fetchone()[0] == 12


def test_retired_dataset_closes_when_last_user_releases():
    import sqlite3

    from api.datasets import ActiveDataset, DatasetInfo
    info = DatasetInfo("x", "v", "upload", True, 1, 1, "t")
    d = ActiveDataset(graph=None, sqlite_conn=sqlite3.connect(":memory:"), info=info)
    d.acquire()
    d.retire()
    assert not d.closed                                       # 아직 쓰는 요청이 있다
    d.release()
    assert d.closed
    idle = ActiveDataset(graph=None, sqlite_conn=sqlite3.connect(":memory:"), info=info)
    idle.retire()
    assert idle.closed                                        # 쓰는 요청이 없으면 바로


def test_switch_closes_previous_dataset(client, bundle_zip):
    old = client.app.state.dataset
    assert client.post("/api/datasets", content=bundle_zip, headers=ZIP).status_code == 200
    assert old.closed


def test_meta_and_plan_events_carry_dataset_version(client):
    version = client.get("/api/datasets/active").json()["version"]
    assert client.get("/api/meta").json()["dataset_version"] == version


@pytest.mark.parametrize("path, body", [
    ("/api/optimize", {"weights": {}, "n_alternatives": 0}),
    ("/api/whatif", {"entries": [{"person_id": "p000", "project_id": "j00", "alloc": 0.5}],
                     "swap": {"out_person_id": "p000", "in_person_id": "p001", "project_id": "j00"}}),
    ("/api/report", {"plan_label": "A", "entries": [], "objective": 1.0, "fulfillment": 1.0,
                     "optimization_ratio": 1.0}),
])
def test_requests_from_a_stale_screen_are_409(client, path, body):
    """다른 사용자가 데이터셋을 바꾼 뒤, 옛 meta를 본 화면의 요청은 거부한다."""
    res = client.post(path, json={**body, "dataset_version": "0" * 64})
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "dataset_changed"


def test_admin_token_guards_switch_reset_and_settings(client, bundle_zip, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "s3cret")
    assert client.get("/api/admin").json() == {"token_required": True}
    assert client.post("/api/datasets", content=bundle_zip, headers=ZIP).status_code == 401
    assert client.post("/api/datasets/reset").status_code == 401
    settings = client.get("/api/settings").json()["settings"]
    put = {"settings": settings, "based_on": None}
    assert client.put("/api/settings", json=put).status_code == 401
    assert client.put("/api/settings", json=put,
                      headers={"X-Admin-Token": "wrong"}).status_code == 401
    ok = client.post("/api/datasets", content=bundle_zip,
                     headers={**ZIP, "X-Admin-Token": "s3cret"})
    assert ok.status_code == 200
    assert client.get("/api/datasets/active").json()["source"] == "upload"


def test_admin_token_is_not_required_when_unset(client):
    assert client.get("/api/admin").json() == {"token_required": False}


def test_many_directory_entries_are_rejected_before_parsing(tmp_path):
    """빈 디렉터리 항목 수십만 개로 zipfile 파싱 자원을 태우는 zip을 미리 거른다."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k in range(500):
            z.writestr(f"d{k}/", b"")
        z.writestr("manifest.json", b"{}")
    with pytest.raises(BundleArchiveError, match="항목이 너무 많다"):
        extract_bundle_zip(buf.getvalue(), tmp_path / "x")


# --- Codex 리뷰 2라운드 반영 ----------------------------------------------

def test_forged_end_record_count_does_not_bypass_entry_limit(tmp_path):
    """끝 레코드의 항목 수를 1로 위조해도, 실제 중앙 디렉터리 레코드를 세어 거부한다."""
    import struct
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k in range(500):
            z.writestr(f"d{k}/", b"")
        z.writestr("manifest.json", b"{}")
    data = bytearray(buf.getvalue())
    pos = data.rfind(b"PK\x05\x06")
    data[pos + 8:pos + 12] = struct.pack("<HH", 1, 1)       # 이 디스크·전체 항목 수 위조
    with pytest.raises(BundleArchiveError, match="항목이 너무 많다"):
        extract_bundle_zip(bytes(data), tmp_path / "x")


def test_dataset_dependency_runs_on_the_event_loop():
    """읽기와 acquire 사이에 전환이 끼어들 수 없도록 async 의존성이어야 한다."""
    import inspect

    from api.deps import get_dataset
    assert inspect.isasyncgenfunction(get_dataset)


def test_closed_dataset_cannot_be_acquired():
    import sqlite3

    from api.datasets import ActiveDataset, DatasetInfo
    d = ActiveDataset(graph=None, sqlite_conn=sqlite3.connect(":memory:"),
                      info=DatasetInfo("x", "v", "upload", True, 1, 1, "t"))
    d.retire()
    with pytest.raises(RuntimeError):
        d.acquire()


def test_report_payload_carries_meta_of_the_acquired_dataset(client, monkeypatch, tmp_path):
    """PDF 페이지가 따로 /api/meta를 부르지 않도록, 요청이 잡은 데이터셋의 meta를 넣는다."""
    import api.routes.report as report_route

    index = tmp_path / "index.html"
    index.write_text("x")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    seen = []

    async def fake_render(payload, *args, **kwargs):
        seen.append(payload)
        return b"%PDF-fake"

    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    version = client.get("/api/meta").json()["dataset_version"]
    res = client.post("/api/report", json={"plan_label": "A", "entries": [], "objective": 1.0,
                                          "fulfillment": 1.0, "optimization_ratio": 1.0,
                                          "dataset_version": version})
    assert res.status_code == 200, res.text
    meta = seen[0]["meta"]
    assert meta["dataset_version"] == version and len(meta["people"]) == 100
