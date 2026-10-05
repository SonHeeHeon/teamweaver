import math

import numpy as np

from core.graph.memory_graph import MemoryGraph
from core.optimize.audit_types import (
    ObjectiveBreakdown,
    RawMilpSolution,
    ValidationIssue,
    ValidationReport,
)
from core.optimize.milp import MilpParams


def _display_alloc(value: float, min_alloc: float) -> float:
    """Independent copy of the display rule (C3): never above the solved value.
    Two-decimal floor, unless that drops below a finer min_alloc -- then keep
    the value at 6 decimals (clamped to min_alloc only within tolerance)."""
    floored = math.floor(value * 100 + 1e-9) / 100
    if floored >= min_alloc - 1e-12:
        return floored
    return max(math.floor(value * 1_000_000 + 1e-9) / 1_000_000, min_alloc)


def _independent_reward_pairs(
    synergy: np.ndarray, params: MilpParams
) -> tuple[tuple[int, int], ...]:
    """Derive the reward scope without reusing the production pruning helper."""
    candidates = [
        (abs(float(synergy[p, q])), p, q)
        for p in range(synergy.shape[0])
        for q in range(p + 1, synergy.shape[0])
    ]
    keep = min(
        int(params.pair_keep_ratio * len(candidates)),
        params.max_pairs,
        len(candidates),
    )
    ranked = sorted(candidates, key=lambda candidate: (-candidate[0], candidate[1], candidate[2]))
    return tuple((p, q) for _, p, q in ranked[:max(0, keep)])


def _independent_penalty_pairs(
    graph: MemoryGraph, params: MilpParams
) -> tuple[tuple[int, int], ...]:
    """Derive every over-familiar pair by scanning graph data directly."""
    return tuple(
        (p, q)
        for p in range(len(graph.people))
        for q in range(p + 1, len(graph.people))
        if float(graph.cowork_months[p, q]) >= params.clique_threshold_months
    )


