"""인사팀 검토 자료 PDF(실제 브라우저, 2026-10-19 claude-a): 잠긴 사실·이의·판단·NOT_CALIBRATED 단서가 PDF 글자로 나오고, 내보낸 사실이 남는다."""
import threading
import time

import httpx
import pytest
import uvicorn

from tests.api.test_pdf_report import _dist_missing, _extract_text, _free_port
from tests.scenarios.holes import waivers
from tests.scenarios.test_review_docs import BENCH, PULLED, SRC, TGT, _entries

pytestmark = [pytest.mark.slow, pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")]


def test_review_pdf_has_layers_and_records_export(yearend, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_SKIP_WARM", "1")
    from api.main import app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(300):
            if server.started:
                break
            time.sleep(0.1)
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=180.0) as c:
            ws = c.get("/api/workspace").json()
            entries = [e for e in _entries(ws) if not (e["person_id"] == PULLED and e["project_id"] == SRC)]
            entries += [{"person_id": PULLED, "project_id": TGT, "alloc": 1.0}, {"person_id": BENCH, "project_id": TGT, "alloc": 1.0}]
            # 빼 온 자리는 "메우지 않음"(2026-10-21 빼 온 자리 결정 -- 그 뒤 이 느린 시험이 422로 깨져 있었다, 2026-10-22 고침)
            r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual",
                                                     "seat_waivers": waivers(c, ws["dataset_version"], entries)})
            assert r.status_code == 200, r.text
            v = r.json()["workspace"]["dataset_version"]
            doc = c.post(f"/api/review-docs/{v}/{TGT}").json()
            loss = next(f for f in doc["facts"] if f["kind"] == "MOVE_LOSS")
            r = c.put(f"/api/review-docs/{v}/{TGT}", json={
                "revision": doc["revision"], "body": "부서장이 쓴 편성 사유 마커QQ.",
                "notes": [{"key": loss["key"], "type": "objection", "text": "이의 마커ZZ 경력 누락"}],
                "judgments": {PULLED: {"text": "판단 마커WW 막바지", "agreement": {"status": "requested", "memo": "인사팀 경유"}}}})
            assert r.status_code == 200, r.text
            res = c.post(f"/api/review-docs/{v}/{TGT}/pdf")
            assert res.status_code == 200, res.text
            assert res.content.startswith(b"%PDF-")
            text = _extract_text(res.content).replace(" ", "")
            for marker in ("시스템사실·수정불가", "NOT_CALIBRATED", "마커QQ", "마커ZZ", "마커WW", "요청중", SRC):
                assert marker in text, marker
            got = c.get(f"/api/review-docs/{v}/{TGT}").json()
            assert got["status"] == "exported" and got["exported_facts"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
