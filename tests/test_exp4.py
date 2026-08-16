"""실험 4(RAG 워크로드) 러너의 계약 테스트 -- Plan 4에서 SQLite 전용으로 축소."""
from core.rag.queries import RAG_QUERIES
from experiments.bench import exp4_rag, harness


def test_run_small_scale_covers_all_queries():
    out = exp4_rag.run(scales=[(100, 20)])
    assert {r["backend"] for r in out["rows"]} == {"sqlite"}
    assert {r["query"] for r in out["rows"]} == set(RAG_QUERIES)
    for r in out["rows"]:
        assert r["median_ms"] > 0 and r["p95_ms"] >= r["median_ms"]


def test_scales_match_plan():
    assert exp4_rag.RAG_SCALES == [(100, 20), (300, 60), (500, 100), (1000, 200)]


def test_every_query_returns_rows():
    out = exp4_rag.run(scales=[(100, 20)])
    for r in out["rows"]:
        assert r["result_count"] > 0, \
            f"{r['query']}: n={r['n_people']}에서 0행 -- 인자를 넓혀야 한다"


def test_committed_decision_artifact_untouched_by_this_runner():
    """판정 근거 exp4_rag.json은 이 축소된 러너가 절대 건드리지 않는다 --
    main()이 다른 이름(exp4_rag_sqlite_only)으로 저장하므로 존재 자체로 증명된다."""
    result = harness.load_result("exp4_rag")  # Plan 3이 커밋한 파일. 없으면 FileNotFoundError로 실패해야 정상.
    assert any(r["backend"] == "neo4j" for r in result["data"]["rows"]), \
        "커밋된 판정 아티팩트는 neo4j 행을 포함해야 한다 -- 축소된 러너로 덮어써지면 이 값이 사라진다"


def test_calibration_is_recorded():
    out = exp4_rag.run(scales=[(100, 20)])
    assert out["calibration"]["sqlite_noop_ms"] > 0
