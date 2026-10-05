import logging
import math
from dataclasses import replace
import numpy as np
import pulp
from pydantic import BaseModel
from core.graph.memory_graph import MemoryGraph
from core.optimize.audit_types import RawMilpSolution, SolverEvidence
from core.optimize.types import AssignEntry, PlanAssignment

logger = logging.getLogger(__name__)


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


def display_alloc(value: float, min_alloc: float) -> float:
    """반환·표시용 투입률. 해 값보다 크게 만들지 않는다(C3 [A-P2]).

    보통은 소수 둘째 자리 내림이다. min_alloc이 그보다 정밀해(예 0.205) 내림이 최소값 아래로
    떨어지면, 끌어올린 뒤 반올림하던 예전 규칙은 해 값 0.206을 0.21로 돌려줘 가용률을 넘겼다.
    그때는 해 값을 6자리 내림으로 그대로 둔다. 솔버가 최소값 바로 아래(허용오차 1e-6 안)에 둔
    값만 최소값으로 올린다 -- 호출부가 value >= min_alloc - 1e-6일 때만 부른다."""
    two = _floor2(value)
    if two >= min_alloc - 1e-12:
        return two
    fine = math.floor(value * 1e6 + 1e-9) / 1e6
    return max(fine, min_alloc)


class MilpParams(BaseModel):
    lam: float = 0.3
    mu: float = 0.2
    min_alloc: float = 0.2
    clique_threshold_months: int = 6
    pair_keep_ratio: float = 0.15
    slack_penalty: float = 100.0
    time_limit: int = 120
    gap: float = 0.05
    max_pairs: int = 5000


def pruned_pairs(C: np.ndarray, keep_ratio: float,
                 max_pairs: int | None = None) -> list[tuple[int, int]]:
    """|C| 상위 쌍만 남긴다. keep_ratio 기반 개수와 max_pairs 중 작은 값을 채택.

    keep_ratio 단독으로는 상한이 되지 못한다 — 쌍 수가 n(n-1)/2로 제곱 증가하므로
    그 15%도 여전히 제곱이다. 규모 스윕(최대 1000명)에서 y 변수가 1,500만 개까지
    늘어 CBC가 풀지 못하므로 절대 상한이 필요하다. 잘라낸 양은 호출부에서 로그로
    남겨 리포트에 명시할 것(조용한 절삭은 '전부 고려했다'는 오해를 부른다).

    Tie-breaking: when |C| values are equal (including the common case of many
    zero-synergy pairs), selection is deterministic by index order (i, j) to
    ensure reproducible results across runs and to match the reference sorted()
    implementation.
    """
    n = C.shape[0]
    iu = np.triu_indices(n, k=1)                  # 상삼각 = i<j 쌍 전체
    total = iu[0].size
    k = int(keep_ratio * total)
    if max_pairs is not None:
        k = min(k, max_pairs)
    k = max(0, min(k, total))
    if k == 0:
        return []
    mags = np.abs(C[iu])

    # Use argsort with stable sort to get deterministic, reproducible ordering that
    # matches the reference sorted() implementation exactly. When |C| values are tied,
    # stable sort preserves enumeration order (which argsort respects).
    # This is O(N log N) but correctly handles the common case of many zero-synergy
    # pairs (4,749 of 4,950 in the demo fixture) tied at |C|=0.
    order = np.argsort(-mags, kind="stable")
    top_indices = order[:k]

    # Return pairs in the order they appear in the sorted result (by descending |C|)
    return [(int(iu[0][t]), int(iu[1][t])) for t in top_indices]


def _overfamiliar_pairs(graph: MemoryGraph, threshold: int) -> set[tuple[int, int]]:
    """All (p, q), p < q, whose cowork history meets the over-familiarity
    threshold — scanned over ALL pairs, independent of |C| pruning.

    pair_keep_ratio prunes by |C_pq| (skill-synergy strength), which has no
    relation to cowork_months. If the Clique Penalty only ranged over the
    |C|-pruned set, a long-tenured pair with low synergy score would be
    pruned away and silently escape the penalty it's specifically meant to
    apply to — defeating the "과숙련 방지" (over-familiarity prevention)
    guarantee. So this is computed independently and unioned into the y
    variable set by the caller.
    """
    cw = graph.cowork_months
    rows, cols = cw.nonzero() if hasattr(cw, "nonzero") else np.nonzero(cw)
    result = set()
    for r, c in zip(rows, cols):
        r, c = int(r), int(c)
        if r < c and cw[r, c] >= threshold:
            result.add((r, c))
    return result


