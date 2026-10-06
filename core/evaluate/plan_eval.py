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
from core.optimize.milp import MilpParams, _overfamiliar_pairs, partner_floor_on, partner_map, pruned_pairs
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


# claude-b's monthly-allocation layer (api/monthly_eval.py on feat/claude-b-monthly-alloc) checks this flag and stops
# recomputing per-month violations itself once the evaluator handles monthly_alloc.
SUPPORTS_MONTHLY_ALLOC = True
MONTHLY_MEAN_TOL = 1e-5        # alloc may be the 6-decimal floored mean of the monthly values (plan_entries)

def evaluate_plan(graph: MemoryGraph, S: np.ndarray, C: np.ndarray, params: MilpParams,
                  entries: list[AssignEntry]) -> PlanEvaluation:
    people, projects = graph.people, graph.projects
    pdx, jdx = graph.pid_index, graph.project_index

    alloc: dict[tuple[int, int], float] = {}
    # 달별 투입률(claude-b 월별 투입률 작업, 2026-10-05 요청): 항목에 monthly_alloc {진행 달: 투입률}이 있으면
    # 가용률·월 예산·투입률 범위를 달별로 검사하고 기술항은 진행 달 평균으로 계산한다. 없으면 이전과 비트 단위로 같다.
    monthly: dict[tuple[int, int], dict[int, float]] = {}
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
        m_alloc = getattr(e, "monthly_alloc", None)
        if m_alloc:                                  # None and {} both mean "same allocation every month"
            months = set(projects[key[1]].months)
            if not all(isinstance(m, int) and not isinstance(m, bool) for m in m_alloc):
                raise ValueError(f"monthly_alloc keys must be integer months for {e.person_id} on {e.project_id}")
            given = {m: float(v) for m, v in m_alloc.items()}
            if set(given) != months:
                raise ValueError(f"monthly_alloc for {e.person_id} on {e.project_id} must cover exactly the "
                                 f"project months {sorted(months)}, got {sorted(given)}")
            if not all(math.isfinite(v) for v in given.values()):
                raise ValueError(f"non-finite monthly_alloc for {e.person_id} on {e.project_id}")
            mean = sum(given.values()) / len(given)
            if abs(mean - e.alloc) > MONTHLY_MEAN_TOL:
                raise ValueError(f"alloc {e.alloc} of {e.person_id} on {e.project_id} is not the mean "
                                 f"{mean:.6f} of its monthly_alloc")
            monthly[key] = given

    def month_alloc(i: int, j: int, month: int) -> float:
        return monthly[(i, j)][month] if (i, j) in monthly else alloc[(i, j)]

    members: dict[int, set[int]] = {}
    for i, j in alloc:
        members.setdefault(j, set()).add(i)

    def together(p: int, q: int, j: int) -> bool:
        team = members.get(j, ())
        return p in team and q in team

    skill = sum(float(S[i, j]) * (sum(monthly[(i, j)].values()) / len(monthly[(i, j)]) if (i, j) in monthly else a)
                for (i, j), a in alloc.items()) \
        + params.seat_fit_weight * sum(float(S[i, j]) for (i, j) in alloc)      # per-seat fit, same as the MILP
    reward = pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)
    penalty = _overfamiliar_pairs(graph, params.clique_threshold_months, getattr(params, "clique_window_months", None))
    synergy = params.lam * sum(float(C[p, q]) for p, q in reward
                               for j in members if together(p, q, j))
    overfam = -params.mu * sum(1 for p, q in penalty for j in members if together(p, q, j))
    if partner_floor_on(params):                    # 실험 G: 팀 안에 새 파트너가 partner_floor명보다 적으면 감점
        partners = partner_map(penalty)
        overfam -= params.partner_floor_weight * sum(
            max(0, params.partner_floor - (len(team) - 1 - len(partners[i] & team)))
            for team in members.values() for i in team if i in partners)

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
        if not any((i, j) in monthly for i in team):
            cost = sum(people[i].monthly_rate * alloc[(i, j)] for i in team)
            if cost > project.monthly_budget + TOL:
                violations.append(PlanViolation(
                    "budget", project.id, cost, project.monthly_budget,
                    f"{project.id} 월 비용 {cost:,.0f}이 예산 {project.monthly_budget:,}을 초과"))
        else:
            # Per-month location (j:monthM) only when someone on this team has monthly values. A swap passes the
            # leaver's monthly_alloc to the newcomer (whatif._swapped_entries), so before/after stay comparable.
            for month in project.months:
                cost = sum(people[i].monthly_rate * month_alloc(i, j, month) for i in team)
                if cost > project.monthly_budget + TOL:
                    violations.append(PlanViolation(
                        "budget", f"{project.id}:month{month}", cost, project.monthly_budget,
                        f"{project.id} {month + 1}번째 달 비용 {cost:,.0f}이 예산 {project.monthly_budget:,}을 초과"))

    for i, person in enumerate(people):
        for month, available in enumerate(person.availability):
            load = sum(month_alloc(pi, j, month) for (pi, j) in alloc
                       if pi == i and month in projects[j].months)
            if load > available + TOL:
                violations.append(PlanViolation(
                    "availability", f"{person.id}:month{month}", load, available,
                    f"{person.id}의 계획 {month + 1}번째 달 투입 합 {load:.2f}가 "
                    f"가용률 {available:.2f}를 초과"))

    # C6 동시 프로젝트 상한(사용자 답변: 최대 3개·보통 1개). 서비스 MILP 제약과 같은 규칙으로, 교체 검토·적용·PDF가
    # 상한을 넘는 명단을 "위반 없음"으로 보이지 않게 한다(claude-b 요청, 2026-10-05).
    limit = params.max_concurrent_projects
    concurrent: dict[tuple[int, int], int] = {}
    for (i, j) in alloc:
        for month in projects[j].months:
            concurrent[(i, month)] = concurrent.get((i, month), 0) + 1
    for (i, month), n in sorted(concurrent.items()):
        if n > limit:
            violations.append(PlanViolation(
                "concurrent_projects", f"{people[i].id}:month{month}", float(n), float(limit),
                f"{people[i].id}의 계획 {month + 1}번째 달 동시 프로젝트 {n}개가 상한 {limit}개를 초과"))

    for (i, j), a in alloc.items():
        if (i, j) in monthly:
            for month, v in sorted(monthly[(i, j)].items()):
                if v < params.min_alloc - TOL or v > 1.0 + TOL:
                    violations.append(PlanViolation(
                        "alloc_range", f"{people[i].id}:{projects[j].id}:month{month}", v, params.min_alloc,
                        f"{people[i].id}의 {projects[j].id} {month + 1}번째 달 투입률 {v:.2f}가 허용 범위"
                        f"({params.min_alloc:.2f}~1.00) 밖"))
            continue
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
