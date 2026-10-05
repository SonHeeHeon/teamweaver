import math
from types import SimpleNamespace

import pulp
import pytest

from core.optimize.validation import validate_raw_solution
from experiments.phase1 import solvers as solver_module
from experiments.phase1.solvers import (
    SolverSolveError,
    SolverUnavailableError,
    available_solvers,
    solve_case,
)
from experiments.phase1.scenarios import build_snapshot
from experiments.phase1.types import (
    BenchmarkProblem,
    SolverAvailabilityState,
    SolverOptions,
)
from tests.phase0.factories import all_terms_fixture


def _all_terms_problem() -> BenchmarkProblem:
    graph, skill, synergy, params, _ = all_terms_fixture()
    return BenchmarkProblem(graph=graph, S=skill, C=synergy, params=params)


def _compatibility_problem() -> BenchmarkProblem:
    materialized = build_snapshot(50, 10, 42, "baseline").materialize()
    return BenchmarkProblem(
        graph=materialized.graph,
        S=materialized.S,
        C=materialized.C,
        params=materialized.params,
    )


class _FiniteCandidateSolver:
    def __init__(self, status, values, native_model=None):
        self.status = status
        self.values = values
        self.native_model = native_model

    def actualSolve(self, problem):
        for variable in problem.variables():
            variable.varValue = self.values.get(variable.name, 0.0)
        problem.solverModel = self.native_model
        solution_status = (
            pulp.LpSolutionIntegerFeasible
            if self.status in (pulp.LpStatusOptimal, pulp.LpStatusNotSolved)
            else pulp.LpSolutionInfeasible
        )
        problem.assignStatus(self.status, solution_status)
        return self.status


def _valid_all_terms_values():
    return {
        "z_0_0": 1.0,
        "z_1_0": 1.0,
        "a_0_0": 1.0,
        "a_1_0": 1.0,
        "y_0_1_0": 1.0,
        "s_0_중급": 1.0,
    }


