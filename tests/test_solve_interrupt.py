"""풀이 중단(core/optimize/interrupt, 2026-10-12): 사용자가 멈추면 그때까지의 해로 빨리 끝나고, 화면이 닫히면 바로 버린다."""
import multiprocessing
import random
import threading
import time

import pulp
import pytest

from core.optimize.highs_portfolio import HighsPortfolio
from core.optimize.interrupt import SolveCancelled, SolveStop, watching


def _hard_knapsack(seed: int = 1, n: int = 160, m: int = 30) -> pulp.LpProblem:
    """여러 제약 배낭 -- 수십 초로는 최적 증명이 안 되지만 해는 금방 나온다."""
    rnd = random.Random(seed)
    prob = pulp.LpProblem("hard", pulp.LpMaximize)
    x = [pulp.LpVariable(f"x{i}", cat="Binary") for i in range(n)]
    prob += pulp.lpSum(rnd.randint(10, 100) * x[i] for i in range(n))
    for _ in range(m):
        prob += pulp.lpSum(rnd.randint(5, 60) * x[i] for i in range(n)) <= 1100
    return prob


def _solve(prob, solver, stop, after: float):
    threading.Timer(after, lambda: stop.request(stop_mode[0])).start()
    t0 = time.monotonic()
    with watching(stop):
        status = prob.solve(solver)
    return status, time.monotonic() - t0


stop_mode = ["finish"]


@pytest.mark.parametrize("seeds,after", [(1, 0.5), (2, 2.0)])
def test_finish_returns_the_best_solution_so_far_quickly(seeds, after):
    stop_mode[0] = "finish"
    prob, stop = _hard_knapsack(), SolveStop()
    solver = HighsPortfolio(seeds=seeds, msg=False, timeLimit=120, gapRel=0.0, threads=1)
    status, took = _solve(prob, solver, stop, after)
    assert took < after + 12                                   # 120초 한도까지 가지 않는다(시드는 유예 10초 안)
    assert solver.interrupted is True
    assert pulp.LpStatus[status] == "Optimal" and prob.sol_status == pulp.LpSolutionIntegerFeasible   # 증명 전의 해
    assert pulp.value(prob.objective) and pulp.value(prob.objective) > 0


@pytest.mark.parametrize("seeds,after", [(1, 0.5), (2, 2.0)])
def test_abort_drops_the_solve_and_its_processes(seeds, after):
    stop_mode[0] = "abort"
    prob, stop = _hard_knapsack(seed=2), SolveStop()
    solver = HighsPortfolio(seeds=seeds, msg=False, timeLimit=120, gapRel=0.0, threads=1)
    t0 = time.monotonic()
    with pytest.raises(SolveCancelled):
        _solve(prob, solver, stop, after)
    assert time.monotonic() - t0 < after + 8
    deadline = time.monotonic() + 5
    while multiprocessing.active_children() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not multiprocessing.active_children()               # 시드 프로세스가 남지 않는다


def test_a_new_solve_does_not_start_after_a_stop():
    stop = SolveStop()
    stop.request("finish")
    t0 = time.monotonic()
    with watching(stop), pytest.raises(SolveCancelled):
        _hard_knapsack().solve(HighsPortfolio(seeds=1, msg=False, timeLimit=120, threads=1))
    assert time.monotonic() - t0 < 1.0


def test_abort_wins_over_finish():
    stop = SolveStop()
    stop.request("abort")
    stop.request("finish")
    assert stop.aborting and stop.mode == "abort"


def test_no_stop_means_no_change():
    prob = pulp.LpProblem("easy", pulp.LpMaximize)
    x = pulp.LpVariable("x", 0, 3, cat="Integer")
    prob += x
    solver = HighsPortfolio(seeds=1, msg=False, timeLimit=10, threads=1)
    prob.solve(solver)
    assert pulp.value(x) == 3 and solver.interrupted is False and solver.callbackTuple is None


def test_when_no_seed_answers_after_a_stop_it_does_not_solve_again(monkeypatch):
    """유예 안에 해를 돌려준 시드가 없으면(모두 오류) 예전처럼 한 프로세스로 다시 풀지 않는다(Codex 리뷰 2026-10-12)."""
    import core.optimize.highs_portfolio as hp
    stop = SolveStop()

    def all_errors(model, seeds, optimal, deadline, stop_arg=None):
        stop.request("finish")                                     # 풀이 중에 멈췄고
        return [hp._error_run(s, "interrupted but no result within 10s") for s in range(seeds)], None

    called = []
    monkeypatch.setattr(hp, "_run_seeds", all_errors)
    monkeypatch.setattr(pulp.HiGHS, "actualSolve", lambda self, lp: called.append(1))
    with watching(stop), pytest.raises(SolveCancelled):
        _hard_knapsack().solve(HighsPortfolio(seeds=2, msg=False, timeLimit=60, threads=1))
    assert called == []
