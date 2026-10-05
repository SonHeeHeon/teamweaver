import numpy as np
import pulp
from core.config import DEFAULT_WEIGHT
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams
from core.optimize.types import PlanAssignment


def matching_fulfillment(graph: MemoryGraph, plan: PlanAssignment,
                         weights: dict[str, int]) -> float:
    """Calculate matching fulfillment per revised spec §4.4 (capacity-consuming).

    Each person's allocation a_ij is finite. If person i qualifies for multiple
    slots in project j, a_ij is split equally among them: each slot s receives
    a_ij / |Q_ij| where Q_ij = {slots person i qualifies for}.

    Formula: f_s = min(1, Σ_{i at j, s ∈ Q_ij} (a_ij / |Q_ij|) / h_s)
    Overall: Σ w_s·f_s / Σ w_s (weight 3.0 default)
    """
    by_pid = {p.id: p for p in graph.people}
    alloc: dict[str, list] = {}
    for e in plan.entries:
        alloc.setdefault(e.project_id, []).append(e)

    num = den = 0.0
    for proj in graph.projects:
        for rq in proj.requirements:
            w = float(weights.get(rq.skill, DEFAULT_WEIGHT))

            # For each slot, sum contributions from all placed persons
            got = 0.0
            for e in alloc.get(proj.id, []):
                person = by_pid.get(e.person_id)
                if person is None:
                    # Skip unknown person (defensive programming)
                    continue

                # Determine which slots this person qualifies for (Q_ij)
                qualified_slots = [
                    r for r in proj.requirements
                    if person.skills.get(r.skill, 0) >= r.min_level
                ]
                num_qualified = len(qualified_slots)

                if num_qualified == 0:
                    # Person doesn't qualify for any slot; contributes nothing
                    continue

                # Check if this person qualifies for current slot
                if person.skills.get(rq.skill, 0) >= rq.min_level:
                    # Contribution: alloc split among all qualified slots
                    got += e.alloc / num_qualified

            num += w * min(1.0, got / rq.headcount)
            den += w

    return num / den if den else 0.0


def _skill_relaxation_upper_bound(graph: MemoryGraph, S: np.ndarray, params: MilpParams) -> float:
    """LP-relaxation upper bound on the skill-only objective term Σ S_ij·a_ij.

    Same feasible region for (a_ij, z_ij) as `solve_milp` -- monthly availability,
    per-grade headcount (with unpenalized slack), monthly budget, and the
    semi-continuous a-z coupling (min_alloc·z <= a <= z) -- except z_ij is
    continuous in [0,1] instead of binary. The synergy reward, clique penalty,
    slack penalty, and the y_pqj linearization variables from `solve_milp` are
    omitted entirely: they don't appear in this skill-only objective, and
    because y only ever appears bounded *above* by z (y <= z_pj, y <= z_qj),
    never bounding z from below, dropping y/pair constraints changes neither
    the feasible region of (a, z) nor the optimal value of Σ S_ij·a_ij over it.

    Any real (a, z) produced by `solve_milp` (binary z, all the same other
    constraints) is feasible for this relaxation too, and this relaxation
    maximizes exactly the same summand -- so by LP-relaxation duality this
    value upper-bounds Σ S_ij·a_ij for *any* feasible integer plan, including
    ones optimized under `solve_milp`'s full objective (skill + synergy -
    penalty - slack_penalty), whose skill term alone can only be <= this UB.
    """
    people, projects = graph.people, graph.projects
    nP, nJ = len(people), len(projects)
    if nP == 0 or nJ == 0:
        return 0.0

    prob = pulp.LpProblem("teamweaver_skill_ub", pulp.LpMaximize)
    z = pulp.LpVariable.dicts("z", (range(nP), range(nJ)), 0.0, 1.0)  # relaxed (was Binary)
    if getattr(params, "allocation_mode", "fixed") == "monthly":
        return _monthly_skill_upper_bound(graph, S, params, prob, z)
    a = pulp.LpVariable.dicts("a", (range(nP), range(nJ)), 0.0, 1.0)
    slack = {(j, g): pulp.LpVariable(f"s_{j}_{g.value}", lowBound=0)
             for j, pj in enumerate(projects) for g in pj.grade_headcount}

    prob += pulp.lpSum(S[i, j] * a[i][j] for i in range(nP) for j in range(nJ))

    for i in range(nP):
        for j in range(nJ):
            prob += a[i][j] <= z[i][j]
            prob += a[i][j] >= params.min_alloc * z[i][j]
    for i, person in enumerate(people):                     # 제약 1: 월별 가동률
        for m in range(len(person.availability)):
            active = [j for j, pj in enumerate(projects) if m in pj.months]
            if active:
                prob += pulp.lpSum(a[i][j] for j in active) <= person.availability[m]
    for j, pj in enumerate(projects):                        # 제약 2: 등급별 정원 + slack
        for g, need in pj.grade_headcount.items():
            members = [i for i, pe in enumerate(people) if pe.grade == g]
            prob += pulp.lpSum(z[i][j] for i in members) + slack[(j, g)] == need
    for j, pj in enumerate(projects):                        # 제약 3: 월 예산
        prob += pulp.lpSum(people[i].monthly_rate * a[i][j] for i in range(nP)) \
                <= pj.monthly_budget

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise RuntimeError(f"skill-relaxation LP failed: {status}")
    return float(pulp.value(prob.objective))


