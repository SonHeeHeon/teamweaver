"""FastAPI 앱. lifespan에서 동결 fixture -> MemoryGraph 재수화 + SQLite 연결을
한 번 준비한다. Plan 3 실측(exp5_persistence_primed.json)으로 이 콜드 스타트는
n=100에서 수 ms, n=1000에서도 수십 ms이므로 부팅 지연으로 문제되지 않는다."""
from contextlib import asynccontextmanager
from pathlib import Path
import os
import sqlite3
import tempfile

from fastapi import FastAPI

from api.cache import ResultCache
from core.config import FIXTURES_DIR, load_env
from core.datagen.fixtures_io import load_fixtures
from core.graph.memory_graph import MemoryGraph
from core.graph.sqlite_store import build_sqlite
from core.optimize.alternatives import generate_plans
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine
from api.routes import meta, optimize, whatif


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
    ds, parsed = load_fixtures(FIXTURES_DIR)
    graph = MemoryGraph.build(ds, parsed)
    app.state.graph = graph

    tmpdir = tempfile.mkdtemp(prefix="teamweaver-api-")
    db_path = Path(tmpdir) / "meta.db"
    build_sqlite(ds, parsed, db_path)
    app.state.sqlite_conn = sqlite3.connect(db_path, check_same_thread=False)

    cache = ResultCache()
    app.state.cache = cache
    # 데모 기본 시나리오(가중치 없음) 사전 계산 -- 부팅 시 1회, 요청 경로 밖.
    # 실측(exp3_algorithm.json, n=100/20): Plan A+B+C+D 전체 약 28.5초. 실제
    # 서버 기동에서는 항상 돈다(사전 캐시+fallback 요구사항). 테스트 스위트가
    # TestClient(app)를 만들 때마다 이 비용을 내지 않도록 환경변수로 끌 수 있게
    # 한다 -- tests/api/conftest.py의 `_skip_warm` autouse 픽스처가 기본으로
    # 이 값을 "1"로 세팅하고, Task 5의 slow-marked 테스트만 명시적으로 해제한다.
    if os.environ.get("TEAMWEAVER_SKIP_WARM") != "1":
        eng = ScoringEngine(graph)
        default_plans = generate_plans(graph, eng.skill_matrix({}), eng.synergy_matrix(),
                                       MilpParams(), n_alternatives=3)
        cache.put(ResultCache.key({}, {}, 3), default_plans)

    yield

    app.state.sqlite_conn.close()


app = FastAPI(title="TeamWeaver API", lifespan=lifespan)
app.include_router(meta.router)
app.include_router(optimize.router)
app.include_router(whatif.router)
