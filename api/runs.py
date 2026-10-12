"""진행 중인 계산 등록부(2026-10-12, 사용자 요청 "중간에 사용자가 계산 멈추는 기능"): 실행 번호 → 멈춤 신호.

/api/optimize·/api/operating/compare가 새로 푸는 계산을 시작하면 번호를 받아 SSE `start` 이벤트로 화면에 알리고,
화면은 `POST /api/runs/stop {run_id}`로 멈춘다(그때까지의 해를 쓰고 끝냄 -- finish). 번호는 추측할 수 없는 난수다.
계산이 끝나면 등록을 지운다(없는 번호면 404 -- 이미 끝났다)."""
from __future__ import annotations

import secrets
import threading

from core.optimize.interrupt import SolveStop

_lock = threading.Lock()


def _table(app) -> dict[str, SolveStop]:
    with _lock:
        table = getattr(app.state, "solve_runs", None)
        if table is None:
            table = app.state.solve_runs = {}
        return table


def register(app, stop: SolveStop) -> str:
    run_id = secrets.token_urlsafe(16)
    table = _table(app)
    with _lock:
        table[run_id] = stop
    return run_id


def unregister(app, run_id: str) -> None:
    table = _table(app)
    with _lock:
        table.pop(run_id, None)


def request_stop(app, run_id: str) -> bool:
    table = _table(app)
    with _lock:
        stop = table.get(run_id)
    if stop is None:
        return False
    stop.request("finish")
    return True
