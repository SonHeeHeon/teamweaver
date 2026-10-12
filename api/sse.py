"""동기(블로킹) 제너레이터를 비동기 SSE 스트림으로 브리지한다.

PuLP/HiGHS는 블로킹 호출이라 async 핸들러에서 직접 순회하면 그동안 이벤트 루프 전체가 멎어 다른 요청(/api/meta 등)도
막힌다. 별도 스레드에서 제너레이터를 돌리고, 값은 이벤트 루프의 큐로 넘긴다."""
import asyncio
import json
import logging
import threading
from typing import AsyncIterator, Callable, Iterator

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from core.optimize.interrupt import SolveStop, watching

log = logging.getLogger(__name__)


class SyncStream:
    """동기 제너레이터를 별도 스레드에서 **바로** 돌리고 값을 이벤트 루프로 넘긴다.

    - 기다리는 동안 스레드풀 작업자를 쥐지 않는다(asyncio 큐 + call_soon_threadsafe). 예전엔 `to_thread(q.get)`이 요청마다
      작업자 하나를 끝까지 쥐어, 최적화 요청 40개면 스레드풀이 묶여 /api/meta까지 9초 넘게 막혔다(claude-a 점검 2026-10-12).
    - 멈춤 신호 `self.stop`(core.optimize.interrupt.SolveStop)을 풀이 스레드에 걸어 둔다: `cancel("abort")`(연결 끊김 --
      `items()` 소비가 멈추면 자동)는 지금 푸는 HiGHS까지 끊고 버리며, `cancel("finish")`(사용자의 "계산 멈추기")는 그때까지의
      해를 내고 다음 값을 만들지 않는다.
    - `on_exit`는 스레드가 끝날 때 꼭 한 번 부른다(슬롯 반납·실행 등록 해제) -- 스트림을 읽든 말든. 스레드를 못 띄우면 그때 부른다.
    - `start=False`면 `start()`를 부를 때 시작한다(실행 번호를 먼저 등록하려고)."""

    def __init__(self, gen_fn: Callable[..., Iterator], *args, on_exit: Callable[[], None] | None = None,
                 start: bool = True, **kwargs):
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue = asyncio.Queue()
        self.stop = SolveStop()
        self._on_exit = on_exit
        self._thread = threading.Thread(target=self._run, args=(gen_fn, args, kwargs), daemon=True)
        if start:
            self.start()

    def start(self) -> None:
        try:
            self._thread.start()
        except BaseException:
            if self._on_exit is not None:
                self._on_exit()
            raise

    @property
    def cancelled(self) -> bool:
        return self.stop.is_set()

    def cancel(self, mode: str = "abort") -> None:
        self.stop.request(mode)

    def _put(self, kind: str, payload) -> None:
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (kind, payload))
        except RuntimeError:                            # 이벤트 루프가 이미 닫혔다(서버 종료) -- 받을 쪽이 없다
            self.stop.request("abort")

    def _run(self, gen_fn, args, kwargs) -> None:
        gen = None
        try:
            if self.stop.is_set():
                return
            with watching(self.stop):                   # 이 스레드의 HiGHS 풀이가 멈춤을 본다
                gen = gen_fn(*args, **kwargs)
                for item in gen:
                    self._put("item", item)
                    if self.stop.is_set():              # 멈췄다 -- 다음 값(다음 대안)을 만들지 않는다
                        break
        except Exception as exc:                        # noqa: BLE001 -- SSE error 이벤트로 재전달
            self._put("error", exc)
        finally:
            try:
                if gen is not None and hasattr(gen, "close"):
                    gen.close()                         # 제너레이터의 정리 코드를 지금 돌린다(GeneratorExit)
            except Exception:                           # noqa: BLE001
                log.warning("스트림 제너레이터 정리 중 오류", exc_info=True)
            finally:
                try:
                    if self._on_exit is not None:
                        self._on_exit()
                finally:
                    self._put("done", None)

    async def items(self) -> AsyncIterator:
        ended = False
        try:
            while True:
                kind, payload = await self._queue.get()
                if kind == "item":
                    yield payload
                elif kind == "error":
                    ended = True
                    raise payload
                else:
                    ended = True
                    return
        finally:
            if not ended:                               # 끝나기 전에 소비자가 떠났다(끊김) -- 남은 풀이는 버린다
                self.stop.request("abort")              # (끝난 뒤에 abort를 걸면 사용자의 "멈춤"이 "중단"으로 덮인다)


async def stream_sync_generator(gen_fn: Callable[..., Iterator], *args, **kwargs) -> AsyncIterator:
    """SyncStream을 처음 읽을 때 시작하는 옛 모양(슬롯이 없는 곳용)."""
    async for item in SyncStream(gen_fn, *args, **kwargs).items():
        yield item


class CancellableStreamResponse(StreamingResponse):
    """응답이 어떻게 끝나든(다 보냄·끊김·헤더 전송 실패) on_close를 부르고, 보내는 중에도 연결 끊김(http.disconnect)을 직접
    지켜본다. 스트림 제너레이터의 finally만으로는 헤더 전에 끊긴 경우를 놓치고(Codex 리뷰 2026-10-12), ASGI 2.4 서버에서는
    Starlette가 끊김을 보지 않아 다음 이벤트를 보낼 때까지(긴 풀이 동안) 모른다."""

    def __init__(self, content, *, on_close: Callable[[], None], **kwargs):
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def watch() -> None:
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    self._on_close()
                    return

        watcher = asyncio.ensure_future(watch())
        try:
            await super().__call__(scope, receive, send)
        finally:
            watcher.cancel()
            self._on_close()


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
