from dataclasses import dataclass, replace
import math
from typing import Protocol

import pulp

from core.domain.models import Grade
from core.optimize.audit_types import RawMilpSolution, SolverEvidence
from core.optimize.milp import _floor2, _overfamiliar_pairs, pruned_pairs
from core.optimize.types import AssignEntry, PlanAssignment
from core.optimize.validation import validate_raw_solution
from experiments.phase1.types import (
    BenchmarkProblem,
    SolverAvailability,
    SolverAvailabilityState,
    SolverOptions,
)


class SolverUnavailableError(RuntimeError):
    pass


class SolverSolveError(RuntimeError):
    """A solver ran but did not yield an extractable integer incumbent."""

    def __init__(self, message: str, evidence: SolverEvidence):
        super().__init__(message)
        self.evidence = evidence


class _VariableModelFactory(Protocol):
    def model(self, name: str) -> pulp.LpProblem: ...

    def binary(self, name: str) -> pulp.LpVariable: ...

    def continuous(
        self, name: str, low: float | None = None, high: float | None = None
    ) -> pulp.LpVariable: ...


@dataclass(frozen=True)
class _PulpFactory:
    def model(self, name: str) -> pulp.LpProblem:
        return pulp.LpProblem(name, pulp.LpMaximize)

    def binary(self, name: str) -> pulp.LpVariable:
        return pulp.LpVariable(name, cat="Binary")

    def continuous(
        self, name: str, low: float | None = None, high: float | None = None
    ) -> pulp.LpVariable:
        return pulp.LpVariable(name, lowBound=low, upBound=high)


@dataclass
class _BuiltModel:
    problem: pulp.LpProblem
    z: dict[tuple[int, int], pulp.LpVariable]
    a: dict[tuple[int, int], pulp.LpVariable]
    y: dict[tuple[int, int, int], pulp.LpVariable]
    slack: dict[tuple[int, Grade], pulp.LpVariable]
    reward_pairs: tuple[tuple[int, int], ...]
    penalty_pairs: tuple[tuple[int, int], ...]


def _build_model(
    benchmark: BenchmarkProblem, factory: _VariableModelFactory
) -> _BuiltModel:
    """Create the one benchmark MILP shared unchanged by every backend."""
    graph, skill, synergy, params = (
        benchmark.graph,
        benchmark.S,
        benchmark.C,
        benchmark.params,
    )
    people, projects = graph.people, graph.projects
    n_people, n_projects = len(people), len(projects)
    problem = factory.model("teamweaver_phase1")
    z = {
        (i, j): factory.binary(f"z_{i}_{j}")
        for i in range(n_people)
        for j in range(n_projects)
    }
    a = {
        (i, j): factory.continuous(f"a_{i}_{j}", 0.0, 1.0)
        for i in range(n_people)
        for j in range(n_projects)
    }
    reward_pairs = tuple(
        pruned_pairs(synergy, params.pair_keep_ratio, params.max_pairs)
    )
    penalty_pairs = tuple(
        sorted(_overfamiliar_pairs(graph, params.clique_threshold_months))
    )
    model_pairs = tuple(sorted(set(reward_pairs) | set(penalty_pairs)))
    y = {
        (p, q, j): factory.continuous(f"y_{p}_{q}_{j}", 0.0, 1.0)
        for p, q in model_pairs
        for j in range(n_projects)
    }
    slack = {
        (j, grade): factory.continuous(f"s_{j}_{grade.value}", 0.0)
        for j, project in enumerate(projects)
        for grade in project.grade_headcount
    }

    problem += (
        pulp.lpSum(
            float(skill[i, j]) * a[(i, j)]
            for i in range(n_people)
            for j in range(n_projects)
        )
        + params.lam
        * pulp.lpSum(
            float(synergy[p, q]) * y[(p, q, j)]
            for p, q in reward_pairs
            for j in range(n_projects)
        )
        - params.mu
        * pulp.lpSum(
            y[(p, q, j)]
            for p, q in penalty_pairs
            for j in range(n_projects)
        )
        - params.slack_penalty * pulp.lpSum(slack.values())
    )

    for i in range(n_people):
        for j in range(n_projects):
            problem += a[(i, j)] <= z[(i, j)]
            problem += a[(i, j)] >= params.min_alloc * z[(i, j)]
    for p, q in model_pairs:
        for j in range(n_projects):
            problem += y[(p, q, j)] <= z[(p, j)]
            problem += y[(p, q, j)] <= z[(q, j)]
            problem += y[(p, q, j)] >= z[(p, j)] + z[(q, j)] - 1
    for i, person in enumerate(people):
        for month, availability in enumerate(person.availability):
            active_projects = [
                j for j, project in enumerate(projects) if month in project.months
            ]
            if active_projects:
                problem += (
                    pulp.lpSum(a[(i, j)] for j in active_projects) <= availability
                )
    for j, project in enumerate(projects):
        for grade, required in project.grade_headcount.items():
            matching_people = [
                i for i, person in enumerate(people) if person.grade == grade
            ]
            problem += (
                pulp.lpSum(z[(i, j)] for i in matching_people) + slack[(j, grade)]
                == required
            )
        problem += (
            pulp.lpSum(
                people[i].monthly_rate * a[(i, j)] for i in range(n_people)
            )
            <= project.monthly_budget
        )

    return _BuiltModel(
        problem=problem,
        z=z,
        a=a,
        y=y,
        slack=slack,
        reward_pairs=reward_pairs,
        penalty_pairs=penalty_pairs,
    )


