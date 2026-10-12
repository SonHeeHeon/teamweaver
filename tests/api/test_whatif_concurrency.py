"""교체 검토를 동시에 보내도 500이 나지 않는다(claude-a 요청 2026-10-12 (1) SQLite 연결 동시 사용).

데이터셋 SQLite 연결 하나를 스레드풀 라우트들이 함께 쓰면 `sqlite3.InterfaceError: bad parameter or other API misuse`가 났다
(TestClient 동시 5건에서 재현). 근거 조회를 잠금으로 직렬화한다."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api.deps import get_evidence, get_graph, get_openai_client_or_none, get_sqlite_conn
from api.main import app
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.graph.sqlite_store import build_sqlite


@pytest.fixture
def shared_conn_client(tmp_path):
    ds = generate_dataset(15, 3, seed=1)
    parsed = parse_reviews_rule_based(ds.reviews)
    graph = MemoryGraph.build(ds, parsed)
    build_sqlite(ds, parsed, tmp_path / "small.db")
    # 서비스와 같은 모양: 메모리 DB 하나를 모든 요청이 함께 쓴다(api.datasets.build_active)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    src = sqlite3.connect(tmp_path / "small.db")
    src.backup(conn)
    src.close()
    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[get_sqlite_conn] = lambda: conn
    app.dependency_overrides[get_evidence] = lambda: None
    app.dependency_overrides[get_openai_client_or_none] = lambda: None     # 규칙 설명 -- 근거 조회만 겹치게
    with TestClient(app) as c:
        yield c, ds
    app.dependency_overrides.clear()
    conn.close()


def test_concurrent_whatif_requests_share_the_sqlite_connection_safely(shared_conn_client):
    client, ds = shared_conn_client
    project = ds.projects[0]
    by_grade: dict = {}
    for p in ds.people:
        by_grade.setdefault(p.grade, []).append(p.id)
    grade, ids = next((g, v) for g, v in by_grade.items() if len(v) >= 3)
    pairs = [(a, b) for a in ids for b in ids if a != b][:12]

    def one(pair):
        out, inn = pair
        return client.post("/api/whatif", json={
            "entries": [{"person_id": out, "project_id": project.id, "alloc": 0.5}],
            "swap": {"out_person_id": out, "in_person_id": inn, "project_id": project.id}, "weights": {}}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(one, pairs * 4))
    assert codes == [200] * len(codes)
