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
    floored = math.floor(value * 100 + 1e-9) / 100
    return round(max(min_alloc, floored), 2)


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

    def equality(code: str, location: str, actual: float, expected: float) -> None:
        error = abs(actual - expected)
        if error > tol:
            issues.append(ValidationIssue(code, location, actual, expected, error))

    def upper(code: str, location: str, actual: float, limit: float) -> None:
        error = actual - limit
        if error > tol:
            issues.append(ValidationIssue(code, location, actual, limit, error))

    def lower(code: str, location: str, actual: float, limit: float) -> None:
        error = limit - actual
        if error > tol:
            issues.append(ValidationIssue(code, location, actual, limit, error))

    for i in range(n_people):
        for j in range(n_projects):
            z_value = solution.z.get((i, j), 0.0)
            a_value = solution.a.get((i, j), 0.0)
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
                solution.a.get((i, j), 0.0)
                for j, project in enumerate(projects)
                if month in project.months
            )
            upper("availability", f"person={person.id},month={month}", load, availability)

    for j, project in enumerate(projects):
        for grade, required in project.grade_headcount.items():
            assigned = sum(
                solution.z.get((i, j), 0.0)
                for i, person in enumerate(people)
                if person.grade == grade
            )
            slack = solution.slack.get((j, grade), 0.0)
            lower("slack_nonnegative", f"project={project.id},grade={grade.value}", slack, 0.0)
            equality(
                "grade_headcount",
                f"project={project.id},grade={grade.value}",
                assigned + slack,
                float(required),
            )

        cost = sum(
            person.monthly_rate * solution.a.get((i, j), 0.0)
            for i, person in enumerate(people)
        )
        upper("budget", f"project={project.id}", cost, float(project.monthly_budget))

    expected_reward_pairs = _independent_reward_pairs(synergy, params)
    expected_penalty_pairs = _independent_penalty_pairs(graph, params)

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

    expected_y_keys = {
        (p, q, j)
        for p, q in set(expected_reward_pairs) | set(expected_penalty_pairs)
        for j in range(n_projects)
    }
    equality("pair_key_count", "y", float(len(solution.y)), float(len(expected_y_keys)))
    for p, q, j in expected_y_keys:
        expected = solution.z.get((p, j), 0.0) * solution.z.get((q, j), 0.0)
        equality("pair_product", f"y[{p},{q},{j}]", solution.y.get((p, q, j), 0.0), expected)

    skill_term = sum(
        float(skill[i, j]) * solution.a.get((i, j), 0.0)
        for i in range(n_people)
        for j in range(n_projects)
    )
    synergy_term = params.lam * sum(
        float(synergy[p, q]) * solution.y.get((p, q, j), 0.0)
        for p, q in expected_reward_pairs
        for j in range(n_projects)
    )
    overfamiliarity_term = -params.mu * sum(
        solution.y.get((p, q, j), 0.0)
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
        if solution.z.get((i, j), 0.0) > 0.5
        and solution.a.get((i, j), 0.0) >= params.min_alloc - tol
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
