"""HiGHS seed portfolio (core/optimize/highs_portfolio.py, 2026-10-06)."""
import numpy as np
import pulp
import pytest

import core.optimize.highs_portfolio as hp
from core.optimize.highs_portfolio import HighsPortfolio


def _knapsack():
    prob = pulp.LpProblem("k", pulp.LpMaximize)
    x = [pulp.LpVariable(f"x{i}", cat="Binary") for i in range(8)]
    w = [5, 4, 6, 3, 7, 2, 5, 4]
    v = [10, 7, 12, 5, 13, 3, 9, 8]
    prob += pulp.lpSum(vi * xi for vi, xi in zip(v, x))
    prob += pulp.lpSum(wi * xi for wi, xi in zip(w, x)) <= 15
    return prob, x


def test_one_seed_is_plain_pulp_highs():
    a, xa = _knapsack()
    a.solve(pulp.HiGHS(msg=False, threads=1, gapRel=0.0))
    b, xb = _knapsack()
    solver = HighsPortfolio(seeds=1, msg=False, threads=1, gapRel=0.0)
    b.solve(solver)
    assert pulp.value(b.objective) == pulp.value(a.objective)
    assert [v.value() for v in xb] == [v.value() for v in xa]
    assert solver.portfolio is None


def test_several_seeds_agree_on_a_provable_optimum_and_pick_seed_zero():
    a, _ = _knapsack()
    a.solve(pulp.HiGHS(msg=False, threads=1, gapRel=0.0))
    b, xb = _knapsack()
    solver = HighsPortfolio(seeds=3, msg=False, threads=1, gapRel=0.0, timeLimit=30)
    b.solve(solver)
    assert pulp.LpStatus[b.status] == "Optimal" and b.sol_status == pulp.LpSolutionOptimal
    assert pulp.value(b.objective) == pytest.approx(pulp.value(a.objective))
    assert solver.portfolio["chosen_seed"] == 0
    assert len(solver.portfolio["objectives"]) >= 1          # a proven seed ends the others early
    assert solver.portfolio["errors"] == {}
    assert solver.portfolio["best_bound"] == pytest.approx(pulp.value(a.objective))     # maximize direction


def test_an_infeasible_model_is_reported_infeasible():
    prob = pulp.LpProblem("inf", pulp.LpMaximize)
    x = pulp.LpVariable("x", cat="Binary")
    prob += x
    prob += x >= 2
    prob.solve(HighsPortfolio(seeds=2, msg=False, threads=1))
    assert pulp.LpStatus[prob.status] == "Infeasible"


def _fake_run(seed, value, n, maximize=True, status=None):
    """value in the PuLP direction; HiGHS always minimizes (PuLP negates a maximization)."""
    import highspy
    status = int(highspy.HighsModelStatus.kTimeLimit) if status is None else status
    feasible = value is not None
    sign = -1.0 if maximize else 1.0
    return {"seed": seed, "error": None, "model_status": status, "feasible": feasible,
            "objective": None if not feasible else sign * value,
            "dual_bound": sign * (50.0 + seed) if maximize else 1.0 - seed,
            "col_value": np.full(n, float(seed)) if feasible else None}


def test_the_best_seed_wins_ties_go_to_the_lowest_and_bounds_take_the_tightest(monkeypatch):
    prob, x = _knapsack()
    n = len(prob.variables())
    runs = [_fake_run(0, 20.0, n), _fake_run(1, 24.0, n), _fake_run(2, 24.0, n), _fake_run(3, None, n)]
    monkeypatch.setattr(hp, "_run_seeds", lambda model, seeds, optimal, deadline: (runs, None))
    solver = HighsPortfolio(seeds=4, msg=False, threads=1)
    prob.solve(solver)
    assert solver.portfolio["chosen_seed"] == 1
    assert all(v.value() == 1.0 for v in x)                     # seed 1's column values were loaded
    assert prob.sol_status == pulp.LpSolutionIntegerFeasible     # stopped at the time limit, not proven
    assert solver.portfolio["best_bound"] == 50.0                # tightest of 50, 51, 52, 53 (maximize)


def test_minimization_picks_the_smallest_objective(monkeypatch):
    """Opus review MUST: the selection must not assume maximization."""
    prob = pulp.LpProblem("m", pulp.LpMinimize)
    x = pulp.LpVariable("x", lowBound=0, upBound=10, cat="Integer")
    prob += x
    prob += x >= 2
    runs = [_fake_run(0, 9.0, 1, maximize=False), _fake_run(1, 3.0, 1, maximize=False)]
    monkeypatch.setattr(hp, "_run_seeds", lambda model, seeds, optimal, deadline: (runs, None))
    solver = HighsPortfolio(seeds=2, msg=False, threads=1)
    prob.solve(solver)
    assert solver.portfolio["chosen_seed"] == 1 and x.value() == 1.0
    assert solver.portfolio["best_bound"] == 1.0                 # tightest lower bound of 1 and 0


