import math
import numpy as np
import pulp
from pydantic import BaseModel
from core.graph.memory_graph import MemoryGraph
from core.optimize.types import AssignEntry, PlanAssignment


def _floor2(v: float) -> float:
    """Floor to 2 decimals (with a tiny epsilon guard against float repr noise).

    Never rounds *up*: nearest-rounding a CBC solution value can push alloc
    just over its true LP value (e.g. 0.239 -> round(.,2) -> 0.24), which can
    tip a budget/availability sum that CBC solved exactly at the boundary
    into a reported violation. Flooring guarantees reported alloc <= true
    solved alloc, so any constraint CBC satisfied stays satisfied after
    rounding for display.
    """
    return math.floor(v * 100 + 1e-9) / 100


class MilpParams(BaseModel):
    lam: float = 0.3
    mu: float = 0.2
    min_alloc: float = 0.2
    clique_threshold_months: int = 6
    pair_keep_ratio: float = 0.15
    slack_penalty: float = 100.0
    time_limit: int = 120
    gap: float = 0.05


def pruned_pairs(C: np.ndarray, keep_ratio: float) -> list[tuple[int, int]]:
    n = C.shape[0]
    all_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    k = int(keep_ratio * len(all_pairs))
    return sorted(all_pairs, key=lambda p: -abs(C[p]))[:k]


def solve_milp(graph: MemoryGraph, S: np.ndarray, C: np.ndarray,
               params: MilpParams, extra_constraints=None) -> PlanAssignment:
    people, projects = graph.people, graph.projects
    nP, nJ = len(people), len(projects)
    prob = pulp.LpProblem("teamweaver", pulp.LpMaximize)
    z = pulp.LpVariable.dicts("z", (range(nP), range(nJ)), cat="Binary")
    a = pulp.LpVariable.dicts("a", (range(nP), range(nJ)), 0.0, 1.0)
    pairs = pruned_pairs(C, params.pair_keep_ratio)
    y = {(p, q, j): pulp.LpVariable(f"y_{p}_{q}_{j}", 0.0, 1.0)
         for (p, q) in pairs for j in range(nJ)}
    grades = sorted({g for pj in projects for g in pj.grade_headcount}, key=lambda g: g.value)
    slack = {(j, g): pulp.LpVariable(f"s_{j}_{g.value}", lowBound=0)
             for j, pj in enumerate(projects) for g in pj.grade_headcount}
    overfam = {(p, q) for (p, q) in pairs
               if graph.cowork_months[p, q] >= params.clique_threshold_months}

    prob += (
        pulp.lpSum(S[i, j] * a[i][j] for i in range(nP) for j in range(nJ))
        + params.lam * pulp.lpSum(C[p, q] * y[(p, q, j)] for (p, q) in pairs for j in range(nJ))
        - params.mu * pulp.lpSum(y[(p, q, j)] for (p, q) in overfam for j in range(nJ))
        - params.slack_penalty * pulp.lpSum(slack.values()))

    for i in range(nP):
        for j in range(nJ):
            prob += a[i][j] <= z[i][j]
            prob += a[i][j] >= params.min_alloc * z[i][j]
    for (p, q) in pairs:
        for j in range(nJ):
            prob += y[(p, q, j)] <= z[p][j]
            prob += y[(p, q, j)] <= z[q][j]
            prob += y[(p, q, j)] >= z[p][j] + z[q][j] - 1
    for i, person in enumerate(people):                     # 제약 1: 월별 가동률
        for m in range(len(person.availability)):
            active = [j for j, pj in enumerate(projects) if m in pj.months]
            if active:
                prob += pulp.lpSum(a[i][j] for j in active) <= person.availability[m]
    for j, pj in enumerate(projects):                       # 제약 2: 등급 정원 + slack
        for g, need in pj.grade_headcount.items():
            members = [i for i, pe in enumerate(people) if pe.grade == g]
            prob += pulp.lpSum(z[i][j] for i in members) + slack[(j, g)] == need
    for j, pj in enumerate(projects):                       # 제약 3: 월 예산
        prob += pulp.lpSum(people[i].monthly_rate * a[i][j] for i in range(nP)) \
                <= pj.monthly_budget
    if extra_constraints:
        extra_constraints(prob, z)

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=params.time_limit, gapRel=params.gap))
    if pulp.LpStatus[prob.status] not in ("Optimal", "Not Solved"):
        raise RuntimeError(f"MILP failed: {pulp.LpStatus[prob.status]}")

    entries = []
    for i in range(nP):
        for j in range(nJ):
            zval, aval = z[i][j].value(), a[i][j].value()
            if zval and zval > 0.5 and aval is not None and aval >= params.min_alloc - 1e-6:
                # floor (not nearest-round) so reported alloc never exceeds the
                # true solved value; clamp up to min_alloc for the rare case CBC's
                # value sits an epsilon below it (see _floor2 docstring above).
                alloc = max(params.min_alloc, _floor2(aval))
                entries.append(AssignEntry(person_id=people[i].id, project_id=projects[j].id,
                                           alloc=round(alloc, 2)))
    unfilled = [f"{projects[j].id}:{g.value}:{int(round(v.value()))}명 미충원"
                for (j, g), v in slack.items() if v.value() and v.value() > 0.5]
    return PlanAssignment(entries=entries, objective=float(pulp.value(prob.objective)),
                          unfilled=unfilled, violations=[], label="A")
