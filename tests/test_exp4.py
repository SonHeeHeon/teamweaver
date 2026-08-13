"""실험 4(RAG 워크로드 벤치마크) 러너의 계약 테스트.

여기서는 neo4j=False 로 돌려 SQLite 단독 경로만 검증한다 — Neo4j가 있어야만
검증되는 것(양쪽 결과 일치)은 tests/test_neo4j_rag.py의 parity 테스트와 실제
스윕 실행(`python -m experiments.bench.exp4_rag`)이 맡는다.
"""
from core.rag.queries import RAG_QUERIES
from experiments.bench import exp4_rag


def test_run_small_scale_covers_all_queries():
    out = exp4_rag.run(scales=[(100, 20)], neo4j=False)
    assert {r["backend"] for r in out["rows"]} == {"sqlite"}
    assert {r["query"] for r in out["rows"]} == set(RAG_QUERIES)
    for r in out["rows"]:
        assert r["median_ms"] > 0 and r["p95_ms"] >= r["median_ms"]


def test_neo4j_exclusion_is_recorded():
    out = exp4_rag.run(scales=[(100, 20)], neo4j=False)
    assert any("neo4j" in s["backend"] for s in out["skipped"]), \
        "제외한 백엔드는 조용히 빠지지 않고 기록돼야 한다"


def test_scales_match_plan():
    assert exp4_rag.RAG_SCALES == [(100, 20), (300, 60), (500, 100), (1000, 200)]


def test_every_query_returns_rows():
    """빈 결과를 재는 것은 워크로드를 재는 게 아니라 양쪽 백엔드의 '아무것도
    못 찾는 경로'를 재는 것이다 — 규모에 따라 0행이었다 아니었다 하면 규모
    추세 자체가 인공물이 된다. 인자 기본값이 0행으로 되돌아가면 여기서 잡는다.
    """
    out = exp4_rag.run(scales=[(100, 20)], neo4j=False)
    for r in out["rows"]:
        assert r["result_count"] > 0, \
            f"{r['query']}: n={r['n_people']}에서 0행 — 인자를 넓혀야 한다"


def test_calibration_is_recorded():
    """Neo4j는 호출마다 세션을 새로 열고 SQLite는 이미 열린 conn을 받는다 —
    한쪽에만 붙는 호출당 비용이다. 숨기거나 몰래 빼지 않고 측정해 기록한다.
    """
    out = exp4_rag.run(scales=[(100, 20)], neo4j=False)
    cal = out["calibration"]
    assert cal["sqlite_noop_ms"] > 0
    assert cal["neo4j_session_open_ms"] is None, \
        "neo4j를 안 돌렸으면 세션 오버헤드 값은 비어 있어야 한다(0이 아니라)"
