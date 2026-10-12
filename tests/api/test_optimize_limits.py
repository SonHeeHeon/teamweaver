"""/api/optimize 동시 실행·시간 상한·끊김 처리(claude-a 점검 2026-10-12 (3)).

예전: 익명 요청 40개(time_limit 3600, 대안 6, fresh)면 스레드풀이 묶여 /api/meta가 9초 넘게 막혔고, 연결을 끊어도 풀이가 끝까지 돌았다."""
import asyncio
import json
import threading
import time

import anyio
import pytest

from api.main import app
from api.routes.optimize import solve_slots
from api.sse import SyncStream


@pytest.fixture
def one_slot():
    app.state.optimize_slots = threading.BoundedSemaphore(1)
    try:
        yield app.state.optimize_slots
    finally:
        del app.state.optimize_slots


def _events(res) -> list[str]:
    return [line[len("event:"):].strip() for line in res.iter_lines() if line.startswith("event:")]


def test_busy_slots_refuse_new_solves_with_429_but_cached_results_still_stream(small_graph_client, one_slot):
    body = {"weights": {}, "n_alternatives": 0}
    with small_graph_client.stream("POST", "/api/optimize", json=body) as res:      # 캐시를 채운다
        assert res.status_code == 200 and _events(res)[-1] == "done"
    assert one_slot.acquire(blocking=False)                                            # 다른 계산이 슬롯을 쥐고 있다
    try:
        busy = small_graph_client.post("/api/optimize", json={**body, "fresh": True})
        assert busy.status_code == 429 and "다른 최적화 계산" in busy.json()["detail"]
        with small_graph_client.stream("POST", "/api/optimize", json=body) as res:  # 캐시 결과는 풀지 않으니 슬롯이 필요 없다
            assert res.status_code == 200 and _events(res)[-1] == "done"
    finally:
        one_slot.release()


