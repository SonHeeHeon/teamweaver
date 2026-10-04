import sqlite3
from fastapi import Depends, HTTPException, Request
from core.graph.memory_graph import MemoryGraph
from api.cache import ResultCache
from api.datasets import ActiveDataset
from api.settings import SettingsStore


async def get_dataset(request: Request):
    """요청을 시작할 때의 활성 데이터셋. FastAPI가 요청당 한 번만 풀므로, 같은 요청의
    graph·SQLite·버전은 업로드로 전환이 일어나도 서로 같은 데이터셋에서 나온다(K9).
    acquire/release로 사용 중임을 알려, 전환으로 물러난 데이터셋의 SQLite가 이 요청이
    끝난 뒤에 닫히게 한다.

    **async여야 한다**: 동기 의존성은 스레드풀에서 돌아, 포인터를 읽은 뒤 acquire하기
    전에 이벤트 루프의 전환(_activate)이 끼어들어 닫힌 SQLite를 넘길 수 있었다(Codex 실측).
    이벤트 루프에서 읽기와 acquire 사이에 await가 없으면 전환과 겹칠 수 없다."""
    dataset: ActiveDataset = request.app.state.dataset
    dataset.acquire()
    try:
        yield dataset
    finally:
        dataset.release()


def check_dataset_version(expected: str | None, dataset: ActiveDataset) -> None:
    """화면이 본 데이터셋(meta의 dataset_version)과 서버의 활성 데이터셋이 다르면 409.
    다른 사용자가 그사이 전환했으면, 옛 명단·이름으로 새 데이터의 결과를 섞어 보이게 된다.
    expected가 없으면(구버전 클라이언트·스크립트) 검사하지 않는다."""
    if expected is not None and expected != dataset.info.version:
        raise HTTPException(status_code=409, detail={
            "code": "dataset_changed",
            "message": "서버의 데이터셋이 바뀌었다. 화면을 새 데이터로 다시 불러올 것.",
            "active_version": dataset.info.version})


def get_graph(dataset: ActiveDataset = Depends(get_dataset)) -> MemoryGraph:
    return dataset.graph


def get_sqlite_conn(dataset: ActiveDataset = Depends(get_dataset)) -> sqlite3.Connection:
    return dataset.sqlite_conn


def get_cache(request: Request) -> ResultCache:
    return request.app.state.cache


def get_settings_store(request: Request) -> SettingsStore:
    return request.app.state.settings_store


def get_openai_client_or_none():
    """API 키가 없거나 openai 패키지 초기화가 실패하면 None -- 호출부가
    fallback으로 넘어가는 신호."""
    try:
        from openai import OpenAI
        return OpenAI()
    except Exception:                                   # noqa: BLE001
        return None
