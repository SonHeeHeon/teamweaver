import math
import pulp
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, solve_milp
from core.optimize.types import PlanAssignment

_LABELS = "ABCDEFG"
_QUALITY_FLOOR = 0.95
_PLAN_A_GAP = 0.01


def meets_quality_floor(alt_objective: float, plan_a_objective: float,
                        floor: float = _QUALITY_FLOOR) -> bool:
    """Sign-symmetric "within `floor` of Plan A's quality" gate.

    `solve_milp`'s objective bakes in `-slack_penalty * sum(slack)` (100 per
    unfilled grade slot -- see core/optimize/milp.py), so it can be a large
    NEGATIVE number whenever any slot goes unfilled. The textbook
    `alt_objective >= floor * plan_a_objective` silently inverts once
    plan_a_objective < 0: e.g. objA=-100 -> floor*objA = -95, which sits
    ABOVE objA (closer to zero). The "floor" check then demands alt beat
    -95, i.e. beat Plan A by 5% of its magnitude, rather than merely stay
    within 5% of it. Since every alt is solved under a strict superset of
    Plan A's constraints (the diversity no-good cuts added below), alt's
    objective can essentially never exceed the unconstrained global optimum
    objA -- so with objA < 0 the naive formula rejects every single
    alternative, silently producing zero alternatives for the demo
    (directly threatening the spec's "Plan A 외 유효 대안 >= 3개" target).

    Fix: measure the allowed degradation as `(1-floor)` of Plan A's
    *magnitude* and subtract it from objA. This is sign-symmetric and
    reduces to the textbook `floor*objA` exactly when objA >= 0.
    """
    allowed_drop = (1.0 - floor) * abs(plan_a_objective)
    return alt_objective >= plan_a_objective - allowed_drop - 1e-6


def _diversity_cut(prev_sets: list[set[tuple[str, str]]],
                   pdx: dict[str, int], jdx: dict[str, int]):
    """Build an `extra_constraints(prob, z)` callback enforcing, for EVERY plan
    already accepted (Plan A and every alt accepted so far -- not just Plan A),
    a no-good cut approximating Jaccard(alt, prior) <= 0.8:
    `Σ_{(i,j)∈prior} z_ij <= floor(0.8·|prior|)`.
    """
    def cut(prob, z):
        for pairs_set in prev_sets:
            if not pairs_set:
                continue
            prob += pulp.lpSum(z[pdx[pid]][jdx[jid]] for pid, jid in pairs_set) \
                    <= math.floor(0.8 * len(pairs_set))
    return cut


def generate_plans(graph: MemoryGraph, S, C, params: MilpParams,
                   n_alternatives: int = 3) -> list[PlanAssignment]:
    # Plan A anchors both the headline "95% of Plan A" quality floor (meets_quality_floor)
    # and Task 14's E2E assertion. If Plan A is solved only to the caller's gap (default
    # 0.05), it is itself merely guaranteed within 5% of the TRUE optimum -- so the
    # end-to-end worst-case guarantee for an alternative vs. the true optimum compounds to
    # roughly floor*(1-gap) = 0.95*0.95 ~= 90.25%, not the 95% the "quality floor" name
    # implies. Plan A is solved exactly once (alternatives are solved n_alternatives times),
    # so tightening just its gap is cheap -- solve it far closer to true-optimal and leave
    # alternatives at the caller's (looser, faster) gap.
    plan_a_params = params.model_copy(update={"gap": min(params.gap, _PLAN_A_GAP)})
    plan_a = solve_milp(graph, S, C, plan_a_params)
    plans = [plan_a]
    jdx = {j.id: k for k, j in enumerate(graph.projects)}
    pdx = graph.pid_index
    for _ in range(n_alternatives):
        prev_sets = [p.pairs() for p in plans]
        cut = _diversity_cut(prev_sets, pdx, jdx)
        alt = solve_milp(graph, S, C, params, extra_constraints=cut)
        if not meets_quality_floor(alt.objective, plan_a.objective):
            break
        alt.label = _LABELS[len(plans)]
        plans.append(alt)
    return plans
