"""FastAPI 앱. lifespan에서 동결 fixture -> MemoryGraph 재수화 + SQLite 연결을
한 번 준비한다. Plan 3 실측(exp5_persistence_primed.json)으로 이 콜드 스타트는
n=100에서 수 ms, n=1000에서도 수십 ms이므로 부팅 지연으로 문제되지 않는다."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.admin import warn_if_unprotected
from api.cache import ResultCache
from api.datasets import ActiveDataset, DatasetStore, build_active, dir_version
from api.routes.datasets import validate_and_build
from api.plan_edits import PlanEditStore
from api.storage import data_dir
from api.settings import SettingsStore, default_settings_path
from core.config import FIXTURES_DIR, load_env
from core.datagen.fixtures_io import load_fixtures
from core.optimize.alternatives import cacheable, generate_plans
from core.scoring.engine import ScoringEngine
from api.routes import admin, datasets, meta, optimize, plans, report, settings, whatif


log = logging.getLogger(__name__)
# fixture 데이터셋 버전에 들어가는 파일(계산에 쓰이는 것만). pricing·meta는 계산 입력이 아니다.
FIXTURE_DATA_FILES = ("people.json", "projects.json", "coworks.json", "reviews_ko.json",
                      "parsed_reviews.json")
WARM_TIME_LIMIT_MAX = 120
WARM_GAP_MIN = 0.05


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
    warn_if_unprotected()
    # 활성 데이터셋(K9): graph·SQLite(메모리)·식별 정보를 한 객체로 두고 업로드 때 통째로 바꾼다.
    def build_fixture_dataset() -> ActiveDataset:
        ds, parsed = load_fixtures(FIXTURES_DIR)
        return build_active(ds, parsed, dataset_id="fixture-demo-100x20",
                            version=dir_version(FIXTURES_DIR, list(FIXTURE_DATA_FILES)),
                            source="fixture", synthetic=True)

    app.state.build_fixture_dataset = build_fixture_dataset
    # 업로드해 둔 데이터가 있으면 그것으로 뜬다(K13). 같은 검증·변환을 다시 거치고, 내용
    # 해시가 저장 당시 버전과 다르면 거부한다. 실패하면 기본 데이터로 뜨고 이유를 화면에 알린다.
    store = DatasetStore(data_dir())
    app.state.dataset_store = store
    app.state.plan_edit_store = PlanEditStore(data_dir())
    app.state.dataset_restore_error = None
    restored = None
    try:
        saved = store.load()
        if saved is not None:
            pointer, blob = saved
            active, report, archive_error = validate_and_build(blob)
            if active is None:
                raise ValueError(archive_error or "저장된 묶음이 지금 검증을 통과하지 못한다: "
                                 + "; ".join(e["message"] for e in (report or {}).get("errors", [])[:3]))
            if active.info.version != pointer["version"]:
                raise ValueError("저장된 묶음의 내용 해시가 저장 당시와 다르다")
            restored = active
    except Exception as exc:                        # noqa: BLE001
        # 어떤 이유로 실패해도(로더 계약 변경으로 옛 묶음이 새 코드에서 터지는 경우 포함) 서버는
        # 기본 데이터로 뜬다 -- 저장본이 남아 있어 재기동마다 죽는 일을 막는다(Opus 리뷰 S4).
        log.error("업로드 데이터 복원 실패, 기본 데이터로 시작한다: %s", exc)
        app.state.dataset_restore_error = str(exc)
    app.state.dataset = restored or build_fixture_dataset()
    app.state.dataset_lock = asyncio.Lock()
    app.state.dataset_switching = False
    graph = app.state.dataset.graph

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
        warm_outcome: dict = {}
        try:
            default_plans = generate_plans(graph, eng.skill_matrix({}), eng.synergy_matrix(),
                                           warm_params, n_alternatives=3, outcome=warm_outcome)
        except Exception:                                   # noqa: BLE001
            # Plan A가 검증에 거절되는 설정·데이터(C0)라도 서버는 떠야 한다 -- 설정과 업로드는
            # 저장되므로, 여기서 예외를 올리면 재기동할 때마다 같은 이유로 부팅이 막힌다.
            log.warning("부팅 사전계산 실패 -- 캐시 없이 시작한다", exc_info=True)
        else:
            if cacheable(warm_outcome):
                cache.put(ResultCache.key({}, warm_params, 3, app.state.dataset.info.version),
                          default_plans)

    yield

    app.state.dataset.retire()


app = FastAPI(title="TeamWeaver API", lifespan=lifespan)
# /api/report 본문 크기 상한(K4). JSON 파싱 전에 끊어야 의미가 있으므로 미들웨어다.
# CORS보다 *먼저* 등록해 안쪽에 둔다(add_middleware는 나중 것이 바깥) -- 그래야
# 상한 초과 413에도 CORS 헤더가 붙어 dev(:5173)에서 안내 문구를 읽을 수 있다.
app.add_middleware(report.ReportBodyLimit)
# Vite dev 서버(:5173)가 API(:8000)를 부를 수 있어야 한다. 프로덕션 빌드는
# FastAPI가 직접 서빙하므로(Task 8) same-origin이라 CORS가 필요 없지만,
# 개발 중에는 포트가 갈리므로 필요하다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    # 관리자 세션 쿠키(K14)를 dev(:5173 → :8000)에서도 보내려면 필요하다. 허용 origin은 위의
    # 명시 목록뿐이라 다른 사이트에는 쿠키가 붙은 응답을 읽게 해 주지 않는다.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(meta.router)
app.include_router(optimize.router)
app.include_router(whatif.router)
app.include_router(report.router)
app.include_router(settings.router)
app.include_router(datasets.router)
app.include_router(admin.router)
app.include_router(plans.router)

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