def solve_milp_assessment(graph: MemoryGraph, S: np.ndarray, C: np.ndarray,
                          params: MilpParams, extra_constraints=None):
    """Capture native evidence and assess it without weakening the final gate."""
    from core.optimize.numerics import (
        NumericalPolicy, assess_candidate, capture_model_contract,
        project_additive_constraints, UnsupportedModelMutation,
    )
    people, projects = graph.people, graph.projects
    nP, nJ = len(people), len(projects)
    prob = pulp.LpProblem("teamweaver", pulp.LpMaximize)
    z = pulp.LpVariable.dicts("z", (range(nP), range(nJ)), cat="Binary")
    a = pulp.LpVariable.dicts("a", (range(nP), range(nJ)), 0.0, 1.0)
    pruned = pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)         # top |C| pairs -> synergy reward term
    # len(pruned) == max_pairs is not by itself evidence that the cap did anything --
    # keep_ratio alone can happen to select exactly max_pairs pairs, which would log a
    # "cap applied" message even though max_pairs never bound anything (false positive,
    # final review Minor). Compare against the pre-cap (keep_ratio-only) count instead.
    total_pairs = int(0.5 * C.shape[0] * (C.shape[0] - 1))
    precap_count = min(int(params.pair_keep_ratio * total_pairs), total_pairs)
    if precap_count > params.max_pairs:
        logger.info(f"Synergy pair pruning: cap applied (limited to {params.max_pairs}/{total_pairs} total pairs)")
    overfam = _overfamiliar_pairs(graph, params.clique_threshold_months)  # ALL over-familiar pairs -> penalty term
    pairs = sorted(set(pruned) | overfam)                    # y/linearization must cover both
    y = {(p, q, j): pulp.LpVariable(f"y_{p}_{q}_{j}", 0.0, 1.0)
         for (p, q) in pairs for j in range(nJ)}
    slack = {(j, g): pulp.LpVariable(f"s_{j}_{g.value}", lowBound=0)
             for j, pj in enumerate(projects) for g in pj.grade_headcount}

    prob += (
        pulp.lpSum(S[i, j] * a[i][j] for i in range(nP) for j in range(nJ))
        + params.lam * pulp.lpSum(C[p, q] * y[(p, q, j)] for (p, q) in pruned for j in range(nJ))
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
    before_callback = capture_model_contract(prob) if extra_constraints else None
    if extra_constraints:
        extra_constraints(prob, z)
    after_callback = capture_model_contract(prob) if extra_constraints else None

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=params.time_limit, gapRel=params.gap))
    status = pulp.LpStatus[prob.status]
    if status not in ("Optimal", "Not Solved"):
        raise RuntimeError(f"MILP failed: {status}")
    raw_z = {(i, j): z[i][j].value() for i in range(nP) for j in range(nJ)}
    raw_a = {(i, j): a[i][j].value() for i in range(nP) for j in range(nJ)}
    raw_y = {key: var.value() for key, var in y.items()}
    raw_slack = {key: var.value() for key, var in slack.items()}
    raw_values = (*raw_z.values(), *raw_a.values(), *raw_y.values(), *raw_slack.values())
    has_incumbent = all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in raw_values
    )
    evidence = SolverEvidence(
        solver_name="CBC",
        native_status=status,
        # PuLP는 CBC가 시간 한도에서 멈춰도 해가 있으면 status를 "Optimal"로 바꿔 준다 -- 그 경우
        # sol_status만 IntegerFeasible이다. 시간 한도에 걸린 해는 부하에 따라 달라지므로 구분해 둔다.
        termination_reason=("time_limit_incumbent"
                            if prob.sol_status == pulp.LpSolutionIntegerFeasible else status),
        has_incumbent=has_incumbent,
        best_bound=None,
        options={"time_limit": params.time_limit, "gap": params.gap},
    )
    if not has_incumbent or pulp.value(prob.objective) is None:
        # Finite values alone are only an extractable candidate: CBC may expose
        # a fractional relaxation on timeout. Independent validation below must
        # establish integer feasibility before anything can leave this function.
        raise RuntimeError(
            f"MILP found no incumbent solution within time_limit={params.time_limit}s "
            f"(status={status}) — cannot extract a plan")

    entries = []
    for i in range(nP):
        for j in range(nJ):
            zval, aval = z[i][j].value(), a[i][j].value()
            if zval and zval > 0.5 and aval is not None and aval >= params.min_alloc - 1e-6:
                # floor (not nearest-round) so reported alloc never exceeds the
                # true solved value (display_alloc, C3).
                entries.append(AssignEntry(person_id=people[i].id, project_id=projects[j].id,
                                           alloc=display_alloc(aval, params.min_alloc)))
    unfilled = [f"{projects[j].id}:{g.value}:{int(round(v.value()))}명 미충원"
                for (j, g), v in slack.items() if v.value() and v.value() > 0.5]
    objective = float(pulp.value(prob.objective))
    plan = PlanAssignment(entries=entries, objective=objective,
                          unfilled=unfilled, violations=[], label="A")
    candidate = RawMilpSolution(
        plan=plan,
        status=status,
        objective=objective,
        z={key: float(value) for key, value in raw_z.items()},
        a={key: float(value) for key, value in raw_a.items()},
        y={key: float(value) for key, value in raw_y.items()},
        slack={key: float(value) for key, value in raw_slack.items()},
        reward_pairs=tuple(pruned),
        penalty_pairs=tuple(sorted(overfam)),
        variable_count=len(prob.variables()),
        constraint_count=len(prob.constraints),
        evidence=evidence,
    )
    extra_rows, mutation = (), None
    if before_callback is not None:
        fixed_values = {z[i][j].name:float(raw_z[(i,j)]) for i in range(nP) for j in range(nJ)}
        fixed_values.update({var.name:float(raw_y[key]) for key,var in y.items()})
        fixed_values.update({var.name:float(raw_slack[key]) for key,var in slack.items()})
        try:
            extra_rows = project_additive_constraints(before_callback,after_callback,
                allocation_variables={a[i][j].name:(i,j) for i in range(nP) for j in range(nJ)},
                fixed_values=fixed_values)
        except UnsupportedModelMutation as exc:
            mutation = str(exc)
    assessment = assess_candidate(graph,S,C,params,candidate,native_capture=candidate,
        policy=NumericalPolicy(enabled=mutation is None),extra_linear_constraints=extra_rows)
    if mutation is not None and assessment.accepted is None:
        assessment = replace(assessment,refinement=replace(assessment.refinement,reason=mutation))
    return assessment


def solve_milp_diagnostic(graph: MemoryGraph, S: np.ndarray, C: np.ndarray,
                          params: MilpParams, extra_constraints=None) -> RawMilpSolution:
    """Return only a strictly validated candidate; retained public contract."""
    from core.optimize.validation import validate_raw_solution
    assessment = solve_milp_assessment(graph,S,C,params,extra_constraints=extra_constraints)
    candidate = assessment.accepted
    validation = (validate_raw_solution(graph,S,C,params,candidate) if candidate is not None
                  else assessment.initial_validation)
    if candidate is None or not validation.valid:
        codes = ",".join(sorted({issue.code for issue in validation.issues}))
        raise RuntimeError(
            f"MILP returned an invalid incumbent candidate (status={assessment.validation_candidate.status}; "
            f"independent_validation_failed:{codes}; {assessment.refinement.reason})"
        )
    return candidate


def solve_milp(graph: MemoryGraph, S: np.ndarray, C: np.ndarray,
               params: MilpParams, extra_constraints=None) -> PlanAssignment:
    """Backward-compatible public solver returning the display plan only."""
    return solve_milp_diagnostic(
        graph, S, C, params, extra_constraints=extra_constraints
    ).plan
