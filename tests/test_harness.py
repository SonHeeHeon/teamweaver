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
