"""Rebuild the existing display contract without changing allocation policy."""
from dataclasses import replace
import math

from core.optimize.milp import mean_alloc, plan_entries
from core.optimize.types import PlanAssignment


def rebuild_plan(graph, params, raw, *, allocations=None, objective=None):
    """allocations: fixed면 {(i, j): a}, monthly(raw.a_month가 있음)면 {(i, j, m): a}."""
    objective = raw.objective if objective is None else objective
    if raw.a_month is not None:
        a_month = dict(raw.a_month if allocations is None else allocations)
        a = mean_alloc(graph, a_month)
    else:
        a_month, a = None, dict(raw.a if allocations is None else allocations)
    entries = plan_entries(graph, params, raw.z, a, a_month)
    unfilled = [f"{graph.projects[j].id}:{grade.value}:{int(round(value))}명 미충원"
                for (j, grade), value in raw.slack.items() if math.isfinite(value) and value > .5]
    plan = PlanAssignment(entries=entries, objective=objective, unfilled=unfilled,
                          violations=[], label=raw.plan.label)
    return replace(raw, a=a, a_month=a_month, objective=objective, plan=plan)