def test_a_proven_seed_marks_the_better_incumbent_optimal_when_it_is_within_the_gap(monkeypatch):
    import highspy
    prob, _ = _knapsack()
    n = len(prob.variables())
    proven = _fake_run(0, 40.0, n, status=int(highspy.HighsModelStatus.kOptimal))
    proven["dual_bound"] = -41.0                                  # its bound: 41 (maximize)
    better = _fake_run(1, 40.5, n)                                # better, but stopped at the time limit
    better["dual_bound"] = -60.0
    monkeypatch.setattr(hp, "_run_seeds", lambda model, seeds, optimal, deadline: ([proven, better], None))
    solver = HighsPortfolio(seeds=2, msg=False, threads=1, gapRel=0.05)
    prob.solve(solver)
    assert solver.portfolio["chosen_seed"] == 1
    assert prob.sol_status == pulp.LpSolutionOptimal              # 41 vs 40.5 is within 5 %


def test_no_seed_with_a_solution_means_no_incumbent_and_errors_are_kept(monkeypatch):
    prob, x = _knapsack()
    n = len(prob.variables())
    runs = [_fake_run(0, None, n), dict(_fake_run(1, None, n), error="run failed (Solve error)", model_status=-1)]
    monkeypatch.setattr(hp, "_run_seeds", lambda model, seeds, optimal, deadline: (runs, None))
    solver = HighsPortfolio(seeds=2, msg=False, threads=1)
    prob.solve(solver)
    assert prob.sol_status == pulp.LpSolutionNoSolutionFound
    assert all(v.value() is None for v in x)
    assert solver.portfolio["errors"] == {"1": "run failed (Solve error)"}


def test_service_milp_with_seeds_matches_the_single_seed_optimum():
    """End to end through solve_milp_diagnostic on the Phase 1 all-terms fixture (gap 0 -> provable optimum)."""
    from core.optimize.milp import solve_milp_diagnostic
    from tests.phase1.test_raw_solution_contract import all_terms_fixture
    graph, skill, synergy, params, _ = all_terms_fixture()
    one = solve_milp_diagnostic(graph, skill, synergy, params.model_copy(update={"solver": "highs", "gap": 0.0}))
    many = solve_milp_diagnostic(graph, skill, synergy,
                                 params.model_copy(update={"solver": "highs", "gap": 0.0, "solver_seeds": 3}))
    assert many.objective == pytest.approx(one.objective, abs=1e-9)
    assert many.plan.entries == one.plan.entries
    assert many.evidence.options["seeds"] == 3 and many.evidence.options["chosen_seed"] == 0
    assert many.evidence.best_bound == pytest.approx(one.evidence.best_bound, abs=1e-5)


def test_every_seed_failing_raises_with_the_reasons(monkeypatch):
    """2nd review SHOULD: failures must not be disguised as "no incumbent"."""
    prob, _ = _knapsack()
    n = len(prob.variables())
    runs = [dict(_fake_run(s, None, n), error=f"boom {s}", model_status=-1) for s in range(2)]
    monkeypatch.setattr(hp, "_run_seeds", lambda model, seeds, optimal, deadline: (runs, None))
    with pytest.raises(RuntimeError, match="every seed failed.*boom 0.*boom 1"):
        prob.solve(HighsPortfolio(seeds=2, msg=False, threads=1))


def test_a_proof_restricts_the_choice_to_seeds_at_or_below_it(monkeypatch):
    """Seeds above the lowest proving seed are ignored even if they finished better (determinism)."""
    import highspy
    prob, _ = _knapsack()
    n = len(prob.variables())
    opt = int(highspy.HighsModelStatus.kOptimal)
    runs = [_fake_run(0, 40.0, n, status=opt), _fake_run(1, 40.4, n)]
    monkeypatch.setattr(hp, "_run_seeds", lambda model, seeds, optimal, deadline: (runs, 0))
    solver = HighsPortfolio(seeds=2, msg=False, threads=1, gapRel=0.05)
    prob.solve(solver)
    assert solver.portfolio["chosen_seed"] == 0 and prob.sol_status == pulp.LpSolutionOptimal


def _die(model, seed, conn):          # stands in for a worker killed by the OS (OOM, segfault)
    import os
    if seed == 0:
        os._exit(9)
    hp._seed_worker(model, seed, conn)


def _hang(model, seed, conn):
    import time
    time.sleep(60)


def test_a_worker_that_dies_is_reported_instead_of_waited_for_forever(monkeypatch):
    """2nd review MUST: multiprocessing.Pool lost the task of a dead worker and waited forever. Seed 0 dies, so the
    choice (seeds at or below the proving seed 1) must wait for it -- and gets EOF instead of hanging."""
    prob, _ = _knapsack()
    monkeypatch.setattr(hp, "_seed_worker", _die)
    solver = HighsPortfolio(seeds=2, msg=False, threads=1, gapRel=0.0, timeLimit=30)
    prob.solve(solver)
    assert solver.portfolio["chosen_seed"] == 1
    assert list(solver.portfolio["errors"]) == ["0"] and "exited without a result" in solver.portfolio["errors"]["0"]


def test_a_hung_worker_is_cut_off_at_the_deadline(monkeypatch):
    import highspy
    prob, _ = _knapsack()
    builder = HighsPortfolio(seeds=1, msg=False, threads=1)
    builder.createAndConfigureSolver(prob)
    builder.buildSolverModel(prob)
    model = hp._export(prob.solverModel, {})
    monkeypatch.setattr(hp, "_seed_worker", _hang)
    t = __import__("time").perf_counter()
    runs, proven = hp._run_seeds(model, 1, int(highspy.HighsModelStatus.kOptimal), deadline_s=1.0)
    assert __import__("time").perf_counter() - t < 15
    assert proven is None and "no result within 1s" in runs[0]["error"]
