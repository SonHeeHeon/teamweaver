"""K4(PDF 자원 상한)와 K9·K10(버전 검사·원 플랜 서명·교체 재계산)을 합친 /api/report의 순서.

- 값싼 검사(데이터셋 버전·서명)는 슬롯을 잡기 전에 끝난다.
- 무거운 교체 재계산은 동시성 슬롯 *안에서*, 전체 시간 상한 *안에서* 돈다.
"""
import time

import pytest

import api.routes.report as report_route
from api import pdf

_BASE = {"plan_label": "A", "objective": 1.0, "fulfillment": 1.0, "optimization_ratio": 1.0,
         "entries": [], "base_entries": [{"person_id": "p000", "project_id": "j00", "alloc": 0.5}],
         "applied_swaps": [{"out_person_id": "p000", "in_person_id": "p001", "project_id": "j00"}]}


@pytest.fixture
def ready(monkeypatch, tmp_path):
    index = tmp_path / "index.html"
    index.write_text("x")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    replays = []
    real = report_route._replay_applied_swaps

    def counting(req, graph):
        replays.append(1)
        return real(req, graph)

    monkeypatch.setattr(report_route, "_replay_applied_swaps", counting)

    async def fake_render(payload, origin, timeout_s):
        return b"%PDF-fake"

    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    return replays


def test_replay_does_not_run_when_slots_are_full(client, ready, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_CONCURRENCY", "1")
    assert pdf.PDF_SLOTS.try_acquire(1)
    try:
        res = client.post("/api/report", json=_BASE)
        assert res.status_code == 429
        assert ready == []                         # 슬롯 없이 무거운 계산을 시작하지 않았다
    finally:
        pdf.PDF_SLOTS.release()
    assert client.post("/api/report", json=_BASE).status_code == 200
    assert ready == [1]
    assert pdf.PDF_SLOTS.active == 0


def test_stale_dataset_is_rejected_before_taking_a_slot(client, ready, monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_CONCURRENCY", "1")
    assert pdf.PDF_SLOTS.try_acquire(1)
    try:
        res = client.post("/api/report", json={**_BASE, "dataset_version": "0" * 64})
        assert res.status_code == 409                  # 슬롯이 꽉 차 있어도 409가 먼저
    finally:
        pdf.PDF_SLOTS.release()


def test_slow_replay_counts_toward_the_time_limit_and_holds_the_slot(client, ready, monkeypatch):
    """시간 초과로 응답은 504로 끝나도, 파이썬 스레드는 멈출 수 없으므로 슬롯은 그 스레드가
    실제로 끝날 때 반납된다 -- 그 사이 새 요청은 429다(동시 계산 수가 상한을 넘지 않는다)."""
    monkeypatch.setenv("TEAMWEAVER_PDF_TIMEOUT_S", "0.3")
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_CONCURRENCY", "1")

    def slow(req, graph):
        time.sleep(1.5)
        return {"applied_swaps": [], "applied_violations": []}

    monkeypatch.setattr(report_route, "_replay_applied_swaps", slow)
    res = client.post("/api/report", json=_BASE)
    assert res.status_code == 504
    assert pdf.PDF_SLOTS.active == 1                       # 스레드가 아직 돈다
    assert client.post("/api/report", json=_BASE).status_code == 429
    deadline = time.monotonic() + 5
    while pdf.PDF_SLOTS.active and time.monotonic() < deadline:
        client.get("/api/admin")                           # 이벤트 루프가 콜백을 돌리게 한다
        time.sleep(0.05)
    assert pdf.PDF_SLOTS.active == 0


def test_invalid_swap_in_replay_is_422_not_500(client, ready):
    bad = {**_BASE, "applied_swaps": [{"out_person_id": "p050", "in_person_id": "p001",
                                       "project_id": "j00"}]}
    res = client.post("/api/report", json=bad)
    assert res.status_code == 422
    assert pdf.PDF_SLOTS.active == 0


def test_final_payload_size_is_capped(client, ready, monkeypatch):
    """서버가 덧붙인 meta·재계산까지 포함한 최종 PDF 데이터 크기를 잰다."""
    monkeypatch.setattr(report_route, "MAX_RENDER_PAYLOAD_BYTES", 2_000)
    res = client.post("/api/report", json={**_BASE, "unfilled": ["x" * 3_000]})
    assert res.status_code == 413
    assert pdf.PDF_SLOTS.active == 0
