import sqlite3
from fastapi import Request
from core.graph.memory_graph import MemoryGraph
from api.cache import ResultCache
from api.settings import SettingsStore


def get_graph(request: Request) -> MemoryGraph:
    return request.app.state.graph


def get_sqlite_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.sqlite_conn


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