def validate_raw_solution(
    graph: MemoryGraph,
    skill: np.ndarray,
    synergy: np.ndarray,
    params: MilpParams,
    solution: RawMilpSolution,
    tol: float = 1e-6,
) -> ValidationReport:
    """Recompute the base MILP contract without consulting PuLP constraints."""
    people, projects = graph.people, graph.projects
    n_people, n_projects = len(people), len(projects)
    issues: list[ValidationIssue] = []

    expected_reward_pairs = _independent_reward_pairs(synergy, params)
    expected_penalty_pairs = _independent_penalty_pairs(graph, params)
    expected_z_keys = {(i, j) for i in range(n_people) for j in range(n_projects)}
    expected_a_keys = expected_z_keys
    expected_slack_keys = {
        (j, grade)
        for j, project in enumerate(projects)
        for grade in project.grade_headcount
    }
    expected_y_keys = {
        (p, q, j)
        for p, q in set(expected_reward_pairs) | set(expected_penalty_pairs)
        for j in range(n_projects)
    }

    def raw_issue(code: str, location: str, actual: float = 0.0) -> None:
        issues.append(ValidationIssue(code, location, actual, 1.0, 1.0))

    def check_raw_values(
        field: str,
        values: dict,
        expected_keys: set,
        domain: str,
    ) -> None:
        for key in expected_keys - set(values):
            raw_issue("missing_key", f"{field}[{key}]")
        for key in set(values) - expected_keys:
            raw_issue("unexpected_key", f"{field}[{key}]")
        for key in expected_keys & set(values):
            value = values[key]
            location = f"{field}[{key}]"
            if value is None:
                raw_issue("missing_value", location)
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raw_issue("nonfinite_value", location)
                continue
            numeric_value = float(value)
            if domain == "binary" and (
                numeric_value < 0.0
                or numeric_value > 1.0
                or min(abs(numeric_value), abs(numeric_value - 1.0)) > tol
            ):
                raw_issue("binary_domain", location, numeric_value)
            elif domain == "unit" and (
                numeric_value < 0.0 or numeric_value > 1.0
            ):
                raw_issue("unit_interval", location, numeric_value)
            elif domain == "nonnegative" and numeric_value < 0.0:
                raw_issue("slack_nonnegative", location, numeric_value)

    # Check the complete raw variable contract before feasibility, objective, or
    # display validation. Missing values must never be interpreted as zero.
    check_raw_values("z", solution.z, expected_z_keys, "binary")
    check_raw_values("a", solution.a, expected_a_keys, "unit")
    check_raw_values("slack", solution.slack, expected_slack_keys, "nonnegative")
    check_raw_values("y", solution.y, expected_y_keys, "unit")
    for location, value in (
        ("objective", solution.objective),
        ("plan.objective", solution.plan.objective),
    ):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raw_issue("nonfinite_value", location)
    seen_entries = set()
    for index, entry in enumerate(solution.plan.entries):
        if not isinstance(entry.alloc, (int, float)) or not math.isfinite(entry.alloc):
            raw_issue("nonfinite_value", f"plan.entries[{index}].alloc")
        key = (entry.person_id, entry.project_id)
        if key in seen_entries:
            raw_issue("duplicate_plan_entry", f"plan.entries[{index}]")
        seen_entries.add(key)
    if issues:
        empty_objective = ObjectiveBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)
        return ValidationReport(
            valid=False,
            issues=tuple(issues),
            objective=empty_objective,
            solver_objective_error=float("inf"),
        )

    def equality(code: str, location: str, actual: float, expected: float) -> None:
        error = abs(actual - expected)
        if not math.isfinite(error) or error > tol:
            issues.append(ValidationIssue(code, location, actual, expected, error))

    def upper(code: str, location: str, actual: float, limit: float) -> None:
        error = actual - limit
        if not math.isfinite(error) or error > tol:
            issues.append(ValidationIssue(code, location, actual, limit, error))

    def lower(code: str, location: str, actual: float, limit: float) -> None:
        error = limit - actual
        if not math.isfinite(error) or error > tol:
            issues.append(ValidationIssue(code, location, actual, limit, error))

    for i in range(n_people):
        for j in range(n_projects):
            z_value = solution.z[(i, j)]
            a_value = solution.a[(i, j)]
            equality("binary_assignment", f"z[{i},{j}]", z_value, round(z_value))
            upper("allocation_upper", f"a[{i},{j}]<=z", a_value, z_value)
            lower(
                "allocation_lower",
                f"a[{i},{j}]>=min_alloc*z",
                a_value,
                params.min_alloc * z_value,
            )

    for i, person in enumerate(people):
        for month, availability in enumerate(person.availability):
            load = sum(
                solution.a[(i, j)]
                for j, project in enumerate(projects)
                if month in project.months
            )
            upper("availability", f"person={person.id},month={month}", load, availability)
            count = sum(
                solution.z[(i, j)]
                for j, project in enumerate(projects)
                if month in project.months
            )
            upper(
                "concurrent_projects",
                f"person={person.id},month={month}",
                count,
                float(params.max_concurrent_projects),
            )

    for j, project in enumerate(projects):
        for grade, required in project.grade_headcount.items():
            assigned = sum(
                solution.z[(i, j)]
                for i, person in enumerate(people)
                if person.grade == grade
            )
            slack = solution.slack[(j, grade)]
            lower("slack_nonnegative", f"project={project.id},grade={grade.value}", slack, 0.0)
            equality(
                "grade_headcount",
                f"project={project.id},grade={grade.value}",
                assigned + slack,
                float(required),
            )

        cost = sum(
            person.monthly_rate * solution.a[(i, j)]
            for i, person in enumerate(people)
        )
        upper("budget", f"project={project.id}", cost, float(project.monthly_budget))

    def check_pair_scope(
        code: str,
        actual_pairs: tuple[tuple[int, int], ...],
        expected_pairs: tuple[tuple[int, int], ...],
    ) -> None:
        actual_set, expected_set = set(actual_pairs), set(expected_pairs)
        if actual_set != expected_set or len(actual_pairs) != len(expected_pairs):
            issues.append(
                ValidationIssue(
                    code,
                    "raw_diagnostics",
                    float(len(actual_pairs)),
                    float(len(expected_pairs)),
                    float(max(1, len(actual_set ^ expected_set))),
                )
            )

    check_pair_scope("reward_pair_scope", solution.reward_pairs, expected_reward_pairs)
    check_pair_scope("penalty_pair_scope", solution.penalty_pairs, expected_penalty_pairs)

    equality("pair_key_count", "y", float(len(solution.y)), float(len(expected_y_keys)))
    for p, q, j in expected_y_keys:
        expected = solution.z[(p, j)] * solution.z[(q, j)]
        equality("pair_product", f"y[{p},{q},{j}]", solution.y[(p, q, j)], expected)

    skill_term = sum(
        float(skill[i, j]) * solution.a[(i, j)]
        for i in range(n_people)
        for j in range(n_projects)
    ) + getattr(params, "seat_fit_weight", 0.0) * sum(          # per-seat fit (roadmap 3), 0 = previous objective
        float(skill[i, j]) * solution.z[(i, j)]
        for i in range(n_people)
        for j in range(n_projects)
    )
    synergy_term = params.lam * sum(
        float(synergy[p, q]) * solution.y[(p, q, j)]
        for p, q in expected_reward_pairs
        for j in range(n_projects)
    )
    overfamiliarity_term = -params.mu * sum(
        solution.y[(p, q, j)]
        for p, q in expected_penalty_pairs
        for j in range(n_projects)
    )
    unfilled_term = -params.slack_penalty * sum(solution.slack.values())
    total = skill_term + synergy_term + overfamiliarity_term + unfilled_term
    objective = ObjectiveBreakdown(
        skill=skill_term,
        synergy=synergy_term,
        overfamiliarity=overfamiliarity_term,
        unfilled=unfilled_term,
        total=total,
    )
    objective_error = abs(total - solution.objective)
    equality("objective", "solver", total, solution.objective)
    equality("plan_objective", "plan", solution.plan.objective, solution.objective)

    expected_entries = {
        (people[i].id, projects[j].id): _display_alloc(solution.a[(i, j)], params.min_alloc)
        for i in range(n_people)
        for j in range(n_projects)
        if solution.z[(i, j)] > 0.5
        and solution.a[(i, j)] >= params.min_alloc - tol
    }
    actual_entries = {
        (entry.person_id, entry.project_id): entry.alloc for entry in solution.plan.entries
    }
    equality(
        "plan_entry_count", "plan.entries", float(len(actual_entries)), float(len(expected_entries))
    )
    for key in set(expected_entries) | set(actual_entries):
        equality(
            "plan_allocation",
            f"person={key[0]},project={key[1]}",
            actual_entries.get(key, -1.0),
            expected_entries.get(key, -1.0),
        )

    expected_unfilled = sorted(
        f"{projects[j].id}:{grade.value}:{int(round(value))}명 미충원"
        for (j, grade), value in solution.slack.items()
        if value > 0.5
    )
    equality(
        "unfilled_count",
        "plan.unfilled",
        float(len(solution.plan.unfilled)),
        float(len(expected_unfilled)),
    )
    if sorted(solution.plan.unfilled) != expected_unfilled:
        issues.append(
            ValidationIssue(
                "unfilled_content",
                "plan.unfilled",
                float(len(solution.plan.unfilled)),
                float(len(expected_unfilled)),
                1.0,
            )
        )

    return ValidationReport(
        valid=not issues,
        issues=tuple(issues),
        objective=objective,
        solver_objective_error=objective_error,
    )