def test_cbc_solution_passes_the_independent_validator():
    problem = _all_terms_problem()

    solution = solve_case(
        problem,
        "cbc",
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    report = validate_raw_solution(
        problem.graph,
        problem.S,
        problem.C,
        problem.params,
        solution,
    )
    assert report.valid, report.issues


def test_optional_solver_availability_is_reported_without_raising():
    availability = available_solvers()

    assert set(availability) == {"cbc", "highs", "scip"}
    assert availability["cbc"].state is SolverAvailabilityState.AVAILABLE
    for solver_name in ("highs", "scip"):
        record = availability[solver_name]
        assert record.state in {
            SolverAvailabilityState.AVAILABLE,
            SolverAvailabilityState.UNAVAILABLE,
        }
        assert record.import_name
        if record.state is SolverAvailabilityState.AVAILABLE:
            assert record.version
            assert record.error is None
        else:
            assert record.version is None
            assert record.error


def test_optional_import_failure_is_typed_unavailable(monkeypatch):
    def missing_highspy():
        raise ImportError("simulated missing highspy")

    monkeypatch.setattr(solver_module, "_highs_version", missing_highspy)

    record = available_solvers()["highs"]

    assert record.state is SolverAvailabilityState.UNAVAILABLE
    assert record.import_name == "highspy"
    assert record.version is None
    assert record.error == "ImportError: simulated missing highspy"


def test_option_configured_constructor_failure_is_typed_unavailable(monkeypatch):
    def injected_highs_constructor(**kwargs):
        if set(kwargs) == {"msg"}:
            return SimpleNamespace(available=lambda: True)
        raise pulp.PulpSolverError("simulated HiGHS option setup failure")

    monkeypatch.setattr(solver_module.pulp, "HiGHS", injected_highs_constructor)

    with pytest.raises(SolverUnavailableError) as caught:
        solve_case(
            _all_terms_problem(),
            "highs",
            SolverOptions(threads=1, time_limit_seconds=30),
        )

    assert caught.value.availability.state is SolverAvailabilityState.UNAVAILABLE
    assert caught.value.availability.import_name == "highspy"
    assert caught.value.availability.version is None
    assert caught.value.availability.error == (
        "PulpSolverError: simulated HiGHS option setup failure"
    )


def test_optimization_time_failure_remains_a_failed_solve(monkeypatch):
    class FailingOptimizationSolver:
        def actualSolve(self, _problem):
            raise pulp.PulpSolverError("simulated optimization failure")

    monkeypatch.setattr(
        solver_module,
        "_solver_for",
        lambda *_args: FailingOptimizationSolver(),
    )

    with pytest.raises(SolverSolveError) as caught:
        solve_case(
            _all_terms_problem(),
            "cbc",
            SolverOptions(threads=1, time_limit_seconds=30),
        )

    assert caught.value.evidence.native_status == "SOLVER_EXECUTION_ERROR"
    assert caught.value.evidence.has_incumbent is False


@pytest.mark.parametrize("solver_name", ["cbc", "highs", "scip"])
def test_available_solver_records_native_status_incumbent_and_requested_options(
    solver_name,
):
    if available_solvers()[solver_name].state is SolverAvailabilityState.UNAVAILABLE:
        pytest.skip(f"{solver_name} is unavailable on this host")
    problem = _all_terms_problem()

    solution = solve_case(
        problem,
        solver_name,
        SolverOptions(threads=1, time_limit_seconds=17),
    )

    assert solution.evidence.native_status
    assert type(solution.evidence.has_incumbent) is bool
    assert solution.evidence.options["threads"] == 1
    assert solution.evidence.options["time_limit_seconds"] == 17
    if solution.evidence.has_incumbent:
        assert math.isfinite(solution.objective)


@pytest.mark.parametrize("solver_name", ["cbc", "highs", "scip"])
def test_available_solver_matches_literal_small_oracle_objective(solver_name):
    if available_solvers()[solver_name].state is SolverAvailabilityState.UNAVAILABLE:
        pytest.skip(f"{solver_name} is unavailable on this host")

    solution = solve_case(
        _all_terms_problem(),
        solver_name,
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    assert solution.evidence.has_incumbent
    assert solution.objective == pytest.approx(-98.38, abs=1e-6)


@pytest.mark.parametrize("solver_name", ["highs", "scip"])
def test_native_boundary_noise_is_canonicalized_before_strict_validation(solver_name):
    if available_solvers()[solver_name].state is SolverAvailabilityState.UNAVAILABLE:
        pytest.skip(f"{solver_name} is unavailable on this host")
    problem = _compatibility_problem()

    solution = solve_case(
        problem,
        solver_name,
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    report = validate_raw_solution(
        problem.graph,
        problem.S,
        problem.C,
        problem.params,
        solution,
    )
    assert report.valid, report.issues
    assert set(solution.z.values()) <= {0.0, 1.0}
    assert all(0.0 <= value <= 1.0 for value in solution.a.values())
    assert all(0.0 <= value <= 1.0 for value in solution.y.values())
    assert all(value >= 0.0 for value in solution.slack.values())


def test_adapter_does_not_hide_a_domain_violation_beyond_numeric_tolerance(monkeypatch):
    values = _valid_all_terms_values()
    values["z_0_0"] = 1.0 + 2e-6
    fake_solver = _FiniteCandidateSolver(status=pulp.LpStatusOptimal, values=values)
    monkeypatch.setattr(solver_module, "_solver_for", lambda *_args: fake_solver)

    with pytest.raises(SolverSolveError, match="binary_domain"):
        solve_case(
            _all_terms_problem(),
            "cbc",
            SolverOptions(threads=1, time_limit_seconds=30),
        )


@pytest.mark.parametrize("solver_name", ["cbc", "highs", "scip"])
def test_native_bound_uses_the_benchmark_maximization_direction(solver_name):
    if available_solvers()[solver_name].state is SolverAvailabilityState.UNAVAILABLE:
        pytest.skip(f"{solver_name} is unavailable on this host")

    solution = solve_case(
        _all_terms_problem(),
        solver_name,
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    if solution.evidence.best_bound is not None:
        assert solution.evidence.best_bound >= solution.objective - 1e-6
        assert solution.evidence.best_bound == pytest.approx(-98.38, abs=1e-6)


@pytest.mark.parametrize(
    "status",
    [pulp.LpStatusNotSolved, pulp.LpStatusInfeasible],
    ids=["invalid-time-limited-candidate", "infeasible-finite-values"],
)
def test_finite_but_invalid_candidate_raises_failed_solve_with_no_incumbent(
    monkeypatch, status
):
    fake_solver = _FiniteCandidateSolver(status=status, values={})
    monkeypatch.setattr(solver_module, "_solver_for", lambda *_args: fake_solver)

    with pytest.raises(SolverSolveError) as caught:
        solve_case(
            _all_terms_problem(),
            "cbc",
            SolverOptions(threads=1, time_limit_seconds=30),
        )

    assert caught.value.evidence.native_status in {"Not Solved", "Infeasible"}
    assert caught.value.evidence.has_incumbent is False


def test_independently_valid_time_limited_cbc_candidate_is_preserved(monkeypatch):
    fake_solver = _FiniteCandidateSolver(
        status=pulp.LpStatusNotSolved,
        values=_valid_all_terms_values(),
    )
    monkeypatch.setattr(solver_module, "_solver_for", lambda *_args: fake_solver)

    solution = solve_case(
        _all_terms_problem(),
        "cbc",
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    assert solution.status == "Not Solved"
    assert solution.evidence.has_incumbent is True
    assert solution.objective == pytest.approx(-98.38, abs=1e-6)


def test_scip_infinity_sentinel_bound_is_not_recorded(monkeypatch):
    native_model = SimpleNamespace(
        getStatus=lambda: "timelimit",
        getNSols=lambda: 1,
        getDualbound=lambda: 1e20,
        infinity=lambda: 1e20,
    )
    fake_solver = _FiniteCandidateSolver(
        status=pulp.LpStatusOptimal,
        values=_valid_all_terms_values(),
        native_model=native_model,
    )
    monkeypatch.setattr(solver_module, "_solver_for", lambda *_args: fake_solver)

    solution = solve_case(
        _all_terms_problem(),
        "scip",
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    assert solution.evidence.best_bound is None


def test_highs_invalid_native_info_does_not_supply_a_bound(monkeypatch):
    native_model = SimpleNamespace(
        getModelStatus=lambda: "time-limit",
        modelStatusToString=lambda _status: "Time limit reached",
        getSolution=lambda: SimpleNamespace(value_valid=True),
        getInfo=lambda: SimpleNamespace(valid=False, mip_dual_bound=0.0),
    )
    fake_solver = _FiniteCandidateSolver(
        status=pulp.LpStatusOptimal,
        values=_valid_all_terms_values(),
        native_model=native_model,
    )
    monkeypatch.setattr(solver_module, "_solver_for", lambda *_args: fake_solver)

    solution = solve_case(
        _all_terms_problem(),
        "highs",
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    assert solution.evidence.best_bound is None


def test_highs_bound_below_incumbent_is_preserved_for_invalid_quality_reporting(
    monkeypatch,
):
    native_model = SimpleNamespace(
        getModelStatus=lambda: "time-limit",
        modelStatusToString=lambda _status: "Time limit reached",
        getSolution=lambda: SimpleNamespace(value_valid=True),
        # PuLP negates a maximization model for HiGHS, so this native +100
        # would normalize to -100: below the independently valid -98.38.
        getInfo=lambda: SimpleNamespace(valid=True, mip_dual_bound=100.0),
    )
    fake_solver = _FiniteCandidateSolver(
        status=pulp.LpStatusOptimal,
        values=_valid_all_terms_values(),
        native_model=native_model,
    )
    monkeypatch.setattr(solver_module, "_solver_for", lambda *_args: fake_solver)

    solution = solve_case(
        _all_terms_problem(),
        "highs",
        SolverOptions(threads=1, time_limit_seconds=30),
    )

    assert solution.objective == pytest.approx(-98.38, abs=1e-6)
    assert solution.evidence.has_incumbent is True
    assert solution.evidence.best_bound == -100.0
