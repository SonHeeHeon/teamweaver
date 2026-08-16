"""FastAPI 앱. lifespan에서 동결 fixture -> MemoryGraph 재수화 + SQLite 연결을
한 번 준비한다. Plan 3 실측(exp5_persistence_primed.json)으로 이 콜드 스타트는
n=100에서 수 ms, n=1000에서도 수십 ms이므로 부팅 지연으로 문제되지 않는다."""
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
import tempfile

from fastapi import FastAPI

from core.config import FIXTURES_DIR, load_env
from core.datagen.fixtures_io import load_fixtures
from core.graph.memory_graph import MemoryGraph
from core.graph.sqlite_store import build_sqlite
from api.routes import meta, optimize


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
    ds, parsed = load_fixtures(FIXTURES_DIR)
    app.state.graph = MemoryGraph.build(ds, parsed)

    tmpdir = tempfile.mkdtemp(prefix="teamweaver-api-")
    db_path = Path(tmpdir) / "meta.db"
    build_sqlite(ds, parsed, db_path)
    app.state.sqlite_conn = sqlite3.connect(db_path, check_same_thread=False)

    yield

    app.state.sqlite_conn.close()


app = FastAPI(title="TeamWeaver API", lifespan=lifespan)
app.include_router(meta.router)
app.include_router(optimize.router)