def _monthly_skill_upper_bound(graph, S, params, prob, z) -> float:
    """월별 투입률 정식의 같은 LP 완화(리뷰 M1): 달별 a, 달별 가용률·예산, 기술항 S × 진행 달 평균.
    고정판 상한은 월별 해의 기술항보다 작을 수 있어 최적화율이 100%를 넘었다."""
    people, projects = graph.people, graph.projects
    nP = len(people)
    am = {(i, j, m): pulp.LpVariable(f"am_{i}_{j}_{m}", 0.0, 1.0)
          for i in range(nP) for j, pj in enumerate(projects) for m in pj.months}
    slack = {(j, g): pulp.LpVariable(f"s_{j}_{g.value}", lowBound=0)
             for j, pj in enumerate(projects) for g in pj.grade_headcount}
    prob += pulp.lpSum(S[i, j] * (1.0 / len(projects[j].months)) * v for (i, j, m), v in am.items())
    for (i, j, m), v in am.items():
        prob += v <= z[i][j]
        prob += v >= params.min_alloc * z[i][j]
    for i, person in enumerate(people):
        for m in range(len(person.availability)):
            active = [j for j, pj in enumerate(projects) if m in pj.months]
            if active:
                prob += pulp.lpSum(am[(i, j, m)] for j in active) <= person.availability[m]
    for j, pj in enumerate(projects):
        for g, need in pj.grade_headcount.items():
            members = [i for i, pe in enumerate(people) if pe.grade == g]
            prob += pulp.lpSum(z[i][j] for i in members) + slack[(j, g)] == need
        for m in pj.months:
            prob += pulp.lpSum(people[i].monthly_rate * am[(i, j, m)] for i in range(nP)) <= pj.monthly_budget
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        raise RuntimeError(f"skill-relaxation LP failed: {status}")
    return float(pulp.value(prob.objective))


def optimization_ratio(graph: MemoryGraph, S: np.ndarray, plan: PlanAssignment,
                       params: MilpParams) -> float:
    """헤드라인 지표: 최적화율 (spec §4.4, 2026-08-04 확정).

    최적화율 = (실 배치 plan의 스킬적합 목적값 Σ S_ij·a_ij) / UB
    UB = 동일 제약(가동률·정원+slack·예산·semi-continuous 결합) 하에서 z를 연속
    완화한 LP의 스킬 목적값 최적해 (_skill_relaxation_upper_bound).

    분자·분모 모두 skill term만 사용한다 -- synergy/clique-penalty/slack-penalty
    항은 제외(`solve_milp`의 전체 목적함수가 아니라 그 skill 부분만 비교 대상).
    UB는 plan이 만족해야 했던 것과 동일한 제약의 완화 문제 최적값이므로, 어떤
    feasible plan의 skill term도 이를 넘을 수 없다(LP relaxation duality) --
    ratio는 이론상 항상 <= 1.0.

    단, 이 보장은 plan이 모든 제약(특히 예산 제약 포함)을 **만족할 때만** 유효하다.
    Greedy 등의 근사 알고리즘이 내는 해가 제약을 위반하면 이 ratio는 보장의 대상이
    아니다. 실제 측정값들은 그 여부와 무관하게 모두 1.0 이하로 나타나지만, 정식적인
    ≤1.0 보장 자체는 feasible plan을 가정한다.
    """
    pidx = graph.pid_index
    jidx = graph.project_index
    numerator = sum(
        S[pidx[e.person_id], jidx[e.project_id]] * e.alloc
        for e in plan.entries
        if e.person_id in pidx and e.project_id in jidx)
    ub = _skill_relaxation_upper_bound(graph, S, params)
    return numerator / ub if ub else 0.0
