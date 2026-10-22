"""사업 요구 기술 · 발주 자료 API(2026-10-23 사용자 "신규 사업 편성화면에 각 사업에 어떤 기술을 요하는지" · "그 자료를 다운 받을 수 있는 버튼").

연말형 시연 묶음으로 서버를 띄운다(신규 사업 J018·J019·J020, 진행 사업 J001~J017)."""
import csv
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

PDF = b"%PDF-1.4\n% fake\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
ZIP = b"PK\x03\x04" + b"\x00" * 40


@pytest.fixture(autouse=True)
def _no_demo_docs(tmp_path, monkeypatch):
    """올린 자료만 보는 시험이 저장소 시연 자료(demo/project_docs)에 흔들리지 않게 -- 시연 자료를 보는 시험은 다시 바꾼다."""
    import api.project_docs as store
    real = store.DEMO_ROOT
    monkeypatch.setattr(store, "DEMO_ROOT", tmp_path / "no-demo")
    store._demo_index.cache_clear()
    yield real
    store._demo_index.cache_clear()


def _v(c):
    return c.get("/api/workspace").json()["dataset_version"]


def _up(c, pid, data=PDF, *, kind="제안요청서(RFP)", filename="RFP 초안.pdf", v=None):
    params = {"kind": kind, "filename": filename}
    if v is not None:
        params["dataset_version"] = v
    return c.post(f"/api/project-docs/{pid}", params=params, content=data,
                  headers={"Content-Type": "application/octet-stream"})


def _docs(c):
    return c.get("/api/project-docs").json()["docs"]


def test_requirements_are_the_registered_rows_with_the_model_level(yearend):
    from api.main import app
    from core.ingest.convert import level_from_months
    rows = [r for r in csv.DictReader(open(yearend / "project_skill_requirements.csv", encoding="utf-8")) if r["project_id"] == "J018"]
    with TestClient(app) as c:
        body = c.get("/api/project-requirements").json()
    req = body["requirements"]
    assert set(req) >= {f"J{i:03d}" for i in range(1, 21)}
    got = [(r["skill"], r["months"], r["level"], r["headcount"]) for r in req["J018"]]
    want = [(r["skill_name"], int(r["min_experience_months"]), level_from_months(int(r["min_experience_months"])), int(r["headcount"]))
            for r in rows]
    assert got == want and got
    assert {r["level"] for r in req["J018"]} <= {1, 2, 3, 4, 5}


