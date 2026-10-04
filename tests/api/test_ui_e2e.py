"""실브라우저 E2E: 배치 설정 → 데이터 업로드 → 최적화 → 교체 검토·적용 → PDF (K8·K9·K10·K4 통합).

단위 테스트는 클라이언트를 목으로 바꾸므로, 실제 브라우저와 실제 서버가 주고받을 때만 드러나는
계약 불일치(dataset_version 전파, optimize가 준 plan_token의 PDF 검증, % 변환)를 여기서 본다.
LLM은 부르지 않는다(get_openai_client_or_none → None, 규칙 기반 브리핑).
"""
import csv
import io
import os
import re
import socket
import threading
import time
import zipfile
from pathlib import Path

import pytest
import uvicorn
from pypdf import PdfReader

from api.deps import get_openai_client_or_none
from api.main import app
from core.config import REPO_ROOT
from core.ingest.synthetic import generate_bundle

pytestmark = pytest.mark.slow


def _dist_missing() -> bool:
    return not (REPO_ROOT / "web" / "dist" / "index.html").exists()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui-e2e")
    saved = {k: os.environ.get(k) for k in
             ("TEAMWEAVER_SKIP_WARM", "TEAMWEAVER_SETTINGS_PATH", "TEAMWEAVER_ADMIN_TOKEN")}
    os.environ["TEAMWEAVER_SKIP_WARM"] = "1"
    os.environ["TEAMWEAVER_SETTINGS_PATH"] = str(tmp / "settings.json")
    os.environ.pop("TEAMWEAVER_ADMIN_TOKEN", None)
    prev_override = app.dependency_overrides.get(get_openai_client_or_none)
    app.dependency_overrides[get_openai_client_or_none] = lambda: None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    try:
        for _ in range(200):
            if srv.started:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn이 기동하지 않았다")
        yield f"http://127.0.0.1:{port}", tmp
    finally:
        srv.should_exit = True
        t.join(timeout=10)
        if prev_override is None:
            app.dependency_overrides.pop(get_openai_client_or_none, None)
        else:
            app.dependency_overrides[get_openai_client_or_none] = prev_override
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _bundle_zip(dest) -> tuple:
    root = generate_bundle(dest / "bundle", 20, 4, 11)
    path = dest / "bundle.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(root.iterdir()):
            z.write(f, f.name)
    with open(root / "people.csv", encoding="utf-8-sig", newline="") as f:
        names = [row["display_name"] for row in csv.DictReader(f)]
    return path, names


def _pdf_text(data: bytes) -> str:
    raw = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    return re.sub(r"[ \t]+", " ", raw)


@pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")
def test_settings_upload_optimize_apply_and_pdf_in_a_real_browser(server):
    from playwright.sync_api import expect, sync_playwright

    base, tmp = server
    zip_path, uploaded_names = _bundle_zip(tmp)
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(accept_downloads=True)
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_default_timeout(60_000)
            page.goto(base + "/")
            expect(page.get_by_role("heading", name="TeamWeaver")).to_be_visible()

            # 1) 배치 설정: 최소 투입률 25%로 저장(K8)
            page.get_by_role("button", name="배치 설정").click()
            page.get_by_label(re.compile("최소 투입률")).fill("25")
            page.get_by_role("button", name="저장").click()
            expect(page.get_by_text("다음 '최적화 실행'부터")).to_be_visible()

            # 2) 데이터: 가상 20명/4프로젝트 묶음 업로드·전환(K9)
            page.get_by_role("button", name="데이터", exact=True).click()
            page.get_by_label("묶음 zip 파일").set_input_files(str(zip_path))
            # 전환 뒤 화면이 새 meta를 받아야 최적화 요청에 새 dataset_version이 실린다 --
            # "전환했다" 문구만으로는 meta 갱신이 끝났다는 보장이 없다.
            with page.expect_response(lambda r: r.url.endswith("/api/meta")) as meta_resp:
                page.get_by_role("button", name="검증 후 전환").click()
            assert meta_resp.value.ok
            expect(page.get_by_text("이 데이터로 전환했다")).to_be_visible()
            expect(page.get_by_text("synthetic-n20-p4-s11")).to_be_visible()

            # 3) 최적화(실제 CBC)
            page.get_by_role("button", name="요건 설정").click()
            page.get_by_role("button", name="최적화 실행").click()
            expect(page.get_by_text("Plan A", exact=True)).to_be_visible(timeout=120_000)
            # 대안 계산이 끝날 때까지(대기 문구가 사라질 때까지) 기다린다 -- 교체 검토 자체는
            # 대안과 무관하지만, 진행 중 스트림과 겹치지 않게 한다.
            expect(page.get_by_text(re.compile("대안 계산 중"))).to_have_count(0, timeout=180_000)

            # 4) 교체 검토 → 적용(K10). 첫 배치 인력을 첫 대기 인력으로 바꾼다.
            out_sel = page.get_by_label("교체 대상")
            out_value = out_sel.locator("option").nth(1).get_attribute("value")
            out_sel.select_option(out_value)
            in_sel = page.get_by_label("교체 투입")
            in_sel.select_option(in_sel.locator("option").nth(1).get_attribute("value"))
            page.get_by_role("button", name=re.compile("브리핑 생성")).click()
            apply_btn = page.get_by_role("button", name=re.compile("^이 교체 적용"))
            expect(apply_btn).to_be_visible(timeout=60_000)
            # 이 seed(20명/4프로젝트, s11)의 첫 교체는 경고가 없다(결정적). 경고가 있는 확인 흐름은
            # web/src/components/ApplyControl.test.tsx가 다룬다.
            expect(apply_btn).to_have_text("이 교체 적용")
            apply_btn.click()
            expect(page.get_by_text(re.compile("교체 1건 적용"))).to_be_visible()

            # 5) PDF(K4 내부 origin + K10 서버 재계산 + 원 플랜 서명)
            with page.expect_download(timeout=120_000) as dl:
                page.get_by_role("button", name="PDF 내려받기").click()
            pdf_bytes = Path(dl.value.path()).read_bytes()
        finally:
            browser.close()

    assert pdf_bytes.startswith(b"%PDF-")
    text = _pdf_text(pdf_bytes)
    assert "적용된 교체 1건" in text
    assert "서명 확인" in text, "optimize가 준 plan_token이 PDF에서 검증되지 않았다"
    assert "최소 투입률 25%" in text
    # 업로드한 데이터로 계산했다는 직접 증거: 교체 행에 업로드 묶음의 인력 이름이 찍힌다.
    swap_line = next((ln for ln in text.splitlines() if "→" in ln and "빠진 인력" not in ln), "")
    # pypdf는 한글·숫자 사이에 공백을 넣기도 한다("가상인력 0001") -- 공백을 빼고 비교한다.
    compact = re.sub(r"\s+", "", swap_line)
    assert any(n in compact for n in uploaded_names), f"교체 행에 업로드 인력이 없다: {swap_line!r}"
    assert errors == [], f"브라우저 콘솔 오류: {errors}"
