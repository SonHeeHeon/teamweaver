import logging
import math
from dataclasses import replace
from typing import Literal

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
    # 협업 보상 쌍 상한(2026-10-05, 규모 리허설 근거: rehearsal/results/n*/pairs.json). 쌍 변수는 (쌍 수 × 사업 수)라
    # 예전 5000(실제로는 0.15×전체 쌍)에서는 200명부터 HiGHS가 600초에도 미충원 57석, 300명은 해를 못 냈다.
    # 200이면 100/200/300명 모두 gap 5% 안에서 종료(10/22/98초), 예전 목적식으로 재채점해도 더 좋다(+42.6/+87.0/+134.0).
    # 규모별 최선은 아니다(200명은 400쌍 +88.6이 더 좋다) -- 300명이 400쌍에서 무너져(미충원 50) 공통값으로 절충했다.
    # 뜻: 화면의 "협업 시너지"는 이제 협업 점수 |C| 상위 200쌍만 보상한다(반복 협업 감점 쌍은 그대로 전부).
    max_pairs: int = 200
    # 한 사람이 같은 달에 맡는 프로젝트 수 상한(C6, 사용자 답변: 최대 3개·보통 1개).
    max_concurrent_projects: int = 3
    # 서비스 솔버(2026-10-05 사용자 결정: HiGHS로 고정). Phase 1(1스레드·240초)에서 HiGHS 79/112, CBC 22/112였고,
    # 조직형 100명 리허설에서도 같은 시간에 CBC보다 훨씬 좋은 해를 냈다(rehearsal/results). "cbc"는 비교·측정용으로만
    # 남긴다 -- API 요청(MilpParamsIn)과 관리자 설정에는 이 칸이 없어 바꿀 수 없다.
    solver: Literal["highs", "cbc"] = "highs"


BOUND_SNAP_EPS = 1e-9


def _snap_bounds(values: dict, lo: float, hi: float | None, integral: bool) -> tuple[dict, int]:
    """Move values that sit within BOUND_SNAP_EPS outside a variable bound (or of an integer, for binaries)
    onto it. HiGHS's postsolve leaves residues like z = 1.0000000000000007 that the strict independent
    validator (C0) rejects as out of domain. The snap is far below every solver tolerance (1e-6). The snapped
    candidate becomes the assessment's native_capture too (so C1 judges what the validator sees); the evidence
    keeps how many values moved and the largest move (snapped_to_bounds, max_snap), not which variables."""
    out, n = {}, 0
    for k, v in values.items():
        w = v
        if lo - BOUND_SNAP_EPS <= v < lo:
            w = lo
        elif hi is not None and hi < v <= hi + BOUND_SNAP_EPS:
            w = hi
        if integral and abs(w - round(w)) <= BOUND_SNAP_EPS:
            w = float(round(w))
        n += w != v
        out[k] = w
    return out, n


def _solver_cmd(params: "MilpParams"):
    """PuLP 솔버 객체. 둘 다 1스레드(Phase 1과 같은 조건, 동시 요청이 코어를 나눠 쓰게)."""
    if params.solver == "cbc":
        return pulp.PULP_CBC_CMD(msg=0, timeLimit=params.time_limit, gapRel=params.gap)
    return pulp.HiGHS(msg=False, timeLimit=params.time_limit, gapRel=params.gap, threads=1)


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
    for i in range(nP):                                     # 제약 1b: 같은 달 동시 프로젝트 수(C6)
        for m in range(len(people[i].availability)):
            active = [j for j, pj in enumerate(projects) if m in pj.months]
            # 상한+1곳에 최소 투입률로도 못 들어가는 달(가용률 < (K+1)·min_alloc)은 가용률 제약이 이미
            # 막으므로 행을 넣지 않는다(리뷰 S3: 묶이지 않는 행이 풀이만 느리게 했다). 검증기는 전부 본다.
            if (len(active) > params.max_concurrent_projects
                    and (params.max_concurrent_projects + 1) * params.min_alloc
                    <= people[i].availability[m] + 1e-6):
                prob += pulp.lpSum(z[i][j] for j in active) <= params.max_concurrent_projects
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

    prob.solve(_solver_cmd(params))
    status = pulp.LpStatus[prob.status]
    if status not in ("Optimal", "Not Solved"):
        raise RuntimeError(f"MILP failed: {status}")
    raw_z = {(i, j): z[i][j].value() for i in range(nP) for j in range(nJ)}
    raw_a = {(i, j): a[i][j].value() for i in range(nP) for j in range(nJ)}
    raw_y = {key: var.value() for key, var in y.items()}
    raw_slack = {key: var.value() for key, var in slack.items()}
    raw_values = (*raw_z.values(), *raw_a.values(), *raw_y.values(), *raw_slack.values())
    # HiGHS (via PuLP) fills every variable with 0.0 when it stops without any solution; only the solution
    # status tells that apart from a real all-zero plan, so it must count as "no incumbent" (review, 2026-10-05).
    # CBC leaves values empty (None) in that case, so the value check below already covers it.
    highs_without_solution = params.solver == "highs" and prob.sol_status == pulp.LpSolutionNoSolutionFound
    has_incumbent = not highs_without_solution and all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in raw_values
    )
    evidence = SolverEvidence(
        solver_name={"cbc": "CBC", "highs": "HiGHS"}[params.solver],
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
    snapped_z, nz = _snap_bounds(candidate.z, 0.0, 1.0, integral=True)
    snapped_a, na = _snap_bounds(candidate.a, 0.0, 1.0, integral=False)
    snapped_y, ny = _snap_bounds(candidate.y, 0.0, 1.0, integral=True)
    snapped_s, ns = _snap_bounds(candidate.slack, 0.0, None, integral=True)
    n_snapped = nz + na + ny + ns
    max_snap = max((abs(v - w) for raw, snapped in ((candidate.z, snapped_z), (candidate.a, snapped_a),
                                                     (candidate.y, snapped_y), (candidate.slack, snapped_s))
                    for (k, v), w in ((kv, snapped[kv[0]]) for kv in raw.items())), default=0.0)
    # The snapped values are what C1's eligibility check and allocation LP must see -- with the native HiGHS
    # residues every budget-residue case would be ruled INELIGIBLE (review SHOULD-1). The snap itself is
    # recorded so the evidence still says the native values were moved and by how much.
    validation_input = candidate if n_snapped == 0 else replace(
        candidate, z=snapped_z, a=snapped_a, y=snapped_y, slack=snapped_s,
        evidence=replace(evidence, options={**evidence.options, "snapped_to_bounds": n_snapped,
                                            "max_snap": max_snap}))
    extra_rows, mutation = (), None
    if before_callback is not None:
        fixed_values = {z[i][j].name:float(validation_input.z[(i,j)]) for i in range(nP) for j in range(nJ)}
        fixed_values.update({var.name:float(validation_input.y[key]) for key,var in y.items()})
        fixed_values.update({var.name:float(validation_input.slack[key]) for key,var in slack.items()})
        try:
            extra_rows = project_additive_constraints(before_callback,after_callback,
                allocation_variables={a[i][j].name:(i,j) for i in range(nP) for j in range(nJ)},
                fixed_values=fixed_values)
        except UnsupportedModelMutation as exc:
            mutation = str(exc)
    assessment = assess_candidate(graph,S,C,params,validation_input,native_capture=validation_input,
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
