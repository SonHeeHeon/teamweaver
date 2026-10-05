"""공유 API 테스트 픽스처.

`client`는 TEAMWEAVER_SKIP_WARM=1을 매 테스트 함수 시작 전에 강제한다 --
Task 5에서 lifespan에 데모 시나리오 사전 계산(부팅 시 CBC 풀이, 실측
~28.5초)이 추가되는데, FastAPI TestClient는 생성될 때마다 lifespan을 새로
돈다. 이 가드가 없으면 이 fixture를 쓰는 모든 테스트가 매번 28.5초를
내야 하고, 기본(`not slow`) 스위트가 사실상 못 쓰게 된다. Task 3 시점에는
아직 그 워밍 코드가 없으므로 이 가드는 지금은 아무 효과가 없다 -- Task 5가
lifespan에 `os.environ.get("TEAMWEAVER_SKIP_WARM") != "1"` 가드를 넣을
때 이 conftest와 맞물린다(Task 5 Step 3 참고).

`small_graph_client`는 실제 동결 fixture(100명/20프로젝트) 대신 합성 소규모
데이터셋으로 `get_graph`/`get_sqlite_conn`을 오버라이드한다 -- MILP를 실제로
푸는 테스트(Task 4의 SSE 플러밍 테스트 등)가 slow 마커 없이도 빠르게(1초
미만) 돌 수 있게 한다. FastAPI의 `app.dependency_overrides`는 앱 소스를
건드리지 않는 표준 테스트 기법이다."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.deps import get_graph, get_sqlite_conn
from api.main import app
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.graph.sqlite_store import build_sqlite


@pytest.fixture(scope="session", autouse=True)
def _session_private_dirs(tmp_path_factory):
    """모듈 단위 실서버 픽스처(live_server 등)는 함수 단위 격리보다 *먼저* 뜬다. 그때도 사용자 홈의
    ~/.teamweaver(설정·업로드 데이터·적용 교체·서명키)를 읽거나 쓰지 않게 세션 시작에 임시 폴더로
    돌린다. 함수 단위 픽스처(settings_path·data_dir)가 테스트마다 다시 덮는다."""
    import os
    base = tmp_path_factory.mktemp("teamweaver-session")
    keys = ("TEAMWEAVER_DATA_DIR", "TEAMWEAVER_SETTINGS_PATH")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["TEAMWEAVER_DATA_DIR"] = str(base / "data")
    os.environ["TEAMWEAVER_SETTINGS_PATH"] = str(base / "settings.json")
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _skip_warm(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_SKIP_WARM", "1")


@pytest.fixture(autouse=True)
def settings_path(monkeypatch, tmp_path):
    """관리자 설정 파일(K8)을 테스트마다 빈 임시 경로로 돌린다 -- 사용자 홈의
    ~/.teamweaver/settings.json을 읽거나 덮어쓰지 않게 한다."""
    path = tmp_path / "teamweaver-settings" / "settings.json"
    monkeypatch.setenv("TEAMWEAVER_SETTINGS_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    """영속 저장 폴더(K13: 업로드 데이터·적용 교체·서명키)를 테스트마다 빈 임시 폴더로 돌린다."""
    path = tmp_path / "teamweaver-data"
    monkeypatch.setenv("TEAMWEAVER_DATA_DIR", str(path))
    monkeypatch.delenv("TEAMWEAVER_PLAN_SECRET", raising=False)
    # 관리자 로그인(K14): 테스트마다 비밀번호 미설정·잠금 초기 상태에서 시작한다.
    for k in ("TEAMWEAVER_ADMIN_PASSWORD", "TEAMWEAVER_ADMIN_PASSWORD_HASH", "TEAMWEAVER_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from api.admin import THROTTLE
    THROTTLE._state.clear()
    THROTTLE._inflight.clear()
    return path


@pytest.fixture
def client():
    with TestClient(app) as c:   # lifespan 실행 -> MemoryGraph/sqlite 준비 (워밍은 스킵됨)
        yield c


@pytest.fixture
def small_graph_client(tmp_path):
    ds = generate_dataset(15, 3, seed=1)
    parsed = parse_reviews_rule_based(ds.reviews)
    graph = MemoryGraph.build(ds, parsed)
    db_path = tmp_path / "small.db"
    build_sqlite(ds, parsed, db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)

    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[get_sqlite_conn] = lambda: conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    conn.close()
