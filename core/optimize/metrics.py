from core.graph.memory_graph import MemoryGraph
from core.optimize.types import PlanAssignment

DEFAULT_WEIGHT = 3.0


def matching_fulfillment(graph: MemoryGraph, plan: PlanAssignment,
                         weights: dict[str, int]) -> float:
    by_pid = {p.id: p for p in graph.people}
    alloc: dict[str, list] = {}
    for e in plan.entries:
        alloc.setdefault(e.project_id, []).append(e)
    num = den = 0.0
    for proj in graph.projects:
        for rq in proj.requirements:
            w = float(weights.get(rq.skill, DEFAULT_WEIGHT))
            got = sum(e.alloc for e in alloc.get(proj.id, [])
                      if by_pid[e.person_id].skills.get(rq.skill, 0) >= rq.min_level)
            num += w * min(1.0, got / rq.headcount)
            den += w
    return num / den if den else 0.0
