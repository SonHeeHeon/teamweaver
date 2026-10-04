"""Score an arbitrary display plan with the production MILP's objective and constraints.

Used where a plan did not come straight from the solver (e.g. a What-if swap), so the
solver's own guarantees do not apply. Pair scopes are taken from core.optimize.milp so the
synergy/penalty terms cover exactly the pairs the MILP counts.
"""
import math
from dataclasses import dataclass

import numpy as np

from core.graph.memory_graph import MemoryGraph
from core.optimize.audit_types import ObjectiveBreakdown
from core.optimize.milp import MilpParams, _overfamiliar_pairs, pruned_pairs
from core.optimize.types import AssignEntry

TOL = 1e-6


@dataclass(frozen=True)
class PlanViolation:
    code: str
    location: str
    actual: float
    limit: float
    message: str


@dataclass(frozen=True)
class GradeShortfall:
    """An unfilled grade slot. Allowed by the MILP (slack) but penalized, so not a violation."""
    project_id: str
    grade: str
    missing: int


@dataclass(frozen=True)
class PlanEvaluation:
    objective: ObjectiveBreakdown
    violations: tuple[PlanViolation, ...]
    shortfalls: tuple[GradeShortfall, ...]


def evaluate_plan(graph: MemoryGraph, S: np.ndarray, C: np.ndarray, params: MilpParams,
                  entries: list[AssignEntry]) -> PlanEvaluation:
    people, projects = graph.people, graph.projects
    pdx, jdx = graph.pid_index, graph.project_index

    alloc: dict[tuple[int, int], float] = {}
    for e in entries:
        if e.person_id not in pdx:
            raise ValueError(f"unknown person_id: {e.person_id!r}")
        if e.project_id not in jdx:
            raise ValueError(f"unknown project_id: {e.project_id!r}")
        if not math.isfinite(e.alloc):
            raise ValueError(f"non-finite alloc for {e.person_id} on {e.project_id}")
        key = (pdx[e.person_id], jdx[e.project_id])
        if key in alloc:
            raise ValueError(f"duplicate assignment: {e.person_id} on {e.project_id}")
        alloc[key] = e.alloc

    members: dict[int, set[int]] = {}
    for i, j in alloc:
        members.setdefault(j, set()).add(i)

    def together(p: int, q: int, j: int) -> bool:
        team = members.get(j, ())
        return p in team and q in team

    skill = sum(float(S[i, j]) * a for (i, j), a in alloc.items())
    reward = pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)
    penalty = _overfamiliar_pairs(graph, params.clique_threshold_months)
    synergy = params.lam * sum(float(C[p, q]) for p, q in reward
                               for j in members if together(p, q, j))
    overfam = -params.mu * sum(1 for p, q in penalty for j in members if together(p, q, j))

    violations: list[PlanViolation] = []
    shortfalls: list[GradeShortfall] = []
    for j, project in enumerate(projects):
        team = members.get(j, set())
        for grade, need in project.grade_headcount.items():
            placed = sum(1 for i in team if people[i].grade == grade)
            if placed < need:
                shortfalls.append(GradeShortfall(project.id, grade.value, need - placed))
            if placed > need:
                violations.append(PlanViolation(
                    "grade_over", f"{project.id}:{grade.value}", placed, need,
                    f"{project.id}의 {grade.value} 배치 {placed}명이 요구 {need}명을 초과"))
        cost = sum(people[i].monthly_rate * alloc[(i, j)] for i in team)
        if cost > project.monthly_budget + TOL:
            violations.append(PlanViolation(
                "budget", project.id, cost, project.monthly_budget,
                f"{project.id} 월 비용 {cost:,.0f}이 예산 {project.monthly_budget:,}을 초과"))

    for i, person in enumerate(people):
        for month, available in enumerate(person.availability):
            load = sum(a for (pi, j), a in alloc.items()
                       if pi == i and month in projects[j].months)
            if load > available + TOL:
                violations.append(PlanViolation(
                    "availability", f"{person.id}:month{month}", load, available,
                    f"{person.id}의 {month}월 투입 합 {load:.2f}가 가용률 {available:.2f}를 초과"))

    for (i, j), a in alloc.items():
        if a < params.min_alloc - TOL or a > 1.0 + TOL:
            violations.append(PlanViolation(
                "alloc_range", f"{people[i].id}:{projects[j].id}", a, params.min_alloc,
                f"{people[i].id}의 {projects[j].id} 투입률 {a:.2f}가 허용 범위"
                f"({params.min_alloc:.2f}~1.00) 밖"))

    unfilled = -params.slack_penalty * sum(s.missing for s in shortfalls)
    objective = ObjectiveBreakdown(skill=skill, synergy=synergy, overfamiliarity=overfam,
                                   unfilled=unfilled,
                                   total=skill + synergy + overfam + unfilled)
    return PlanEvaluation(objective=objective, violations=tuple(violations),
                          shortfalls=tuple(shortfalls))
