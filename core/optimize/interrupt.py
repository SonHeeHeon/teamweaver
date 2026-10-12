"""풀이 중단 신호(2026-10-12, 사용자 요청 "화면 닫으면 중간에 끊기도록 · 사용자가 계산 멈추는 기능").

호출한 스레드에 `watching(stop)`으로 걸어 두면, 그 스레드에서 도는 HiGHS 풀이(`highs_portfolio.HighsPortfolio`)가 확인한다:
- 풀이를 **시작할 때** 이미 멈춤이면 `SolveCancelled` -- 새 풀이를 시작하지 않는다. 그래서 대안 생성기·운영 중 K 비교는
  고치지 않아도 "다음 대안·다음 K를 풀지 않음"이 된다(둘 다 RuntimeError만 잡으므로 이 예외는 삼켜지지 않는다).
- 풀이 **중에** 멈춤이면 HiGHS를 중단한다. 모드 `finish`(사용자의 "계산 멈추기")는 그때까지 찾은 해를 돌려주고(종료 사유
  `user_interrupt`, 독립 검증은 그대로), `abort`(화면이 닫힘 -- 받을 사람이 없다)는 해를 버리고 `SolveCancelled`.

목적식·제약은 바꾸지 않는다 -- 언제 멈추느냐만 정한다."""
from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from typing import Iterator, Literal

Mode = Literal["finish", "abort"]


class SolveCancelled(Exception):
    """멈춤으로 풀이를 시작하지 않았거나 버렸다. RuntimeError가 아니다 -- 대안 실패·운영 실패로 바뀌지 않게."""


class SolveStop:
    """한 요청의 멈춤 신호. 여러 번 요청해도 되고, abort가 finish보다 강하다(한번 abort면 abort)."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._mode: Mode | None = None

    def request(self, mode: Mode = "finish") -> None:
        with self._lock:
            if self._mode != "abort":
                self._mode = mode
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def mode(self) -> Mode | None:
        return self._mode

    @property
    def aborting(self) -> bool:
        return self._mode == "abort"


_current: contextvars.ContextVar[SolveStop | None] = contextvars.ContextVar("solve_stop", default=None)


@contextmanager
def watching(stop: SolveStop | None) -> Iterator[None]:
    """이 스레드(문맥)에서 도는 풀이가 stop을 보게 한다."""
    token = _current.set(stop)
    try:
        yield
    finally:
        _current.reset(token)


def current() -> SolveStop | None:
    return _current.get()
