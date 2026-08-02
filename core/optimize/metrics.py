from core.graph.memory_graph import MemoryGraph
from core.optimize.types import PlanAssignment

DEFAULT_WEIGHT = 3.0


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
