"""운영 중 편성: 현재 배치를 고정하고 최대 K명만 옮길 수 있게 하는 부분 재최적화(2026-10-06).

사용자 장면: "90명이 이미 프로젝트를 뛰고 있는데 이 중에서 한두 명을 기존 프로젝트에서 빼고 잉여 인력 1-2명을 기존
프로젝트에 넣고 뺀 인력은 신규 프로젝트에 넣었을 때 전체 프로젝트 시너지로 봤을 때 얼마나 더 향상될 수 있는지".

규칙(현재 배치 = Dataset.current):
- 유지되는 배치는 사람·사업·투입률이 그대로다: a_ij = 현재 투입률 × z_ij (z_ij=0이면 그 사람이 그 사업에서 빠진 것).
- 빠지는 배치 수 Σ(1 − z_ij) ≤ K(변경 예산). 잠긴 배치(locked)는 빠질 수 없다.
- 진행 중 사업(현재 명단이 있는 사업)은 인원이 늘지 않는다: 빠진 자리만 다른 사람으로 메운다(정원식이 등급별 인원을 지킨다).
- 그 밖(신규 제안, 남는 인력)은 모델이 자유롭게 정한다. 목적식·제약은 서비스 MILP 그대로다 -- 독립 검증·보정도 그대로 탄다.
"""
from __future__ import annotations

from collections import Counter

import pulp

from core.domain.models import CurrentAssignment
from core.graph.memory_graph import MemoryGraph


def move_budget_constraints(graph: MemoryGraph, current: list[CurrentAssignment], k: int,
                            frozen_projects: set[str] | None = None, extra_seats: dict[str, int] | None = None,
                            cap_unstaffed: bool = False, movers_must_be_placed: bool = True,
                            movers_to: str | None = None, fill_open_seats: bool = True):
    """MILP 추가 제약 콜백.

    frozen_projects: 명단이 아예 바뀌지 않는 사업. extra_seats: 그 사업은 현재 인원 + n명까지(보강).
    cap_unstaffed: 현재 명단이 없는 사업(신규 제안 등)에도 새로 배치하지 않는다(보강 시뮬레이션은 대상 사업만 바꾼다).
    movers_must_be_placed: 기존 사업에서 빠진 사람은 다른 사업에 반드시 들어간다(사용자 장면: "뺀 인력은 신규 프로젝트에
    넣었을 때") -- 없으면 엔진이 "빼서 대기시키고 남는 인력으로 교체"하는 해를 낸다(2026-10-06 실측).
    movers_to: 빠진 사람은 이 사업으로만 간다(보강: 다른 사업에서 빼 온 사람은 대상 사업으로).
    fill_open_seats: 진행 사업 인원 상한을 max(현재 인원, 정원)으로 -- 덜 찬 명단의 빈 정원은 메울 수 있게(신규 제안 편성).
    보강은 False(그래프에 열어 둔 자리가 많아도 현재 인원 + n으로 정확히)."""
    if k < 0:
        raise ValueError("move budget must be >= 0")
    pdx, jdx = graph.pid_index, graph.project_index
    rows = [(pdx[c.person_id], jdx[c.project_id], float(c.alloc), bool(c.locked)) for c in current
            if c.person_id in pdx and c.project_id in jdx]
    team_size = Counter(j for _, j, _, _ in rows)
    if fill_open_seats:                       # 정원보다 덜 찬 명단(계획 시나리오 등)은 빈 정원까지는 메울 수 있다(리뷰 SHOULD)
        for j in list(team_size):
            team_size[j] = max(team_size[j], sum(graph.projects[j].grade_headcount.values()))
    current_projects: dict[int, set[int]] = {}
    for i, j, _, _ in rows:
        current_projects.setdefault(i, set()).add(j)
    for pid, n in (extra_seats or {}).items():
        if pid in jdx:
            team_size[jdx[pid]] += int(n)
    if cap_unstaffed:
        for j in range(len(graph.projects)):
            team_size.setdefault(j, 0)
    frozen = {jdx[p] for p in (frozen_projects or set()) if p in jdx}
    n_people = len(graph.people)

    def constrain(prob, z, alloc_vars):
        for i, j, alloc, locked in rows:
            for v in alloc_vars(i, j):
                prob += v <= alloc                      # 늘리지 않는다
                prob += v >= alloc * z[i][j]            # 유지하면(z=1) 그대로, 빠지면(z=0) 0(기존 a ≤ z)
            if locked or j in frozen:
                prob += z[i][j] == 1
            elif movers_to is not None and movers_to in jdx and jdx[movers_to] != j:
                prob += z[i][jdx[movers_to]] >= 1 - z[i][j]
            elif movers_must_be_placed:
                # 새로 들어가는 사업이어야 한다 -- 이미 겸직 중인 다른 사업으로는 채워지지 않는다(리뷰 MUST: 겸직자는 기존
                # 배치만으로 조건을 채워 "빼서 대기, 남는 인력으로 교체" 해가 다시 나왔다)
                prob += pulp.lpSum(z[i][jj] for jj in range(len(graph.projects))
                                   if jj not in current_projects[i]) >= 1 - z[i][j]
        if rows:
            prob += pulp.lpSum(1 - z[i][j] for i, j, _, _ in rows) <= k
        for j, size in team_size.items():               # 진행 중 사업은 인원이 늘지 않는다(빠진 자리만 메움)
            prob += pulp.lpSum(z[i][j] for i in range(n_people)) <= size
        for j in frozen:                                 # 고정 사업에는 새로 들어오지도 않는다
            members = {i for i, jj, _, _ in rows if jj == j}
            for i in range(n_people):
                if i not in members:
                    prob += z[i][j] == 0
    return constrain


def moves_from(current: list[CurrentAssignment], entries) -> dict:
    """현재 배치와 새 배치의 차이: 유지·빠짐(어디로 갔는지)·새로 들어감."""
    cur = {(c.person_id, c.project_id) for c in current}
    new = {(e.person_id, e.project_id) for e in entries}
    by_person_new: dict[str, list[str]] = {}
    for p, j in sorted(new - cur):
        by_person_new.setdefault(p, []).append(j)
    moved = [{"person_id": p, "from": j, "to": by_person_new.get(p, [])} for p, j in sorted(cur - new)]
    movers = {m["person_id"] for m in moved}
    joined = [{"person_id": p, "project_id": j, "from_bench": p not in {c.person_id for c in current}}
              for p, j in sorted(new - cur) if p not in movers]
    return {"kept": len(cur & new), "moved": moved, "joined": joined}
