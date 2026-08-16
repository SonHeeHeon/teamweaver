"""동기(블로킹) 제너레이터를 비동기 SSE 스트림으로 브리지한다.

PuLP/CBC는 블로킹 서브프로세스 호출이라 async 핸들러에서 직접 순회하면 7초
동안 이벤트 루프 전체가 멎어 다른 요청(/api/meta 등)도 막힌다. 별도 스레드에서
제너레이터를 돌리고, 스레드 안전 큐로 값을 이벤트 루프에 넘긴다."""
import json
import queue
import threading
from typing import AsyncIterator, Callable, Iterator

import anyio


async def stream_sync_generator(gen_fn: Callable[..., Iterator], *args, **kwargs) -> AsyncIterator:
    q: "queue.Queue" = queue.Queue()

    def _worker():
        try:
            for item in gen_fn(*args, **kwargs):
                q.put(("item", item))
        except Exception as exc:                        # noqa: BLE001 -- SSE 에러 이벤트로 재전달
            q.put(("error", exc))
        finally:
            q.put(("done", None))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    while True:
        kind, payload = await anyio.to_thread.run_sync(q.get)
        if kind == "item":
            yield payload
        elif kind == "error":
            raise payload
        else:
            return


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
