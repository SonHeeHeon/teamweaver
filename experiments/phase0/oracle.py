from dataclasses import dataclass
import time

import numpy as np
from scipy.optimize import linprog

from core.domain.models import Grade
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams

ORACLE_GUARD_SECONDS = 0.25


class OracleDeadlineExceeded(TimeoutError):
    """Raised before the bounded oracle can cross the enclosing suite deadline."""


def _remaining_oracle_time(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - time.perf_counter() - ORACLE_GUARD_SECONDS
    if remaining <= 0:
        raise OracleDeadlineExceeded("oracle deadline exceeded")
    return remaining


@dataclass(frozen=True)
class OracleResult:
    objective: float
    z: dict[tuple[int, int], int]
    a: dict[tuple[int, int], float]
    slack: dict[tuple[int, Grade], float]
    reward_pairs: tuple[tuple[int, int], ...]
    penalty_pairs: tuple[tuple[int, int], ...]
    enumerated_assignments: int


def _reward_pairs(synergy: np.ndarray, params: MilpParams) -> tuple[tuple[int, int], ...]:
    candidates = [
        (i, j)
        for i in range(synergy.shape[0])
        for j in range(i + 1, synergy.shape[0])
    ]
    keep = int(params.pair_keep_ratio * len(candidates))
    keep = min(keep, params.max_pairs, len(candidates))
    ranked = sorted(candidates, key=lambda pair: -abs(float(synergy[pair])))
    return tuple(ranked[:keep])


def _penalty_pairs(graph: MemoryGraph, params: MilpParams) -> tuple[tuple[int, int], ...]:
    return tuple(
        (i, j)
        for i in range(len(graph.people))
        for j in range(i + 1, len(graph.people))
        if float(graph.cowork_months[i, j]) >= params.clique_threshold_months
    )


def solve_tiny_oracle(
    graph: MemoryGraph,
    skill: np.ndarray,
    synergy: np.ndarray,
    params: MilpParams,
    deadline: float | None = None,
) -> OracleResult:
    """Solve a tiny base model independently by enumerating every binary z."""
    people, projects = graph.people, graph.projects
    n_people, n_projects = len(people), len(projects)
    decision_count = n_people * n_projects
    if decision_count > 12:
        raise ValueError("tiny oracle supports at most 12 person-project decisions")

    reward_pairs = _reward_pairs(synergy, params)
    penalty_pairs = _penalty_pairs(graph, params)
    best_objective = -np.inf
    best_z: dict[tuple[int, int], int] | None = None
    best_a: dict[tuple[int, int], float] | None = None
    best_slack: dict[tuple[int, Grade], float] | None = None
    feasible_count = 0

    for mask in range(1 << decision_count):
        _remaining_oracle_time(deadline)
        z = {
            (i, j): (mask >> (i * n_projects + j)) & 1
            for i in range(n_people)
            for j in range(n_projects)
        }
        slack: dict[tuple[int, Grade], float] = {}
        grade_feasible = True
        for j, project in enumerate(projects):
            for grade, required in project.grade_headcount.items():
                assigned = sum(
                    z[(i, j)] for i, person in enumerate(people) if person.grade == grade
                )
                if assigned > required:
                    grade_feasible = False
                    break
                slack[(j, grade)] = float(required - assigned)
            if not grade_feasible:
                break
        if not grade_feasible:
            continue
        # C6: 한 사람이 같은 달에 맡는 프로젝트 수 상한 -- 서비스·벤치 MILP와 같은 규칙.
        if any(
            sum(z[(i, j)] for j, project in enumerate(projects) if month in project.months)
            > params.max_concurrent_projects
            for i, person in enumerate(people)
            for month in range(len(person.availability))
        ):
            continue

        a_ub: list[list[float]] = []
        b_ub: list[float] = []
        for i, person in enumerate(people):
            for month, availability in enumerate(person.availability):
                row = [0.0] * decision_count
                for j, project in enumerate(projects):
                    if month in project.months:
                        row[i * n_projects + j] = 1.0
                if any(row):
                    a_ub.append(row)
                    b_ub.append(float(availability))
        for j, project in enumerate(projects):
            row = [0.0] * decision_count
            for i, person in enumerate(people):
                row[i * n_projects + j] = float(person.monthly_rate)
            a_ub.append(row)
            b_ub.append(float(project.monthly_budget))

        bounds = [
            (params.min_alloc, 1.0) if z[(i, j)] else (0.0, 0.0)
            for i in range(n_people)
            for j in range(n_projects)
        ]
        remaining = _remaining_oracle_time(deadline)
        result = linprog(
            c=-np.asarray(skill, dtype=float).reshape(-1),
            A_ub=np.asarray(a_ub, dtype=float) if a_ub else None,
            b_ub=np.asarray(b_ub, dtype=float) if b_ub else None,
            bounds=bounds,
            method="highs",
            options={"time_limit": remaining} if remaining is not None else None,
        )
        if result.status == 1:
            raise OracleDeadlineExceeded("oracle linprog time limit reached")
        if not result.success:
            continue
        feasible_count += 1
        allocations = {
            (i, j): float(result.x[i * n_projects + j])
            for i in range(n_people)
            for j in range(n_projects)
        }
        skill_term = -float(result.fun) + getattr(params, "seat_fit_weight", 0.0) * sum(
            float(skill[i, j]) * z[(i, j)] for i in range(n_people) for j in range(n_projects))
        reward_term = params.lam * sum(
            float(synergy[p, q]) * z[(p, j)] * z[(q, j)]
            for p, q in reward_pairs
            for j in range(n_projects)
        )
        penalty_term = -params.mu * sum(
            z[(p, j)] * z[(q, j)]
            for p, q in penalty_pairs
            for j in range(n_projects)
        )
        unfilled_term = -params.slack_penalty * sum(slack.values())
        objective = skill_term + reward_term + penalty_term + unfilled_term
        if objective > best_objective + 1e-12:
            best_objective = objective
            best_z = z
            best_a = allocations
            best_slack = slack

    _remaining_oracle_time(deadline)
    if best_z is None or best_a is None or best_slack is None:
        raise RuntimeError("tiny oracle found no feasible assignment")
    return OracleResult(
        objective=float(best_objective),
        z=best_z,
        a=best_a,
        slack=best_slack,
        reward_pairs=reward_pairs,
        penalty_pairs=penalty_pairs,
        enumerated_assignments=1 << decision_count,
    )
