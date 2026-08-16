import sqlite3
from fastapi import Request
from core.graph.memory_graph import MemoryGraph
from api.cache import ResultCache


def get_graph(request: Request) -> MemoryGraph:
    return request.app.state.graph


def get_sqlite_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.sqlite_conn


def get_cache(request: Request) -> ResultCache:
    return request.app.state.cache