def test_upload_list_download_delete_keeps_the_dataset_version(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _v(c)
        r = _up(c, "J018", v=v)
        assert r.status_code == 200, r.text
        doc = r.json()["doc"]
        assert doc["kind"] == "제안요청서(RFP)" and doc["filename"] == "RFP 초안.pdf" and doc["size"] == len(PDF) and doc["demo"] is False
        assert _v(c) == v                                    # 자료는 계산 입력이 아니다 -- 판·캐시가 그대로
        assert [d["id"] for d in _docs(c)["J018"]] == [doc["id"]]
        got = c.get(f"/api/project-docs/J018/{doc['id']}")
        assert got.status_code == 200 and got.content == PDF
        assert got.headers["content-type"].startswith("application/pdf")
        assert got.headers["x-content-type-options"] == "nosniff"
        cd = got.headers["content-disposition"]
        assert cd.startswith("attachment") and quote("RFP 초안.pdf") in cd
        # 다른 사업 번호로는 못 받는다
        assert c.get(f"/api/project-docs/J019/{doc['id']}").status_code == 404
        d = c.delete(f"/api/project-docs/J018/{doc['id']}", params={"dataset_version": v})
        assert d.status_code == 200 and _docs(c).get("J018") is None
        assert c.get(f"/api/project-docs/J018/{doc['id']}").status_code == 404
        assert c.delete(f"/api/project-docs/J018/{doc['id']}", params={"dataset_version": v}).status_code == 404


def test_rejects_wrong_files_kinds_projects_and_stale_versions(yearend, monkeypatch):
    import api.project_docs as store
    from api.main import app
    with TestClient(app) as c:
        v = _v(c)
        assert _up(c, "J018", ZIP, filename="rfp.pdf").status_code == 422          # 이름만 PDF
        assert _up(c, "J018", PDF, filename="run.exe").status_code == 422          # 받지 않는 형식
        assert _up(c, "J018", PDF, filename="pdf").status_code == 422              # 확장자 없음
        assert _up(c, "J018", b"", filename="a.pdf").status_code == 422            # 빈 파일
        assert _up(c, "J018", kind="견적서").status_code == 422                    # 정해진 종류만
        assert _up(c, "J999").status_code == 404                                    # 없는 사업
        assert _up(c, "J018", v="old-version").status_code == 409                  # 옛 판
        assert _up(c, "J018", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, filename="과업.hwp", kind="과업지시서").status_code == 200
        assert _up(c, "J018", ZIP, filename="묶음.zip", kind="기타").status_code == 200
        r = _up(c, "J018", PDF, filename="../../etc/evil.pdf")
        assert r.status_code == 200 and r.json()["doc"]["filename"] == "evil.pdf"    # 경로는 떼고 이름만
        monkeypatch.setattr(store, "MAX_PER_PROJECT", 3)
        assert _up(c, "J018").status_code == 422                                    # 사업당 개수
        monkeypatch.setattr(store, "MAX_BYTES", 1000)
        assert _up(c, "J019", PDF + b"x" * 2000).status_code == 413                # 한 파일 크기
        assert _docs(c).get("J019") is None


def test_large_files_skip_the_2mib_json_body_limit(yearend):
    from api.main import app
    big = PDF + b"0" * (3 * 1024 * 1024)
    with TestClient(app) as c:
        r = _up(c, "J018", big, filename="큰 RFP.pdf")
        assert r.status_code == 200, r.text[:200]
        assert r.json()["doc"]["size"] == len(big)


def test_reused_project_number_does_not_inherit_old_documents(yearend, monkeypatch):
    """등록 되돌리기 뒤 새 등록이 같은 번호(J021)를 받아도 앞 사업의 자료가 붙지 않는다(올릴 때의 등록 시각으로 가른다)."""
    import api.workspace as wsmod
    from api.main import app
    import itertools
    tick = itertools.count()          # 같은 초 안에 두 번 등록해도 등록 시각이 다르게(실제는 초 단위)
    monkeypatch.setattr(wsmod, "_now", lambda: f"2026-10-23T01:{next(tick) % 60:02d}:00+00:00")
    with TestClient(app) as c:
        form = {**c.get("/api/workspace/projects/J010/form").json(), "name": "보험사 B 고도화", "start_month": "2027-01",
                "end_month": "2028-06", "stage": "예정", "predecessor": "J010"}
        r = c.post("/api/workspace/projects", json={"dataset_version": _v(c), "project": form})
        assert r.status_code == 200 and r.json()["project_id"] == "J021"
        assert _up(c, "J021", kind="사전규격").status_code == 200
        assert len(_docs(c)["J021"]) == 1
        assert c.post("/api/workspace/undo", json={"dataset_version": _v(c)}).status_code == 200
        assert "J021" not in _docs(c)                                             # 사업이 없으면 안 보인다
        r = c.post("/api/workspace/projects", json={"dataset_version": _v(c), "project": {**form, "name": "다른 사업"}})
        assert r.json()["project_id"] == "J021"
        assert "J021" not in _docs(c)                                             # 같은 번호의 다른 사업


def test_demo_documents_show_for_the_matching_synthetic_data_only(yearend, tmp_path, monkeypatch):
    import api.project_docs as store
    from api.main import app
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "j001.pdf").write_bytes(PDF)
    (tmp_path / "demo" / "index.json").write_text(json.dumps({"docs": [
        {"id": "j001-sow", "registered_at": {"org-n100-s2026-yearend": None}, "project_id": "J001", "kind": "과업지시서",
         "filename": "J001 과업지시서(가상).pdf", "file": "j001.pdf", "size": len(PDF), "sha256": ""},
        {"id": "other", "registered_at": {"someone-else": None}, "project_id": "J002", "kind": "기타",
         "filename": "x.pdf", "file": "j001.pdf", "size": len(PDF), "sha256": ""},
        {"id": "escape", "registered_at": {"org-n100-s2026-yearend": None}, "project_id": "J003", "kind": "기타",
         "filename": "x.pdf", "file": "../secret.pdf", "size": 1, "sha256": ""},
        {"id": "broken"},                                                          # 깨진 항목 하나가 목록 전체를 막지 않는다
        {"id": "late", "registered_at": {"org-n100-s2026-yearend": "1999-01-01"}, "project_id": "J018", "kind": "기타",
         "filename": "x.pdf", "file": "j001.pdf", "size": len(PDF), "sha256": ""}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(store, "DEMO_ROOT", tmp_path / "demo")
    store._demo_index.cache_clear()
    with TestClient(app) as c:
        docs = _docs(c)
        assert list(docs) == ["J001"] and docs["J001"][0]["demo"] is True       # 다른 데이터·밖·등록 시각이 다른 사업은 없다
        assert c.get("/api/project-docs/J001/demo-j001-sow").content == PDF
        assert c.delete("/api/project-docs/J001/demo-j001-sow", params={"dataset_version": _v(c)}).status_code == 409
    assert store.demo_docs("org-n100-s2026-yearend", None) == []                 # 실데이터(가상 표시 없음)에는 붙지 않는다


def test_admin_token_guards_upload_and_delete(yearend, monkeypatch):
    from api.main import app
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "t" * 32)
    with TestClient(app) as c:
        assert _up(c, "J018").status_code == 401
        r = c.post("/api/project-docs/J018", params={"kind": "기타", "filename": "a.pdf"}, content=PDF,
                   headers={"x-admin-token": "t" * 32, "Content-Type": "application/octet-stream"})
        assert r.status_code == 200
        doc = r.json()["doc"]["id"]
        assert c.delete(f"/api/project-docs/J018/{doc}").status_code == 401


def test_unreadable_index_is_not_overwritten(tmp_path, monkeypatch):
    import api.project_docs as store
    monkeypatch.setenv("TEAMWEAVER_DATA_DIR", str(tmp_path))
    folder = store._folder("ds")
    folder.mkdir(parents=True)
    (folder / "index.json").write_text("{broken", encoding="utf-8")
    assert store.stored("ds") == []
    with pytest.raises(store.DocError):
        store.add("ds", "J1", "기타", "a.pdf", PDF, registered_at=None, projects={"J1": None})
    assert (folder / "index.json").read_text(encoding="utf-8") == "{broken"


def test_filenames_are_cleaned():
    import api.project_docs as store
    assert store.clean_filename("C:\\Users\\me\\사전규격\u0000.pdf") == "사전규격.pdf"
    long = "가" * 200 + ".hwpx"
    out = store.clean_filename(long)
    assert len(out) == 120 and out.endswith(".hwpx")
    with pytest.raises(store.DocError):
        store.clean_filename("/../")


def test_yearend_demo_shows_the_bundled_documents(yearend, _no_demo_docs, monkeypatch):
    """시연(연말형 기록·묶음): 진행 사업 J001·J003·J011, 신규 사업 J018·J019·J020(J020은 사전규격 → RFP 둘)."""
    import api.project_docs as store
    from api.main import app
    monkeypatch.setattr(store, "DEMO_ROOT", _no_demo_docs)
    store._demo_index.cache_clear()
    with TestClient(app) as c:
        docs = _docs(c)
        kinds = {pid: [d["kind"] for d in ds] for pid, ds in docs.items()}
        assert kinds == {"J001": ["과업지시서"], "J003": ["과업지시서"], "J011": ["제안요청서(RFP)"], "J018": ["제안요청서(RFP)"],
                         "J019": ["사업 개요"], "J020": ["사전규격", "제안요청서(RFP)"]}
        got = c.get(f"/api/project-docs/J018/{docs['J018'][0]['id']}")
        assert got.status_code == 200 and got.content.startswith(b"%PDF-")


def _drop(c, pid):
    r = c.delete(f"/api/workspace/projects/{pid}", params={"dataset_version": _v(c)})
    assert r.status_code == 200, r.text


def _register(c, name="새 사업"):
    form = {**c.get("/api/workspace/projects/J010/form").json(), "name": name, "start_month": "2027-01", "end_month": "2028-06",
            "stage": "예정", "predecessor": "J010"}
    r = c.post("/api/workspace/projects", json={"dataset_version": _v(c), "project": form})
    assert r.status_code == 200, r.text
    return r.json()["project_id"]


def test_reused_number_does_not_inherit_demo_documents(yearend, _no_demo_docs, monkeypatch):
    """떨어뜨린 J020 자리에 새로 등록한 사업(같은 번호 J020)에 J020 가상 자료가 붙지 않는다(폴백 리뷰 M1)."""
    import api.project_docs as store
    from api.main import app
    monkeypatch.setattr(store, "DEMO_ROOT", _no_demo_docs)
    store._demo_index.cache_clear()
    with TestClient(app) as c:
        assert [d["kind"] for d in _docs(c)["J020"]] == ["사전규격", "제안요청서(RFP)"]
        _drop(c, "J020")
        assert _register(c) == "J020"
        assert "J020" not in _docs(c)


@pytest.fixture
def operating(monkeypatch):
    from tests.scenarios.conftest import ROOT
    bundle = ROOT / "demo" / "org-n100-operating"
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(bundle.parent))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(bundle))
    return bundle


def test_document_on_a_project_without_registration_time_is_not_inherited(operating):
    """운영 중 묶음의 신규 제안 J019는 등록 시각이 없다 -- 거기 올린 자료가, 떨어뜨린 뒤 같은 번호로 새로 등록한 사업에 붙지 않는다(M1)."""
    from api.main import app
    with TestClient(app) as c:
        assert _up(c, "J019", filename="old secret.pdf").status_code == 200
        assert [d["filename"] for d in _docs(c)["J019"]] == ["old secret.pdf"]
        _drop(c, "J020")
        _drop(c, "J019")
        assert _register(c) == "J019"
        assert "J019" not in _docs(c)


def test_upload_needs_octet_stream_and_counts_chunked_bodies(yearend, monkeypatch):
    import api.project_docs as store
    from api.main import app
    with TestClient(app) as c:
        r = c.post("/api/project-docs/J018", params={"kind": "기타", "filename": "a.pdf"}, content=PDF,
                   headers={"Content-Type": "text/plain"})
        assert r.status_code == 415                                              # 다른 사이트의 단순 요청을 받지 않는다(S1)
        monkeypatch.setattr(store, "MAX_BYTES", 1000)
        chunks = iter([PDF, b"x" * 600, b"y" * 600])                             # Content-Length 없이 나눠 보내도 센다
        r = c.post("/api/project-docs/J018", params={"kind": "기타", "filename": "a.pdf"}, content=chunks,
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 413
        assert _docs(c).get("J018") is None


def test_dataset_switch_while_reading_the_body_is_rejected(yearend, monkeypatch):
    """본문을 읽는 사이 데이터가 바뀌면 옛 등록 시각으로 저장하지 않고 409(S4)."""
    import types
    import api.routes.project_docs as route
    from api.main import app
    real = route._read_capped

    async def switched(request):
        data = await real(request)
        request.app.state.dataset = types.SimpleNamespace(info=types.SimpleNamespace(version="someone-else"))
        return data

    with TestClient(app) as c:
        v = _v(c)
        before = app.state.dataset
        monkeypatch.setattr(route, "_read_capped", switched)
        try:
            r = _up(c, "J018", v=v)
        finally:
            app.state.dataset = before
        assert r.status_code == 409
        monkeypatch.setattr(route, "_read_capped", real)
        assert _docs(c).get("J018") is None


def test_pdf_header_may_follow_leading_bytes():
    import api.project_docs as store
    store.check_bytes("pdf", b"\x00" * 100 + PDF)                                 # 규격: 첫 1024바이트 안
    with pytest.raises(store.DocError):
        store.check_bytes("pdf", b"\x00" * 2000 + PDF)


# --- 발주 자료에서 필요 기술 읽기(2026-10-23, 사용자 결정 "a·b 나눠서": 글자 뽑기·라우트 = b, 읽기 함수 = claude-a) -----------

def _zipped(name, xml):
    import io
    import zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, xml)
    return b.getvalue()


def test_extract_text_reads_office_xml_in_reading_order_and_not_old_hwp():
    """HWPX hp:t 안의 탭·줄바꿈 뒤 글자를 잃지 않고, 표 앞 글 → 표 칸 → 표 뒤 글 순서. 옛 HWP·ZIP은 None."""
    import api.project_docs as store
    hwpx = _zipped("Contents/section0.xml", '<hs:sec xmlns:hs="s" xmlns:hp="p"><hp:p><hp:run><hp:t>필요 기술:<hp:tab/>Java 5년 이상'
                                            '<hp:lineBreak/>Oracle 3년</hp:t></hp:run></hp:p><hp:p><hp:run><hp:t>표 앞</hp:t><hp:tbl><hp:tr>'
                                            '<hp:tc><hp:p><hp:run><hp:t>ETL 2명</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl>'
                                            '<hp:t>표 뒤</hp:t></hp:run></hp:p></hs:sec>')
    assert store.extract_text("hwpx", hwpx) == "필요 기술: Java 5년 이상\nOracle 3년\n표 앞\nETL 2명\n표 뒤"
    docx = _zipped("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>요구 기술: </w:t></w:r><w:r><w:t>Java 3년</w:t>'
                                        '</w:r></w:p><w:p><w:r><w:t>ETL 2명</w:t></w:r></w:p></w:body></w:document>')
    assert store.extract_text("docx", docx) == "요구 기술: Java 3년\nETL 2명"
    assert store.extract_text("hwp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 10) is None
    assert store.extract_text("zip", _zipped("a.txt", "x")) is None
    assert store._part_key("sec²1tion12.xml")                               # "²"는 숫자가 아니다(예전엔 int()에서 터졌다)


def test_extract_text_turns_broken_files_into_doc_errors():
    import api.project_docs as store
    broken = bytearray(_zipped("word/document.xml", "<w:document xmlns:w='w'><w:body><w:p><w:r><w:t>" + "x" * 500 +
                                                     "</w:t></w:r></w:p></w:body></w:document>"))
    broken[60] ^= 0xFF                                                           # 압축 본문 한 바이트 -- CRC가 깨진다
    for ext, data in [("docx", bytes(broken)), ("docx", b"PK\x03\x04 not a zip"), ("pdf", b"%PDF-broken")]:
        with pytest.raises(store.DocError):
            store.extract_text(ext, data)


def test_extract_text_is_linear_on_big_tables_and_deep_nesting():
    """첫 판은 문단마다 하위 트리를 다시 돌아 4,000칸 표에 5.7초였다(폴백 리뷰 N-M1) -- 한 번만 돈다."""
    import time
    import api.project_docs as store
    cell = '<hp:tc><hp:p><hp:run><hp:t>ETL 2명</hp:t></hp:run></hp:p></hp:tc>'
    big = _zipped("Contents/section0.xml", '<hs:sec xmlns:hs="s" xmlns:hp="p"><hp:p><hp:run><hp:tbl><hp:tr>' + cell * 4000 +
                                           '</hp:tr></hp:tbl></hp:run></hp:p></hs:sec>')
    deep = _zipped("word/document.xml", "<w:document xmlns:w='w'><w:body>" + "<w:p>" * 50_000 + "<w:r><w:t>x</w:t></w:r>" +
                                        "</w:p>" * 50_000 + "</w:body></w:document>")
    t = time.monotonic()
    assert store.extract_text("hwpx", big).count("ETL 2명") == 4000
    assert store.extract_text("docx", deep) == "x"
    assert time.monotonic() - t < 3.0


def test_extract_text_limited_runs_apart_with_time_and_slot_limits(_no_demo_docs):
    """서버 경로: 별도 프로세스(데이터는 시작 뒤 파이프로) + 시간 제한 + 동시 개수 상한(폴백 리뷰 S3·N-M2·N-S1)."""
    import api.project_docs as store
    data = (_no_demo_docs / "j018-rfp.pdf").read_bytes()
    assert "가상 자료" in store.extract_text_limited("pdf", data)
    assert store.extract_text_limited("hwp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 10) is None
    with pytest.raises(store.DocError, match="넘겨 멈췄다"):
        store.extract_text_limited("pdf", data, seconds=0.001)
    with pytest.raises(store.DocError, match="PDF를 읽지 못했다"):
        store.extract_text_limited("pdf", b"%PDF-broken")
    held = [store._extract_slots.acquire(timeout=1) for _ in range(store.EXTRACT_PARALLEL)]
    try:
        assert all(held)
        with pytest.raises(store.DocError, match="다른 자료를 읽는 중"):
            store.extract_text_limited("pdf", data, seconds=0.2)
    finally:
        for _ in held:
            store._extract_slots.release()


def _read(c, data, *, filename="RFP.pdf", v=None, ctype="application/octet-stream"):
    params = {"filename": filename, **({"dataset_version": v} if v else {})}
    return c.post("/api/project-requirements/read", params=params, content=data, headers={"Content-Type": ctype})


def test_read_route_without_ai_uses_rules_and_changes_nothing(yearend, _no_demo_docs, ai_client):
    """AI가 없으면 규칙으로 읽는다. 시연 RFP(J018)의 요구 기술을 찾고, 데이터 판은 그대로다."""
    from api.main import app
    data = (_no_demo_docs / "j018-rfp.pdf").read_bytes()
    with TestClient(app) as c:
        v = _v(c)
        with ai_client(None):
            r = _read(c, data, v=v)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["method"] == "rule" and body["ai_available"] is False and body["fallback_reason"] == "no_client"
        assert {"ETL", "메타데이터 관리"} <= {s["name"] for s in body["skills"]} and body["chars"] > 200
        assert all(s["quote"] for s in body["skills"])
        assert _v(c) == v                                                         # 제안만 -- 데이터는 그대로


def test_read_route_with_ai_passes_only_document_text(yearend, _no_demo_docs, ai_client):
    import json
    from types import SimpleNamespace
    from api.main import app
    from core.ingest.skills import load_dictionary
    etl = next(cid for cid, c in load_dictionary().concepts.items() if c["label"] == "ETL")
    calls = []

    class Fake:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            calls.append(kw)
            msg = SimpleNamespace(content=json.dumps({"skills": [{"id": etl, "quote": "ETL 경력 5년 이상 2명", "kind": "required",
                                                                  "months": 60, "headcount": 2}], "not_required": [], "unmapped_terms": []},
                                                     ensure_ascii=False))
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))

        def with_options(self, **kw):
            return self

    data = (_no_demo_docs / "j018-rfp.pdf").read_bytes()
    with TestClient(app) as c, ai_client(Fake()) as used:
        people = [p["name"] for p in c.get("/api/meta").json()["people"]]
        r = _read(c, data, v=_v(c))
    assert r.status_code == 200, r.text
    body = r.json()
    assert used and body["method"] == "ai" and body["ai_available"] is True
    assert [(s["name"], s["months"], s["headcount"]) for s in body["skills"]] == [("ETL", 60, 2)]
    sent = "\n".join(m["content"] for m in calls[0]["messages"])
    assert "ETL 경력 5년 이상 2명" in sent                                        # 자료 글자는 간다
    assert people and not [n for n in people if n in sent]                       # 사람 이름은 가지 않는다(자료 글자 + 사전만)


def test_read_route_rejects_unreadable_wrong_type_and_stale(yearend, _no_demo_docs):
    from api.main import app
    with TestClient(app) as c:
        assert _read(c, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 64, filename="과업.hwp").status_code == 422   # 글자 못 읽는 형식
        assert _read(c, b"%PDF-broken").status_code == 422
        assert _read(c, PDF, filename="run.exe").status_code == 422
        assert _read(c, PDF, ctype="text/plain").status_code == 415
        assert _read(c, PDF, v="old-version").status_code == 409
        big = PDF + b"0" * (3 * 1024 * 1024)                                     # 2MiB JSON 상한을 건너뛴다(자기 상한 20MiB)
        assert _read(c, big).status_code in (200, 422)
        assert _read(c, big).status_code != 413


def test_extract_text_keeps_sibling_breaks_skips_phonetics_and_keeps_read_part():
    """DOCX w:tab·w:br, PPTX a:br은 글자 요소의 형제다 -- 빠뜨리면 'Java5년 이상Oracle'로 붙어 규칙 읽기가 Java를 놓쳤다(폴백 리뷰 S1).
    XLSX 후리가나(rPh)는 글자로 치지 않고, 잘린 XML도 그때까지 읽은 문단은 남긴다."""
    import api.project_docs as store
    docx = _zipped("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:pPr><w:tabs><w:tab w:val="left"/></w:tabs></w:pPr>'
                                        '<w:r><w:t>Java</w:t><w:tab/><w:t>5년 이상</w:t><w:br/><w:t>Oracle 3년</w:t></w:r></w:p></w:body></w:document>')
    assert store.extract_text("docx", docx) == "Java 5년 이상\nOracle 3년"
    pptx = _zipped("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:p><a:r><a:t>Spark</a:t></a:r><a:br/><a:r><a:t>Kafka</a:t></a:r>'
                                            '</a:p></p:sld>')
    assert store.extract_text("pptx", pptx) == "Spark\nKafka"
    xlsx = _zipped("xl/sharedStrings.xml", '<sst xmlns="x"><si><t>ETL</t><rPh sb="0" eb="1"><t>イーティーエル</t></rPh></si></sst>')
    assert store.extract_text("xlsx", xlsx) == "ETL"
    cut = _zipped("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>앞 문단</w:t></w:r></w:p><w:p><w:r><w:t>뒤')
    assert store.extract_text("docx", cut) == "앞 문단"


def test_read_route_respects_the_external_block(yearend, _no_demo_docs, monkeypatch):
    """실데이터처럼 외부 전송이 막히면(llm_client_for) AI를 부르지 않고 규칙으로 읽는다."""
    import api.deps as deps
    from api.main import app
    monkeypatch.setattr(deps, "llm_client_for", lambda dataset, client: (None, "external_blocked"))
    with TestClient(app) as c:
        r = _read(c, (_no_demo_docs / "j018-rfp.pdf").read_bytes())
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "rule" and r.json()["fallback_reason"] == "external_blocked" and r.json()["ai_available"] is False


@pytest.mark.parametrize("base_url,expected", [(None, "none"), ("https://api.openai.com/v1", "external"), ("http://10.0.0.5/v1", "onprem")])
def test_requirements_tell_where_document_text_would_go(yearend, ai_client, base_url, expected):
    """읽기 전에 화면이 보내는 곳을 알린다(폴백 리뷰 S2) -- llm_client_for와 같은 사내 판정."""
    from types import SimpleNamespace
    from api.main import app
    fake = None if base_url is None else SimpleNamespace(base_url=base_url, with_options=lambda **kw: fake)
    with TestClient(app) as c, ai_client(fake):
        assert c.get("/api/project-requirements").json()["ai_destination"] == expected


def test_clean_text_keeps_a_pdf_with_lone_surrogates_or_control_characters_readable():
    """pypdf가 남긴 짝 없는 대리 문자 하나·NUL 수만 개로 PDF 한 편이 통째로 실패하지 않게(2차 폴백 리뷰 SHOULD: JSON 답 회귀)."""
    import json
    import api.project_docs as store
    out = store.clean_text("Java 5 years\ud800 required\x00\x07\n  ETL\t2명 ")
    assert out == "Java 5 years� required\nETL 2명"
    json.dumps({"value": out}, ensure_ascii=False).encode("utf-8")                # 자식 답으로 보낼 수 있다
    assert store.clean_text("\x00" * 60_000) == ""
    worst = json.dumps({"status": "ok", "value": "\u0001" * store.MAX_TEXT_CHARS}, ensure_ascii=False).encode("utf-8")
    assert len(worst) <= store.REPLY_MAX_BYTES                                   # 정리 전이어도 답 상한 안
    bad = _zipped("word/document.xml", '<?xml version="1.0" encoding="x-nope"?><w:document xmlns:w="w"/>')
    with pytest.raises(store.DocError):                                          # 낯선 encoding도 DocError만
        store.extract_text("docx", bad)