def test_the_slot_comes_back_when_the_solve_fails(small_graph_client, one_slot, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated solver failure")
        yield  # pragma: no cover

    monkeypatch.setattr("api.routes.optimize.generate_plans_streaming", boom)
    with small_graph_client.stream("POST", "/api/optimize", json={"weights": {}, "fresh": True}) as res:
        assert "error" in _events(res)
    deadline = time.monotonic() + 5
    while not one_slot.acquire(blocking=False):                                         # 풀이 스레드가 끝나며 놓는다
        assert time.monotonic() < deadline, "슬롯이 반납되지 않았다"
        time.sleep(0.01)
    one_slot.release()


@pytest.mark.parametrize("params", [{"time_limit": 901}, {"max_pairs": 5001}, {"slack_penalty": 10_001.0}])
def test_runaway_solver_parameters_are_refused(small_graph_client, params):
    res = small_graph_client.post("/api/optimize", json={"weights": {}, "milp_params": params})
    assert res.status_code == 422


def test_solve_slots_default_and_env(monkeypatch):
    class _App:
        state = type("S", (), {})()
    monkeypatch.setenv("TEAMWEAVER_OPTIMIZE_SLOTS", "3")
    sem = solve_slots(_App)
    assert [sem.acquire(blocking=False) for _ in range(4)] == [True, True, True, False]
    assert solve_slots(_App) is sem                                                     # 앱 하나에 하나


def test_a_closed_stream_does_not_start_the_next_item():
    """화면이 끊으면(응답 제너레이터가 닫히면) 지금 만드는 값까지만 만들고 다음 값(다음 대안)은 시작하지 않는다."""
    gate, closed, exited = threading.Event(), threading.Event(), threading.Event()
    started: list[int] = []

    def plans():
        try:
            for i in range(5):
                started.append(i)
                if i == 1:
                    gate.wait(5)                        # 두 번째 대안을 푸는 중에 화면이 끊긴다
                yield i
        finally:
            closed.set()

    async def main():
        stream = SyncStream(plans, on_exit=exited.set)
        agen = stream.items()
        assert await agen.__anext__() == 0
        await agen.aclose()                             # 끊김
        gate.set()
        assert await anyio.to_thread.run_sync(lambda: exited.wait(5))
    asyncio.run(main())
    assert closed.is_set() and started[:1] == [0] and 2 not in started   # 끊긴 시점에 따라 [0] 또는 [0, 1]


def test_waiting_streams_do_not_hold_worker_threads():
    """기다리는 스트림이 스레드풀 작업자를 쥐지 않는다 -- 예전엔 요청마다 작업자 하나를 쥐어 40건이면 /api/meta가 막혔다."""
    release = threading.Event()

    def slow():
        release.wait(10)
        yield 1

    async def main():
        streams = [SyncStream(slow) for _ in range(60)]                     # 기본 작업자 한도(40)보다 많이
        readers = [asyncio.ensure_future(_drain(s)) for s in streams]
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        assert await asyncio.wait_for(anyio.to_thread.run_sync(lambda: "ok"), timeout=2) == "ok"
        took = time.monotonic() - t0
        release.set()
        assert [await r for r in readers] == [[1]] * 60
        return took
    assert asyncio.run(main()) < 1.0


async def _drain(stream):
    return [x async for x in stream.items()]


def test_optimize_stream_payload_shape_is_unchanged(small_graph_client):
    with small_graph_client.stream("POST", "/api/optimize", json={"weights": {}, "n_alternatives": 1}) as res:
        data = [json.loads(line[5:]) for line in res.iter_lines() if line.startswith("data:")]
    assert "run_id" in data[0]                                                   # 새 풀이는 start(실행 번호)부터
    assert data[1]["label"] == "A" and "plan_token" in data[1] and "count" in data[-1]


def test_a_client_gone_before_the_headers_still_stops_the_next_alternative(small_graph_client, one_slot, monkeypatch):
    """헤더를 보내기 전에 연결이 끊기면 스트림 제너레이터가 시작조차 안 한다 -- 그래도 다음 대안은 풀지 않는다
    (Codex 리뷰 2026-10-12 재현: 예전엔 끊긴 뒤 네 대안을 모두 풀었다)."""
    gate, started = threading.Event(), []

    def plans(*args, **kwargs):
        for i in range(4):
            started.append(i)
            if i == 1:
                gate.wait(5)
            yield i

    monkeypatch.setattr("api.routes.optimize.generate_plans_streaming", plans)
    body = json.dumps({"weights": {}, "fresh": True}).encode()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/api/optimize", "raw_path": b"/api/optimize",
             "root_path": "", "query_string": b"", "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]}
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            raise OSError("client went away")               # 헤더를 보내는 순간 끊김

    def wait_for_slot():
        deadline = time.monotonic() + 5
        while not one_slot.acquire(blocking=False):
            assert time.monotonic() < deadline, "슬롯이 반납되지 않았다"
            time.sleep(0.01)
        one_slot.release()

    async def main():
        try:
            await app(scope, receive, send)
        except Exception:                                     # noqa: BLE001 -- 끊김이 어떤 모양으로 올라오든 상관없다
            pass
        gate.set()
        # 실제 서버처럼 이벤트 루프가 살아 있는 동안 풀이가 끝나기를 기다린다(루프가 닫히면 그 자체로 멈추므로)
        await anyio.to_thread.run_sync(wait_for_slot)
    asyncio.run(main())
    assert started[:1] == [0] and 2 not in started and 3 not in started   # 풀던 대안까지만(끊긴 시점에 따라 [0] 또는 [0, 1])


@pytest.mark.parametrize("path,root", [("/api/whatif", "/a"), ("/a/api/whatif", "/a"), ("/tw/api/report", "/tw")])
def test_body_limit_holds_whether_or_not_the_proxy_kept_the_prefix(monkeypatch, path, root):
    """root_path는 경로 구간 경계에서만 뗀다 -- 예전엔 root "/a" + path "/api/whatif"가 "pi/whatif"가 되어 상한을 건너뛰었다."""
    from api.routes.report import ReportBodyLimit
    monkeypatch.setenv("TEAMWEAVER_MAX_JSON_BODY_BYTES", "100")
    monkeypatch.setenv("TEAMWEAVER_PDF_MAX_BODY_BYTES", "100")
    sent = []

    async def inner(scope, receive, send):
        raise AssertionError("상한 초과 요청이 앱까지 들어갔다")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": path, "root_path": root, "method": "POST", "headers": [(b"content-length", b"5000")]}
    asyncio.run(ReportBodyLimit(inner)(scope, None, send))
    assert sent[0]["status"] == 413
