"""동기(블로킹) 제너레이터를 비동기 SSE 스트림으로 브리지한다.

PuLP/HiGHS는 블로킹 호출이라 async 핸들러에서 직접 순회하면 그동안 이벤트 루프 전체가 멎어 다른 요청(/api/meta 등)도
막힌다. 별도 스레드에서 제너레이터를 돌리고, 값은 이벤트 루프의 큐로 넘긴다."""
import asyncio
import json
import logging
import threading
from typing import AsyncIterator, Callable, Iterator

log = logging.getLogger(__name__)


class SyncStream:
    """동기 제너레이터를 별도 스레드에서 **바로** 돌리고 값을 이벤트 루프로 넘긴다.

    - 기다리는 동안 스레드풀 작업자를 쥐지 않는다(asyncio 큐 + call_soon_threadsafe). 예전엔 `to_thread(q.get)`이 요청마다
      작업자 하나를 끝까지 쥐어, 최적화 요청 40개면 스레드풀이 묶여 /api/meta까지 9초 넘게 막혔다(claude-a 점검 2026-10-12).
    - `items()` 소비가 멈추면(연결 끊김으로 응답 제너레이터가 닫힘) 멈춤 신호를 켠다 -- 지금 만드는 값 하나는 끝까지 가고
      다음 값은 만들지 않는다(끊긴 최적화가 다음 대안까지 풀지 않게. 솔버 한 번은 중간에 끊지 못한다).
    - `on_exit`는 스레드가 끝날 때 꼭 한 번 부른다(슬롯 반납) -- 스트림을 읽든 말든. 스레드를 못 띄우면 생성자에서 부른다."""

    def __init__(self, gen_fn: Callable[..., Iterator], *args, on_exit: Callable[[], None] | None = None, **kwargs):
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._stop = threading.Event()
        self._on_exit = on_exit
        thread = threading.Thread(target=self._run, args=(gen_fn, args, kwargs), daemon=True)
        try:
            thread.start()
        except BaseException:
            if on_exit is not None:
                on_exit()
            raise

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def cancel(self) -> None:
        self._stop.set()

    def _put(self, kind: str, payload) -> None:
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (kind, payload))
        except RuntimeError:                            # 이벤트 루프가 이미 닫혔다(서버 종료) -- 받을 쪽이 없으니 다음 값도 만들지 않는다
            self._stop.set()

    def _run(self, gen_fn, args, kwargs) -> None:
        gen = None
        try:
            if self._stop.is_set():
                return
            gen = gen_fn(*args, **kwargs)
            for item in gen:
                self._put("item", item)
                if self._stop.is_set():                 # 받을 쪽이 떠났다 -- 다음 값(다음 대안)을 만들지 않는다
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
        try:
            while True:
                kind, payload = await self._queue.get()
                if kind == "item":
                    yield payload
                elif kind == "error":
                    raise payload
                else:
                    return
        finally:
            self._stop.set()


async def stream_sync_generator(gen_fn: Callable[..., Iterator], *args, **kwargs) -> AsyncIterator:
    """SyncStream을 처음 읽을 때 시작하는 옛 모양(슬롯이 없는 곳용)."""
    async for item in SyncStream(gen_fn, *args, **kwargs).items():
        yield item


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
