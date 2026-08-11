import time
from experiments.bench import harness, datasets


def test_measure_shape_and_ordering():
    r = harness.measure(lambda: time.sleep(0.001), repeats=5, warmup=2)
    assert {"median_ms", "p95_ms", "min_ms", "max_ms", "repeats", "warmup"} <= r.keys()
    assert r["repeats"] == 5 and r["warmup"] == 2
    assert r["min_ms"] <= r["median_ms"] <= r["p95_ms"] <= r["max_ms"]
    assert r["median_ms"] >= 0.5     # 1ms sleep 이므로 하한만 느슨하게 검증


def test_measure_excludes_warmup_from_stats():
    calls = []
    def fn():
        calls.append(1)
        time.sleep(0.02 if len(calls) <= 3 else 0.001)   # 웜업 3회만 느리게
    r = harness.measure(fn, repeats=4, warmup=3)
    assert len(calls) == 7                      # 웜업 3 + 측정 4
    assert r["max_ms"] < 15.0, "웜업(20ms)이 통계에 섞이면 실패한다"


def test_environment_records_versions():
    env = harness.environment()
    assert env["python"].startswith("3.12")
    for key in ("platform", "cpu_count", "packages", "cbc"):
        assert key in env
    assert "numpy" in env["packages"]


def test_environment_records_neo4j_server_cpu_and_ram():
    """최종 리뷰 Important 9: 헤드라인이 'Neo4j가 5배 느리다'인 벤치마크인데
    environment()가 Neo4j *서버* 버전(클라이언트 드라이버 버전과 별개)도, CPU
    모델·RAM도 기록하지 않았다. 세 값 모두 방어적으로 수집돼야 하고(probe 실패가
    벤치마크 자체를 깨면 안 됨), 실패 시에도 문자열 키는 항상 존재해야 한다."""
    env = harness.environment()
    for key in ("neo4j_server", "cpu_model", "ram"):
        assert key in env
        assert isinstance(env[key], str) and env[key]


def test_neo4j_server_version_never_raises_when_neo4j_down(monkeypatch):
    """Neo4j가 죽어 있어도(또는 접속 정보가 잘못돼도) environment() 전체가 죽으면
    안 된다 -- probe는 항상 방어적이어야 한다."""
    def _boom(*a, **kw):
        raise RuntimeError("connection refused")
    import core.graph.neo4j_store as neo4j_store
    monkeypatch.setattr(neo4j_store, "get_driver", _boom)
    result = harness._neo4j_server_version()
    assert isinstance(result, str) and "unavailable" in result


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "RESULTS_DIR", tmp_path)
    p = harness.save_result("demo", {"a": 1})
    assert p.exists()
    loaded = harness.load_result("demo")
    assert loaded["data"] == {"a": 1} and "environment" in loaded


def test_build_scale_shapes():
    ds, parsed, g = datasets.build_scale(50, 10, seed=42)
    assert len(ds.people) == 50 and len(ds.projects) == 10
    assert len(parsed) == len(ds.reviews)
    assert g.cowork_months.shape == (50, 50)


def test_scales_ratio_is_five_to_one():
    assert datasets.SCALES[0] == (50, 10) and datasets.SCALES[-1] == (1000, 200)
    assert all(n // 5 == j for n, j in datasets.SCALES)
