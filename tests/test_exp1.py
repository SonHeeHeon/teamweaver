import pytest
from experiments.bench import exp1_storage

def test_run_small_scale_shape():
    out = exp1_storage.run(scales=[(50, 10)], hops=(1, 2), seeds_per_run=3, neo4j=False)
    backends = {r["backend"] for r in out["rows"]}
    assert backends == {"sqlite", "memory"}          # neo4j=False 이므로 제외
    assert len(out["rows"]) == 2 * 2                  # 백엔드 2 × hops 2
    for r in out["rows"]:
        assert r["median_ms"] > 0 and r["p95_ms"] >= r["median_ms"]
        assert r["n_people"] == 50 and r["result_count"] >= 0

def test_parity_recorded_and_backends_agree():
    """공정성의 핵심: 같은 질문에 같은 답을 내야 비교가 성립한다."""
    out = exp1_storage.run(scales=[(50, 10)], hops=(1, 2), seeds_per_run=3, neo4j=False)
    assert out["parity"], "백엔드 일치 검증 기록이 있어야 한다"
    for p in out["parity"]:
        assert p["match"] is True, f"불일치: {p}"

def test_neo4j_skip_is_recorded_not_silent():
    out = exp1_storage.run(scales=[(50, 10)], hops=(1,), seeds_per_run=2, neo4j=False)
    assert any("neo4j" in s["backend"] for s in out["skipped"])
