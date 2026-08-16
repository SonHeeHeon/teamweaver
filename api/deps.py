import sqlite3
from fastapi import Request
from core.graph.memory_graph import MemoryGraph


def get_graph(request: Request) -> MemoryGraph:
    return request.app.state.graph


def get_sqlite_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.sqlite_conn
