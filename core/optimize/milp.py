import logging
import math
import numpy as np
import pulp
from pydantic import BaseModel
from core.graph.memory_graph import MemoryGraph
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


def solve_milp(graph: MemoryGraph, S: np.ndarray, C: np.ndarray,
               params: MilpParams, extra_constraints=None) -> PlanAssignment:
    people, projects = graph.people, graph.projects
    nP, nJ = len(people), len(projects)
    prob = pulp.LpProblem("teamweaver", pulp.LpMaximize)
    z = pulp.LpVariable.dicts("z", (range(nP), range(nJ)), cat="Binary")
    a = pulp.LpVariable.dicts("a", (range(nP), range(nJ)), 0.0, 1.0)
    pruned = pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)         # top |C| pairs -> synergy reward term
    if len(pruned) == params.max_pairs:
        logger.info(f"Synergy pair pruning: cap applied (limited to {params.max_pairs}/{int(0.5 * C.shape[0] * (C.shape[0] - 1))} total pairs)")
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
    if extra_constraints:
        extra_constraints(prob, z)

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=params.time_limit, gapRel=params.gap))
    status = pulp.LpStatus[prob.status]
    if status not in ("Optimal", "Not Solved"):
        raise RuntimeError(f"MILP failed: {status}")
    if pulp.value(prob.objective) is None:
        # "Not Solved" can mean either "time limit hit with a valid incumbent"
        # (fine — a time-limited but real solution) or "time limit hit with NO
        # incumbent at all" (every variable's .value() is None). The latter
        # must not silently fall through to an empty/partial PlanAssignment
        # that looks like a legitimate answer.
        raise RuntimeError(
            f"MILP found no incumbent solution within time_limit={params.time_limit}s "
            f"(status={status}) — cannot extract a plan")

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
