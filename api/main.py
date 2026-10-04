"""FastAPI 앱. lifespan에서 동결 fixture -> MemoryGraph 재수화 + SQLite 연결을
한 번 준비한다. Plan 3 실측(exp5_persistence_primed.json)으로 이 콜드 스타트는
n=100에서 수 ms, n=1000에서도 수십 ms이므로 부팅 지연으로 문제되지 않는다."""
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import os
import sqlite3
import tempfile

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.cache import ResultCache
from api.settings import SettingsStore, default_settings_path
from core.config import FIXTURES_DIR, load_env
from core.datagen.fixtures_io import load_fixtures
from core.graph.memory_graph import MemoryGraph
from core.graph.sqlite_store import build_sqlite
from core.optimize.alternatives import generate_plans
from core.scoring.engine import ScoringEngine
from api.routes import meta, optimize, report, settings, whatif


log = logging.getLogger(__name__)
WARM_TIME_LIMIT_MAX = 120
WARM_GAP_MIN = 0.05


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
    # 관리자 배치 설정(K8). 사전계산도 이 설정으로 한다 -- 웹이 같은 설정을
    # milp_params로 보내므로 기본 화면의 첫 실행이 캐시에 맞는다.
    store = SettingsStore(default_settings_path())
    app.state.settings_store = store
    warm_params = store.current().settings.to_milp_params()
    # 사전계산은 서버 기동을 막는다. 시간 한도가 크거나(최대 600초 × Plan A·대안 4회)
    # gap이 작으면(실측: gap 0·120초에서 부팅 377초) 재기동이 오래 멈춘다 -- 기본값
    # 수준(120초 이하, gap 5% 이상)일 때만 한다(기본 설정 실측 ~34초).
    warm_ok = (warm_params.time_limit <= WARM_TIME_LIMIT_MAX
               and warm_params.gap >= WARM_GAP_MIN)
    # 데모 기본 시나리오(가중치 없음) 사전 계산 -- 부팅 시 1회, 요청 경로 밖.
    # 실측(exp3_algorithm.json, n=100/20): Plan A+B+C+D 전체 약 28.5초. 실제
    # 서버 기동에서는 항상 돈다(사전 캐시+fallback 요구사항). 테스트 스위트가
    # TestClient(app)를 만들 때마다 이 비용을 내지 않도록 환경변수로 끌 수 있게
    # 한다 -- tests/api/conftest.py의 `_skip_warm` autouse 픽스처가 기본으로
    # 이 값을 "1"로 세팅하고, Task 5의 slow-marked 테스트만 명시적으로 해제한다.
    if os.environ.get("TEAMWEAVER_SKIP_WARM") != "1" and not warm_ok:
        log.warning("배치 설정(time_limit=%ss, gap=%s)이 사전계산 한도(%ss 이하, gap %s 이상)"
                    "를 벗어나 부팅 사전계산을 생략한다", warm_params.time_limit, warm_params.gap,
                    WARM_TIME_LIMIT_MAX, WARM_GAP_MIN)
    elif os.environ.get("TEAMWEAVER_SKIP_WARM") != "1":
        eng = ScoringEngine(graph)
        default_plans = generate_plans(graph, eng.skill_matrix({}), eng.synergy_matrix(),
                                       warm_params, n_alternatives=3)
        cache.put(ResultCache.key({}, warm_params, 3), default_plans)

    yield

    app.state.sqlite_conn.close()


app = FastAPI(title="TeamWeaver API", lifespan=lifespan)
# Vite dev 서버(:5173)가 API(:8000)를 부를 수 있어야 한다. 프로덕션 빌드는
# FastAPI가 직접 서빙하므로(Task 8) same-origin이라 CORS가 필요 없지만,
# 개발 중에는 포트가 갈리므로 필요하다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(meta.router)
app.include_router(optimize.router)
app.include_router(whatif.router)
app.include_router(report.router)
app.include_router(settings.router)

# 빌드 산출물이 있으면 SPA를 같은 오리진에서 서빙한다. API 라우터를 모두
# 등록한 *뒤에* 마운트해야 "/"가 API 경로를 가리지 않는다.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.is_dir():

    @app.get("/report", include_in_schema=False)
    async def _report_page() -> FileResponse:
        """클라이언트 라우트 /report를 index.html로 되돌려준다.

        StaticFiles(html=True)는 SPA 폴백을 하지 않는다 -- 소스를 보면
        html=True는 (1) *디렉터리* URL에 index.html을 주고 (2) 그 외
        못 찾은 경로에는 404.html을 status 404로 줄 뿐이다. 그래서 /report는
        마운트만으로는 404가 된다. Playwright가 바로 이 경로로 들어오므로
        (api/pdf.py) 명시적으로 라우트를 판다. 마운트보다 *먼저* 등록해야
        "/" 마운트에 먹히지 않는다.
        """
        return FileResponse(_DIST / "index.html")

    app.mount("/", StaticFiles(directory=_DIST, html=True), name="spa")
