import numpy as np
from core.graph.memory_graph import MemoryGraph
from core.optimize.types import AssignEntry, PlanAssignment


def solve_greedy(graph: MemoryGraph, S: np.ndarray) -> PlanAssignment:
    remaining = {p.id: list(p.availability) for p in graph.people}
    entries, unfilled, violations = [], [], []
    proj_index = {p.id: k for k, p in enumerate(graph.projects)}

    for j, proj in sorted(enumerate(graph.projects), key=lambda t: t[1].start_month):
        cost = 0.0
        for grade, need in sorted(proj.grade_headcount.items(), key=lambda t: t[0].value):
            cands = [(S[graph.pid_index[p.id], j], p) for p in graph.people
                     if p.grade == grade]
            for p_score, p in sorted(cands, key=lambda t: (-t[0], t[1].id)):
                if need == 0:
                    break
                free = min(remaining[p.id][m] for m in proj.months)
                if free < 0.2:
                    continue
                alloc = round(min(1.0, free), 2)
                for m in proj.months:
                    remaining[p.id][m] -= alloc
                entries.append(AssignEntry(person_id=p.id, project_id=proj.id, alloc=alloc))
                cost += p.monthly_rate * alloc
                need -= 1
            if need > 0:
                unfilled.append(f"{proj.id}:{grade.value}:{need}명 미충원")
        if cost > proj.monthly_budget:
            violations.append(f"{proj.id}: 월 예산 {proj.monthly_budget} 초과 (소요 {int(cost)})")

    objective = float(sum(S[graph.pid_index[e.person_id], proj_index[e.project_id]] * e.alloc
                          for e in entries))
    return PlanAssignment(entries=entries, objective=objective,
                          unfilled=unfilled, violations=violations, label="Greedy")
