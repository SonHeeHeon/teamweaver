"""계산 멈추기(POST /api/runs/stop)와 화면 닫힘 중단(2026-10-12 사용자 요청).

TestClient는 응답을 끝까지 만든 뒤에 돌려주므로(스트리밍 도중에 끼어들 수 없다) 스트림 중 멈춤은 ASGI를 직접 부른다."""
import asyncio
import json
import threading
import time

import anyio
import pytest

from api import runs
from api.main import app
from core.optimize.interrupt import SolveCancelled, SolveStop, current


def _wait_for_stop(timeout: float = 10.0):
    stop = current()
    assert stop is not None, "풀이 스레드에 멈춤 신호가 걸려 있어야 한다"
    deadline = time.monotonic() + timeout
    while not stop.is_set():
        assert time.monotonic() < deadline, "멈춤이 오지 않았다"
        time.sleep(0.01)
    return stop


def _asgi_stream(path: str, body: dict, *, on_event=None, disconnect_after_start: bool = False) -> list:
    """ASGI로 직접 부르고 SSE 이벤트를 받는 대로 on_event(kind, data)에 넘긴다(스트리밍 도중 개입용)."""
    raw = json.dumps(body).encode()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": b"", "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]}
    events, buf = [], [""]

    async def main():
        started, finished = asyncio.Event(), asyncio.Event()
        sent_body = [False]

        async def receive():
            if not sent_body[0]:
                sent_body[0] = True
                return {"type": "http.request", "body": raw, "more_body": False}
            if disconnect_after_start:
                await started.wait()
            else:
                await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] != "http.response.body":
                return
            buf[0] += message.get("body", b"").decode()
            while "\n\n" in buf[0]:
                block, buf[0] = buf[0].split("\n\n", 1)
                lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
                kind, data = lines.get("event"), json.loads(lines.get("data", "{}"))
                events.append((kind, data))
                if kind == "start":
                    started.set()
                if on_event is not None:
                    await anyio.to_thread.run_sync(on_event, kind, data)
            if not message.get("more_body", False):
                finished.set()

        try:
            await app(scope, receive, send)
        except Exception:                                   # noqa: BLE001 -- 끊김이 어떤 모양으로 올라오든
            pass
    asyncio.run(main())
    return events


def test_user_stop_ends_the_run_as_stopped_not_failed(small_graph_client, monkeypatch):
    seen = {}

    def plans(*args, **kwargs):
        stop = _wait_for_stop()
        seen["mode"] = stop.mode
        raise SolveCancelled("다음 풀이를 시작하지 않았다")          # HighsPortfolio가 멈춤 뒤 새 풀이에서 하는 일
        yield  # pragma: no cover

    def on_event(kind, data):
        if kind == "start":
            assert runs.request_stop(app, data["run_id"]) is True    # 화면의 "계산 멈추기"

    monkeypatch.setattr("api.routes.optimize.generate_plans_streaming", plans)
    events = _asgi_stream("/api/optimize", {"weights": {}, "fresh": True}, on_event=on_event)
    assert seen["mode"] == "finish"
    assert [k for k, _ in events] == ["start", "done"] and events[-1][1]["stop_reason"] == "user_stopped"
    assert runs.request_stop(app, events[0][1]["run_id"]) is False   # 끝난 계산은 등록에서 빠진다


def test_stop_endpoint_reaches_a_registered_run(client):
    stop = SolveStop()
    run_id = runs.register(client.app, stop)
    try:
        res = client.post("/api/runs/stop", json={"run_id": run_id})
        assert res.status_code == 200 and res.json()["stopping"] is True
        assert stop.is_set() and stop.mode == "finish"
    finally:
        runs.unregister(client.app, run_id)
    assert client.post("/api/runs/stop", json={"run_id": run_id}).status_code == 404
    assert client.post("/api/runs/stop", json={"run_id": "x"}).status_code == 422


def test_closing_the_screen_aborts_the_solve_in_progress(small_graph_client, monkeypatch):
    """끊김은 abort -- 지금 푸는 HiGHS까지 버린다(다음 대안만 막던 예전과 달리)."""
    seen, done = {}, threading.Event()

    def plans(*args, **kwargs):
        try:
            seen["mode"] = _wait_for_stop().mode
        finally:
            done.set()
        raise SolveCancelled("abort")
        yield  # pragma: no cover

    monkeypatch.setattr("api.routes.optimize.generate_plans_streaming", plans)
    events = _asgi_stream("/api/optimize", {"weights": {}, "fresh": True}, disconnect_after_start=True)
    assert events[0][0] == "start"
    assert done.wait(5) and seen["mode"] == "abort"


def test_operating_compare_can_be_stopped_after_a_row(op_client, monkeypatch):
    def compare(graph, S, C, params, current_rows, ks, on_row=None):
        on_row({"k": 0, "accepted": False, "quality": 1.0})
        _wait_for_stop()
        raise SolveCancelled("다음 K를 시작하지 않았다")

    run = {}

    def on_event(kind, data):
        if kind == "start":
            run["id"] = data["run_id"]
        if kind == "row":
            assert runs.request_stop(app, run["id"]) is True

    monkeypatch.setattr("core.evaluate.operating.compare_move_budgets", compare)
    v = op_client.get("/api/operating/state").json()["dataset_version"]
    events = _asgi_stream("/api/operating/compare", {"dataset_version": v, "ks": [0, 1], "fresh": True},
                          on_event=on_event)
    kinds = [k for k, _ in events if k != "progress"]
    assert kinds == ["start", "row", "done"] and events[-1][1]["stopped"] is True


def test_operating_compare_disconnect_aborts(op_client, monkeypatch):
    seen, done = {}, threading.Event()

    def compare(graph, S, C, params, current_rows, ks, on_row=None):
        try:
            seen["mode"] = _wait_for_stop().mode
        finally:
            done.set()
        raise SolveCancelled("abort")

    monkeypatch.setattr("core.evaluate.operating.compare_move_budgets", compare)
    v = op_client.get("/api/operating/state").json()["dataset_version"]
    _asgi_stream("/api/operating/compare", {"dataset_version": v, "ks": [0, 1], "fresh": True},
                 disconnect_after_start=True)
    assert done.wait(5) and seen["mode"] == "abort"


@pytest.fixture
def op_client(client):
    from tests.api.test_operating import _operating_dataset
    old = client.app.state.dataset
    client.app.state.dataset = _operating_dataset()
    old.retire()
    return client
