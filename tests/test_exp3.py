import pytest
from experiments.bench import exp3_algorithm

def test_run_small_scale_reports_both_algorithms():
    out = exp3_algorithm.run(scales=[(50, 10)], seeds=(42,), pair_caps=(1000,), time_limit=60)
    algos = {r["algorithm"] for r in out["rows"]}
    assert algos == {"greedy", "milp"}
    for r in out["rows"]:
        assert 0.0 <= r["optimization_ratio"] <= 1.0 + 1e-6
        assert r["solve_ms"] > 0

def test_milp_beats_greedy_on_optimization_ratio():
    out = exp3_algorithm.run(scales=[(50, 10)], seeds=(42,), pair_caps=(1000,), time_limit=60)
    milp = next(r for r in out["rows"] if r["algorithm"] == "milp")
    greedy = next(r for r in out["rows"] if r["algorithm"] == "greedy")
    assert milp["optimization_ratio"] > greedy["optimization_ratio"]

def test_constraint_violations_are_recorded_per_algorithm():
    """실험의 핵심 논지: MILP는 제약을 지키고 Greedy는 예산을 넘긴다."""
    out = exp3_algorithm.run(scales=[(50, 10)], seeds=(42,), pair_caps=(1000,), time_limit=60)
    v = {x["algorithm"]: x for x in out["violations"]}
    assert v["milp"]["budget_violations"] == 0
    assert "budget_violations" in v["greedy"]

def test_pair_cap_sweep_recorded():
    out = exp3_algorithm.run(scales=[(50, 10)], seeds=(42,), pair_caps=(200, 1000), time_limit=60)
    caps = {r["pair_cap"] for r in out["rows"] if r["algorithm"] == "milp"}
    assert caps == {200, 1000}


def test_hit_time_limit_flag_present_and_false_for_fast_small_solve():
    """작은 규모(n=50)는 60초 예산 안에서 여유 있게 풀리므로 hit_time_limit=False여야 한다."""
    out = exp3_algorithm.run(scales=[(50, 10)], seeds=(42,), pair_caps=(1000,), time_limit=60)
    for r in out["rows"]:
        assert "hit_time_limit" in r
        assert isinstance(r["hit_time_limit"], bool)
    assert all(not r["hit_time_limit"] for r in out["rows"])


def test_scale_failure_is_isolated_and_recorded(monkeypatch):
    """한 스케일에서 solve_milp가 죽어도(예: 대규모에서 CBC Infeasible) 스윕 전체가
    죽지 않고, 그 스케일은 failures에 기록되며 다른 스케일은 정상적으로 결과를 낸다.
    """
    real_solve = exp3_algorithm.solve_milp

    def flaky(graph, S, C, params, extra_constraints=None):
        if len(graph.people) == 50:
            raise RuntimeError("MILP failed: Infeasible")
        return real_solve(graph, S, C, params, extra_constraints=extra_constraints)

    monkeypatch.setattr(exp3_algorithm, "solve_milp", flaky)
    out = exp3_algorithm.run(scales=[(50, 10), (100, 20)], seeds=(42,), pair_caps=(1000,), time_limit=60)

    failed_ns = {f["n_people"] for f in out["failures"]}
    assert 50 in failed_ns
    ok_ns = {r["n_people"] for r in out["rows"]}
    assert 100 in ok_ns
    assert 50 not in ok_ns


def test_on_scale_done_checkpoints_after_each_scale():
    """스케일 하나가 끝날 때마다 누적 결과로 콜백이 불려야 중간 저장(체크포인트)이 가능하다."""
    calls = []
    exp3_algorithm.run(scales=[(50, 10), (100, 20)], seeds=(42,), pair_caps=(1000,), time_limit=60,
                       on_scale_done=lambda partial: calls.append(len(partial["rows"])))
    assert len(calls) == 2
    assert calls[0] < calls[1]
