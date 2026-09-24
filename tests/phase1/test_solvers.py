import math

import pytest

from core.optimize.validation import validate_raw_solution
from experiments.phase1.solvers import available_solvers, solve_case
from experiments.phase1.types import (
    BenchmarkProblem,
    SolverAvailabilityState,
    SolverOptions,
)
from tests.phase0.factories import all_terms_fixture


def _all_terms_problem() -> BenchmarkProblem:
    graph, skill, synergy, params, _ = all_terms_fixture()
    return BenchmarkProblem(graph=graph, S=skill, C=synergy, params=params)


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
