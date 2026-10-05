"""Rebuild the existing display contract without changing allocation policy."""
from dataclasses import replace
import math
from core.optimize.milp import display_alloc
from core.optimize.types import AssignEntry, PlanAssignment


def rebuild_plan(graph, params, raw, *, allocations=None, objective=None):
    a = raw.a if allocations is None else allocations
    objective = raw.objective if objective is None else objective
    entries = [AssignEntry(person_id=graph.people[i].id, project_id=graph.projects[j].id,
                           alloc=display_alloc(value, params.min_alloc))
               for (i, j), value in a.items()
               if math.isfinite(value) and raw.z[(i, j)] > .5 and value >= params.min_alloc - 1e-6]
    unfilled = [f"{graph.projects[j].id}:{grade.value}:{int(round(value))}명 미충원"
                for (j, grade), value in raw.slack.items() if math.isfinite(value) and value > .5]
    plan = PlanAssignment(entries=entries, objective=objective, unfilled=unfilled,
                          violations=[], label=raw.plan.label)
    return replace(raw, a=dict(a), objective=objective, plan=plan)