def _available(
    import_name: str, version_loader, solver_loader
) -> SolverAvailability:
    try:
        version = str(version_loader())
        solver = solver_loader()
        available = solver.available()
        if not available:
            return SolverAvailability(
                state=SolverAvailabilityState.UNAVAILABLE,
                import_name=import_name,
                version=None,
                error="solver backend reported unavailable",
            )
    except (ImportError, ModuleNotFoundError, OSError, pulp.PulpSolverError) as exc:
        return SolverAvailability(
            state=SolverAvailabilityState.UNAVAILABLE,
            import_name=import_name,
            version=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    return SolverAvailability(
        state=SolverAvailabilityState.AVAILABLE,
        import_name=import_name,
        version=version,
        error=None,
    )


def _cbc_version() -> str:
    return f"PuLP {pulp.__version__} bundled CBC"


def _highs_version() -> str:
    import highspy

    return ".".join(
        str(value)
        for value in (
            highspy.HIGHS_VERSION_MAJOR,
            highspy.HIGHS_VERSION_MINOR,
            highspy.HIGHS_VERSION_PATCH,
        )
    )


def _scip_version() -> str:
    import pyscipopt

    return pyscipopt.__version__


def available_solvers() -> dict[str, SolverAvailability]:
    return {
        "cbc": _available(
            "pulp.PULP_CBC_CMD",
            _cbc_version,
            lambda: pulp.PULP_CBC_CMD(msg=False),
        ),
        "highs": _available(
            "highspy",
            _highs_version,
            lambda: pulp.HiGHS(msg=False),
        ),
        "scip": _available(
            "pyscipopt",
            _scip_version,
            lambda: pulp.SCIP_PY(msg=False),
        ),
    }


def _solver_for(name: str, options: SolverOptions) -> pulp.LpSolver:
    common = {
        "msg": False,
        "timeLimit": options.time_limit_seconds,
        "gapRel": options.relative_gap,
        "threads": options.threads,
    }
    if name == "cbc":
        return pulp.PULP_CBC_CMD(**common)
    if name == "highs":
        return pulp.HiGHS(**common)
    if name == "scip":
        return pulp.SCIP_PY(**common)
    raise ValueError(f"unknown solver: {name}")


def _finite_or_none(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


_HIGHS_EVIDENCE_STATUSES = {
    "Optimal",
    "Bound on objective reached",
    "Target for objective reached",
    "Time limit reached",
    "Iteration limit reached",
    "Solution limit reached",
    "Interrupted by user",
    "Memory limit reached",
    "Interrupted by HiGHS",
}
_SCIP_EVIDENCE_STATUSES = {
    "optimal",
    "timelimit",
    "userinterrupt",
    "nodelimit",
    "totalnodelimit",
    "stallnodelimit",
    "gaplimit",
    "memlimit",
    "sollimit",
    "bestsollimit",
    "restartlimit",
}


def _status_supports_solver_evidence(name: str, native_status: str) -> bool:
    if name == "highs":
        return native_status in _HIGHS_EVIDENCE_STATUSES
    if name == "scip":
        return native_status in _SCIP_EVIDENCE_STATUSES
    return native_status in {"Optimal", "Not Solved"}


def _bounded_native_value(value, infinity: float | None = None) -> float | None:
    bound = _finite_or_none(value)
    if bound is None:
        return None
    finite_infinity = _finite_or_none(infinity)
    if finite_infinity is not None and abs(bound) >= abs(finite_infinity):
        return None
    return bound


def _native_evidence(
    name: str,
    built: _BuiltModel,
    options: SolverOptions,
    raw_values: tuple[float | None, ...],
) -> tuple[SolverEvidence, bool]:
    model = built.problem.solverModel
    finite_values = all(_finite_or_none(value) is not None for value in raw_values)
    objective = _finite_or_none(pulp.value(built.problem.objective))
    best_bound: float | None = None

    if name == "highs":
        native_status_code = model.getModelStatus()
        native_status = model.modelStatusToString(native_status_code)
        solution = model.getSolution()
        candidate_available = bool(
            solution.value_valid
            and finite_values
            and objective is not None
            and _status_supports_solver_evidence(name, native_status)
        )
        info = model.getInfo()
        if info.valid and _status_supports_solver_evidence(name, native_status):
            best_bound = _bounded_native_value(info.mip_dual_bound)
        # PuLP translates maximization to a negated minimization model for
        # highspy, so translate the native dual bound back to our public
        # maximization objective direction as well.
        if best_bound is not None and built.problem.sense == pulp.LpMaximize:
            best_bound = -best_bound
    elif name == "scip":
        native_status = str(model.getStatus())
        candidate_available = bool(
            model.getNSols() > 0
            and finite_values
            and objective is not None
            and _status_supports_solver_evidence(name, native_status)
        )
        if _status_supports_solver_evidence(name, native_status):
            best_bound = _bounded_native_value(
                model.getDualbound(), infinity=model.infinity()
            )
    else:
        native_status = pulp.LpStatus[built.problem.status]
        candidate_available = bool(
            native_status in {"Optimal", "Not Solved"}
            and finite_values
            and objective is not None
        )

    return (
        SolverEvidence(
            solver_name={"cbc": "CBC", "highs": "HiGHS", "scip": "SCIP"}[name],
            native_status=native_status,
            termination_reason=native_status,
            has_incumbent=False,
            best_bound=best_bound,
            options={
                "threads": options.threads,
                "time_limit_seconds": options.time_limit_seconds,
                "relative_gap": options.relative_gap,
            },
        ),
        candidate_available,
    )


def _extract_solution(
    benchmark: BenchmarkProblem,
    name: str,
    built: _BuiltModel,
    options: SolverOptions,
) -> RawMilpSolution:
    raw_z = {key: variable.value() for key, variable in built.z.items()}
    raw_a = {key: variable.value() for key, variable in built.a.items()}
    raw_y = {key: variable.value() for key, variable in built.y.items()}
    raw_slack = {key: variable.value() for key, variable in built.slack.items()}
    all_values = tuple(
        [*raw_z.values(), *raw_a.values(), *raw_y.values(), *raw_slack.values()]
    )
    evidence, candidate_available = _native_evidence(name, built, options, all_values)
    objective = _finite_or_none(pulp.value(built.problem.objective))
    if not candidate_available or objective is None:
        raise SolverSolveError(
            f"{evidence.solver_name} did not return an extractable incumbent "
            f"(native_status={evidence.native_status})",
            evidence,
        )

    try:
        z = {key: float(value) for key, value in raw_z.items()}
        a = {key: float(value) for key, value in raw_a.items()}
        y = {key: float(value) for key, value in raw_y.items()}
        slack = {key: float(value) for key, value in raw_slack.items()}
    except (TypeError, ValueError) as exc:
        raise SolverSolveError(
            f"{evidence.solver_name} incumbent extraction failed: {exc}", evidence
        ) from exc

    people, projects = benchmark.graph.people, benchmark.graph.projects
    entries = []
    for (i, j), selected in z.items():
        allocation = a[(i, j)]
        if selected > 0.5 and allocation >= benchmark.params.min_alloc - 1e-6:
            display_allocation = max(
                benchmark.params.min_alloc, _floor2(allocation)
            )
            entries.append(
                AssignEntry(
                    person_id=people[i].id,
                    project_id=projects[j].id,
                    alloc=round(display_allocation, 2),
                )
            )
    unfilled = [
        f"{projects[j].id}:{grade.value}:{int(round(value))}명 미충원"
        for (j, grade), value in slack.items()
        if value > 0.5
    ]
    plan = PlanAssignment(
        entries=entries,
        objective=objective,
        unfilled=unfilled,
        violations=[],
        label="A",
    )
    candidate = RawMilpSolution(
        plan=plan,
        status=pulp.LpStatus[built.problem.status],
        objective=objective,
        z=z,
        a=a,
        y=y,
        slack=slack,
        reward_pairs=built.reward_pairs,
        penalty_pairs=built.penalty_pairs,
        variable_count=len(built.problem.variables()),
        constraint_count=len(built.problem.constraints),
        evidence=evidence,
    )
    validation = validate_raw_solution(
        benchmark.graph,
        benchmark.S,
        benchmark.C,
        benchmark.params,
        candidate,
    )
    if not validation.valid:
        issue_codes = ",".join(sorted({issue.code for issue in validation.issues}))
        failed_evidence = replace(
            evidence,
            termination_reason=(
                f"{evidence.native_status}; independent_validation_failed:{issue_codes}"
            ),
            has_incumbent=False,
        )
        raise SolverSolveError(
            f"{evidence.solver_name} returned an invalid incumbent candidate "
            f"({issue_codes})",
            failed_evidence,
        )

    best_bound = evidence.best_bound
    if best_bound is not None and best_bound < objective - 1e-6:
        best_bound = None
    validated_evidence = replace(
        evidence,
        has_incumbent=True,
        best_bound=best_bound,
    )
    return replace(candidate, evidence=validated_evidence)


def solve_case(
    problem: BenchmarkProblem, solver_name: str, options: SolverOptions
) -> RawMilpSolution:
    normalized_name = solver_name.lower()
    availability = available_solvers()
    if normalized_name not in availability:
        raise ValueError(f"unknown solver: {solver_name}")
    record = availability[normalized_name]
    if record.state is SolverAvailabilityState.UNAVAILABLE:
        raise SolverUnavailableError(
            f"{solver_name} unavailable ({record.import_name}): {record.error}"
        )

    built = _build_model(problem, _PulpFactory())
    solver = _solver_for(normalized_name, options)
    try:
        built.problem.solve(solver)
    except (ImportError, ModuleNotFoundError, OSError, pulp.PulpSolverError) as exc:
        evidence = SolverEvidence(
            solver_name=solver_name,
            native_status="SOLVER_SETUP_ERROR",
            termination_reason=f"{type(exc).__name__}: {exc}",
            has_incumbent=False,
            best_bound=None,
            options={
                "threads": options.threads,
                "time_limit_seconds": options.time_limit_seconds,
                "relative_gap": options.relative_gap,
            },
        )
        raise SolverSolveError(str(exc), evidence) from exc
    return _extract_solution(problem, normalized_name, built, options)
