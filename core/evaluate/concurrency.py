"""빈자리 → 프로젝트 동시 수행 허용 후보(2026-10-19 사용자 결정, 설계 .omc/plan/2026-10-19-concurrency.md 3.5).

사용자: "특정 인력은 현재 상황에 사람이 부족해서 혹은 이 인력이 저기에도 잠깐 봐줘야해서 등의 사유로 플젝 여러개를 한다."
전일 투입 계산이 빈자리를 남기면, 그 자리를 동시 수행으로 채울 수 있는 사람을 보여 준다 -- 다시 풀지 않는다(부서장이 사유와 함께
허용하면 다시 계산). 후보 = 같은 등급 · 그 사업 기간 가용률 1.0 · 그 기간 다른 사업에 있음(비어 있으면 동시 수행이 필요 없다) ·
아직 상한에 닿지 않은 사람(허용 안 됨이면 1곳) · 그 사업 남은 예산 ≥ 단가. 기술 적합도(S) 순.
"""
from __future__ import annotations

from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import full_eligible
from core.optimize.types import AssignEntry


def concurrency_candidates(graph: MemoryGraph, S, entries: list[AssignEntry], top: int = 3) -> list[dict]:
    people, projects = graph.people, graph.projects
    pdx, jdx = graph.pid_index, graph.project_index
    teams: dict[int, set[int]] = {}
    for e in entries:
        if e.person_id in pdx and e.project_id in jdx:
            teams.setdefault(jdx[e.project_id], set()).add(pdx[e.person_id])
    out = []
    for j, project in enumerate(projects):
        team = teams.get(j, set())
        months = set(project.months)
        spent = sum(people[i].monthly_rate for i in team)
        for grade, need in project.grade_headcount.items():
            missing = need - sum(1 for i in team if people[i].grade == grade)
            if missing <= 0:
                continue
            free, cands = 0, []
            for i, person in enumerate(people):
                if person.grade != grade or i in team or not full_eligible(person, project):
                    continue
                busy = sorted({projects[jj].id for jj, members in teams.items()
                               if jj != j and i in members and months & set(projects[jj].months)})
                if not busy:
                    free += 1                       # 비어 있는 사람 -- 동시 수행 없이도 넣을 수 있다
                    continue
                load = max(sum(1 for jj, members in teams.items() if i in members and m in projects[jj].months)
                           for m in months)
                if load >= 3 or person.monthly_rate > project.monthly_budget - spent:
                    continue
                cands.append({"person_id": person.id, "grade": grade.value, "skill_fit": round(float(S[i, j]), 4),
                              "monthly_cost": person.monthly_rate, "busy_with": busy,
                              "already_allowed": person.max_concurrent > 1,
                              "needs_more": load >= person.max_concurrent})
            cands.sort(key=lambda c: (-c["skill_fit"], c["person_id"]))
            out.append({"project_id": project.id, "grade": grade.value, "missing": missing,
                        "budget_left": project.monthly_budget - spent, "free_eligible": free,
                        "candidates": cands[:top]})
    return out
