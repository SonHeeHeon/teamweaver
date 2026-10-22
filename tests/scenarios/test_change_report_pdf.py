"""변경 보고서 PDF(실제 브라우저, 2026-10-22 claude-a): 기간 묶음 한 PDF에 표지·변경마다 사람 표·사유·인사팀 검토 자료 전문이 글자로 나오고,
검토 자료의 "내보냄" 상태는 바뀌지 않는다."""
import threading
import time

import httpx
import pytest
import uvicorn

from tests.api.test_pdf_report import _dist_missing, _extract_text, _free_port
from tests.scenarios.holes import waivers
from tests.scenarios.test_review_docs import BENCH, PULLED, SRC, TGT, _entries

pytestmark = [pytest.mark.slow, pytest.mark.skipif(_dist_missing(), reason="web/dist 없음 -- `cd web && npm run build` 먼저")]


def test_change_report_pdf_bundles_changes_and_review_docs(yearend, monkeypatch):
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
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180.0) as c:
            ws = c.get("/api/workspace").json()
            entries = [e for e in _entries(ws) if not (e["person_id"] == PULLED and e["project_id"] == SRC)]
            entries += [{"person_id": PULLED, "project_id": TGT, "alloc": 1.0}, {"person_id": BENCH, "project_id": TGT, "alloc": 1.0}]
            r = c.post("/api/workspace/apply", json={"dataset_version": ws["dataset_version"], "entries": entries, "source": "manual",
                                                     "seat_waivers": waivers(c, ws["dataset_version"], entries, "마커WAIVE 막바지")})
            assert r.status_code == 200, r.text
            v = r.json()["workspace"]["dataset_version"]
            doc = c.post(f"/api/review-docs/{v}/{TGT}").json()
            loss = next(f for f in doc["facts"] if f["kind"] == "MOVE_LOSS")
            r = c.put(f"/api/review-docs/{v}/{TGT}", json={"revision": doc["revision"],
                                                           "notes": [{"key": loss["key"], "type": "objection", "text": "이의 마커ZZ"}]})
            assert r.status_code == 200, r.text
            ws = c.get("/api/workspace").json()
            r = c.post("/api/workspace/concurrency", json={"dataset_version": ws["dataset_version"], "person_id": "DP0048", "allow": True,
                                                            "max_projects": 2, "reason": "긴급 지원", "memo": "마커MEMO"})
            assert r.status_code == 200, r.text
            items = c.get("/api/changes").json()["items"]
            assert [x["kind"] for x in items] == ["concurrency", "apply_manual"]
            res = c.post("/api/changes/report", json={"ids": [x["id"] for x in items], "start": "2026-01-01T00:00:00Z"})
            assert res.status_code == 200, res.text
            assert res.content.startswith(b"%PDF-")
            text = _extract_text(res.content).replace(" ", "")
            for marker in ("인력배치변경보고서", "손편성확정1건", "프로젝트동시수행허용", "마커WAIVE", "마커MEMO", "마커ZZ",
                           "시스템사실·수정불가", "인사팀보고사항", "NOT_CALIBRATED", SRC, TGT):
                assert marker in text, marker
            assert c.get(f"/api/review-docs/{v}/{TGT}").json()["status"] == "draft"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
