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
    v = {x["algorithm"]: x for x in out["violations"] if x["algorithm"] == "greedy"
         or x.get("pair_cap") == 1000}
    assert v["milp"]["budget_violations"] == 0
    assert isinstance(v["greedy"]["budget_violations"], int)
    assert v["greedy"]["budget_violations"] >= 0


@pytest.mark.slow
def test_greedy_budget_violations_pinned_nonzero_at_n100():
    """회귀 방지: budget_violations가 항상 0을 반환하는 버그가 있어도 이전 테스트는
    통과했을 것이다(>=0 assertion은 0도 허용하므로) — 실험의 핵심 논지(Greedy가
    예산을 넘긴다는 사실 자체)를 직접 고정한다. n=100/seed=42는 실측으로 위반이
    정확히 1건 나오는 것이 확인된 스케일이다(task-5-report.md 참고).

    최종 리뷰 Important 12: 이 테스트는 실제 n=100 MILP solve(~7초, task-5-report.md
    기준)를 거치므로 `slow`로 표시한다 — pin하려는 사실 자체가 실측 n=100 스케일에
    고유하므로(다른 스케일로 shrink하면 더 이상 같은 것을 검증하지 않는다) 스케일은
    그대로 두고 마커만 옮긴다.
    """
    out = exp3_algorithm.run(scales=[(100, 20)], seeds=(42,), pair_caps=(1000,), time_limit=60)
    greedy_v = next(x for x in out["violations"] if x["algorithm"] == "greedy")
    assert greedy_v["budget_violations"] == 1


def test_milp_budget_violations_checked_at_every_pair_cap():
    """MILP의 예산 준수는 max_pairs(=synergy pruning 정도)와 무관한 하드 제약이므로
    cap마다 개별로 확인해야 한다 — 이전 구현은 pair_caps[0]에서만 확인하고 나머지
    cap은 추론으로 남겨뒀다(코디네이터 리뷰, 2026-08-08 반영).
    """
    out = exp3_algorithm.run(scales=[(50, 10)], seeds=(42,), pair_caps=(200, 1000), time_limit=60)
    milp_v = [x for x in out["violations"] if x["algorithm"] == "milp"]
    caps = {x["pair_cap"] for x in milp_v}
    assert caps == {200, 1000}
    assert all(x["budget_violations"] == 0 for x in milp_v)

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


@pytest.mark.slow
def test_scale_failure_is_isolated_and_recorded(monkeypatch):
    """한 스케일에서 solve_milp가 죽어도(예: 대규모에서 CBC Infeasible) 스윕 전체가
    죽지 않고, 그 스케일은 failures에 기록되며 다른 스케일은 정상적으로 결과를 낸다.

    최종 리뷰 Important 12: 이 테스트는 orchestration(실패 격리)을 검증하는 것이지
    최적화 품질을 검증하는 게 아니다 — 그런데도 이전 구현은 n=50/n=100 실 스케일을
    solve해 unmarked 상태로 39.8초를 태웠다. 아주 작은 스케일(10/14명)로도 같은
    분기(예외 발생 스케일은 failures에, 나머지는 rows에)를 그대로 검증할 수 있으므로
    스케일을 줄이고, 그래도 실제 solve를 거치니 `slow`로 표시한다."""
    real_solve = exp3_algorithm.solve_milp

    def flaky(graph, S, C, params, extra_constraints=None):
        if len(graph.people) == 10:
            raise RuntimeError("MILP failed: Infeasible")
        return real_solve(graph, S, C, params, extra_constraints=extra_constraints)

    monkeypatch.setattr(exp3_algorithm, "solve_milp", flaky)
    out = exp3_algorithm.run(scales=[(10, 3), (14, 4)], seeds=(42,), pair_caps=(1000,), time_limit=30)

    failed_ns = {f["n_people"] for f in out["failures"]}
    assert 10 in failed_ns
    ok_ns = {r["n_people"] for r in out["rows"]}
    assert 14 in ok_ns
    assert 10 not in ok_ns


@pytest.mark.slow
def test_on_scale_done_checkpoints_after_each_scale():
    """스케일 하나가 끝날 때마다 누적 결과로 콜백이 불려야 중간 저장(체크포인트)이 가능하다.

    최종 리뷰 Important 12: 체크포인트 콜백 타이밍을 검증하는 orchestration 테스트다
    (최적화 품질과 무관) — 이전에는 n=50/n=100 실 스케일로 unmarked 44.6초를 태웠다.
    아주 작은 스케일 두 개로도 "스케일마다 콜백이 불리고 누적된다"는 계약을 그대로
    검증할 수 있다."""
    calls = []
    exp3_algorithm.run(scales=[(10, 3), (14, 4)], seeds=(42,), pair_caps=(1000,), time_limit=30,
                       on_scale_done=lambda partial: calls.append(len(partial["rows"])))
    assert len(calls) == 2
    assert calls[0] < calls[1]


def test_skip_scales_uses_code_produced_schema():
    """대규모 스케일을 아예 시도하지 않고 건너뛰기로 한 결정도 코드 경로를 통해
    기록돼야 재현 가능하다(코디네이터 리뷰, 2026-08-08 반영) — out-of-repo 도구가
    직접 JSON을 조작해 만든 failures 항목은 `main()`을 재실행하면 재현되지 않는다.
    스킵 항목은 예외 경로와 동일한 키 스키마(n_people, n_projects, seed, status,
    reason)를 쓴다.
    """
    out = exp3_algorithm.run(scales=[(50, 10), (300, 60)], seeds=(42,), pair_caps=(1000,),
                             time_limit=60, skip_scales={300: "extrapolated beyond practical budget"})
    ok_ns = {r["n_people"] for r in out["rows"]}
    assert 50 in ok_ns
    assert 300 not in ok_ns

    skip_entries = [f for f in out["failures"] if f["n_people"] == 300]
    assert len(skip_entries) == 1
    entry = skip_entries[0]
    assert set(entry.keys()) == {"n_people", "n_projects", "seed", "status", "reason"}
    assert entry["n_projects"] == 60
    assert entry["seed"] == 42
    assert entry["status"] == "skipped"
    assert entry["reason"] == "extrapolated beyond practical budget"
