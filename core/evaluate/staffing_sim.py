"""진행 사업 보강 시뮬레이터(2026-10-06 사용자 장면 S2).

사용자: "이미 진행되고 있는 특정 프로젝트의 상황이 안 좋을 때 추가로 인력을 투입해야 하는 상황이 생기는데, 이럴 때 누구를
넣는 게 좋을지 인력을 넣었다 뺐다 하면서 시뮬레이션 해볼 수 있는 기능."

- rank_candidates: 대상 사업에 한 명씩 넣어 보고(정원은 그 사람 등급으로 +1, 추가 예산은 관리자 입력) 현행 평가기
  (plan_eval: MILP 전체 목적·제약)로 점수 변화·위반을 계산해 순위를 낸다. 다른 사업에서 빼 오는 후보(pull)는 그 사업의
  빠진 자리 감점까지 함께 계산된다. 이유: 대상 사업 요구 기술 적합(S), 기존 팀과의 협업 보상, 익숙한 쌍, 월 비용.
- simulate: 넣기·빼기 묶음을 평가기로 다시 채점(현재 배치 대비 변화).
- best_additions: n명 최선 조합을 부분 재최적화 엔진(core/optimize/incremental)으로 계산 -- 대상 사업만 인원이 늘 수 있고,
  다른 사업에서 빼 오는 인원은 변경 예산 K 이내.
"""
from __future__ import annotations

import math
from dataclasses import replace

from core.domain.models import CurrentAssignment, Grade
from core.evaluate.operating import exact, project_contributions
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.incremental import move_budget_constraints, moves_from
from core.optimize.milp import MilpParams, solve_milp_assessment
from core.optimize.types import AssignEntry


def graph_with_extra_seats(graph: MemoryGraph, project_id: str, add: dict[Grade, int], budget_add: int = 0) -> MemoryGraph:
    """대상 사업의 등급 정원을 늘리고 월 예산을 더한 그래프(사람·기술·협업은 그대로)."""
    projects = []
    for p in graph.projects:
        if p.id == project_id:
            hc = dict(p.grade_headcount)
            for g, k in add.items():
                if g in hc:                          # 정원에 없는 등급은 원래 제약 밖이라 늘릴 필요가 없다
                    hc[g] = hc[g] + k
            p = p.model_copy(update={"grade_headcount": hc, "monthly_budget": p.monthly_budget + int(budget_add)})
        projects.append(p)
    return replace(graph, projects=projects, _within_cache={})


def _free_alloc(graph: MemoryGraph, entries: list[AssignEntry], person_id: str, project_id: str) -> float:
    """대상 사업의 진행 달 동안 그 사람에게 남은 가용률(다른 배치를 뺀 값)의 최솟값."""
    pi = graph.pid_index[person_id]
    months = graph.projects[graph.project_index[project_id]].months
    used = {m: 0.0 for m in months}
    for e in entries:
        if e.person_id != person_id:
            continue
        other = graph.projects[graph.project_index[e.project_id]]
        for m in other.months:
            if m in used:
                used[m] += e.monthly_alloc.get(m, e.alloc) if e.monthly_alloc else e.alloc
    return min(graph.people[pi].availability[m] - used[m] for m in months)


def _parts(evaluation) -> dict:
    o = evaluation.objective
    return {"total": o.total, "skill": o.skill, "synergy": o.synergy, "overfamiliarity": o.overfamiliarity,
            "unfilled": o.unfilled}


def simulate(graph: MemoryGraph, S, C, params: MilpParams, entries: list[AssignEntry],
             adds: list[AssignEntry] = (), removes: list[tuple[str, str]] = (),
             graph_after: MemoryGraph | None = None) -> dict:
    """넣기·빼기를 적용한 배치를 평가기로 채점하고 현재 대비 변화를 낸다.

    보강이면 graph_after에 늘린 정원·예산을 넣는다 -- '보강 전'은 원래 그래프로 채점해야 늘린 자리가 보강 전 빈자리
    감점으로 끼어들지 않는다(2026-10-06 실측: 그렇게 하면 다른 사업에서 빼 온 사람의 빈자리가 상쇄돼 보였다)."""
    drop = set(removes)
    new = [e for e in entries if (e.person_id, e.project_id) not in drop] + list(adds)
    base = evaluate_plan(graph, S, C, params, entries)
    after = evaluate_plan(graph_after or graph, S, C, params, new)
    b, a = _parts(base), _parts(after)
    return {"before": b, "after": a, "delta": {k: a[k] - b[k] for k in a},
            "violations": [{"code": v.code, "location": v.location} for v in after.violations],
            "new_violations": sorted({(v.code, v.location) for v in after.violations}
                                     - {(v.code, v.location) for v in base.violations}),
            "entries": new}


SCREEN = 40          # 빠른 근사 점수로 거른 뒤 정확히 채점할 후보 수


def rank_candidates(graph: MemoryGraph, S, C, params: MilpParams, entries: list[AssignEntry], project_id: str, *,
                    include_pull: bool = False, budget_add: int | None = None, top: int | None = 10,
                    screen: int | None = SCREEN, locked: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """대상 사업에 한 명씩 넣어 본 점수 변화 순위. include_pull이면 다른 사업 사람도(그 사업에서 빼 온다).

    budget_add가 None이면 후보의 월 비용만큼 대상 사업 예산을 늘린다(보강에는 예산이 따라붙는다) -- 필요한 예산은
    결과의 monthly_cost로 보인다. 숫자를 주면 그 금액만 늘리고, 투입률을 남는 예산에 맞춰 낮춘다(최소 투입률 밑이면 그대로
    두어 예산 초과가 위반으로 보이게). locked: (사람, 사업) 잠긴 배치 -- 빼 오지 않는다.

    2단계(2026-10-06 실측: 후보마다 전체 평가면 300명 62초): 모든 후보를 빠른 근사 점수(기술 적합×투입률 + 팀 협업 보상
    − 익숙한 쌍 감점 − 빼 오면 생기는 정원 빈자리)로 거르고, 상위 screen명만 평가기로 정확히 채점한다. screen=None이면 전부."""
    j = graph.project_index[project_id]
    team = {e.person_id for e in entries if e.project_id == project_id}
    from core.optimize.milp import _overfamiliar_pairs, pruned_pairs
    reward = {tuple(sorted(p)) for p in pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)}
    penalty = {tuple(sorted(p)) for p in _overfamiliar_pairs(graph, params.clique_threshold_months,
                                                              params.clique_window_months)}
    teams: dict[str, list[int]] = {}
    for e in entries:
        teams.setdefault(e.project_id, []).append(graph.pid_index[e.person_id])
    listed = {pj.id: set(pj.grade_headcount) for pj in graph.projects}

    def pair_value(i: int, members: list[int]) -> float:
        v = 0.0
        for t in members:
            if t == i:
                continue
            key = (min(i, t), max(i, t))
            if key in reward:
                v += params.lam * float(C[key])
            if key in penalty:
                v -= params.mu
        return v

    rate = {p.id: p.monthly_rate for p in graph.people}
    spent = sum(rate[e.person_id] * e.alloc for e in entries if e.project_id == project_id)
    screened = []
    for person in graph.people:
        pid = person.id
        if pid in team:
            continue
        elsewhere = sorted({e.project_id for e in entries if e.person_id == pid})
        removes: list[tuple[str, str]] = []
        free = _free_alloc(graph, entries, pid, project_id)
        if free < params.min_alloc - 1e-9:
            if not (elsewhere and include_pull) or any((pid, p) in locked for p in elsewhere):
                continue                                  # 남는 가용률이 없다 -- 빼 오기 허용·잠금 아님일 때만 후보
            removes = [(pid, p) for p in elsewhere]       # 다른 사업에서 빼 온다(그 사업의 빈자리 감점까지 계산)
            free = _free_alloc(graph, [e for e in entries if e.person_id != pid], pid, project_id)
        alloc = math.floor(min(1.0, free) * 10_000) / 10_000      # 내림: 반올림하면 남은 가용률을 넘을 수 있다
        if budget_add is not None:                                 # 정해진 추가 예산 안으로 투입률을 맞춘다
            room = (graph.projects[j].monthly_budget + int(budget_add) - spent) / person.monthly_rate
            if room >= params.min_alloc - 1e-9:
                alloc = min(alloc, math.floor(room * 10_000) / 10_000)
        if alloc < params.min_alloc - 1e-9:
            continue
        i_ = graph.pid_index[pid]
        quick = float(S[i_, j]) * alloc + pair_value(i_, teams.get(project_id, []))
        for _, src in removes:                     # 빼 오면 원래 사업의 기여와 정원 자리를 잃는다
            src_e = next(e for e in entries if e.person_id == pid and e.project_id == src)
            quick -= float(S[i_, graph.project_index[src]]) * src_e.alloc + pair_value(i_, teams.get(src, []))
            if person.grade in listed[src]:
                quick -= params.slack_penalty
        screened.append((quick, person, pid, elsewhere, removes, alloc))
    screened.sort(key=lambda t: (-t[0], t[2]))
    out = []
    for quick, person, pid, elsewhere, removes, alloc in (screened if screen is None else screened[:screen]):
        extra_budget = int(-(-person.monthly_rate * alloc // 1)) if budget_add is None else int(budget_add)
        g2 = graph_with_extra_seats(graph, project_id, {person.grade: 1}, extra_budget)
        sim = simulate(graph, S, C, params, entries, adds=[AssignEntry(person_id=pid, project_id=project_id, alloc=alloc)],
                       removes=removes, graph_after=g2)
        contrib = project_contributions(g2, S, C, params, sim["entries"]).get(project_id, {})
        teammates = [graph.pid_index[t] for t in team]
        i = graph.pid_index[pid]
        out.append({
            "person_id": pid, "grade": person.grade.value, "alloc": alloc,
            "source": "pull" if removes else ("partly_free" if elsewhere else "bench"),
            "pulled_from": [p for _, p in removes],
            "delta_total": round(sim["delta"]["total"], 4),
            "delta": {k: round(v, 4) for k, v in sim["delta"].items()},
            "skill_fit": round(float(S[i, j]), 4),
            "team_synergy": round(sum(float(C[i, t]) for t in teammates), 4),
            "project_total_after": round(contrib.get("total", 0.0), 4),
            "monthly_cost": person.monthly_rate * alloc, "budget_added": extra_budget,
            "new_violations": sim["new_violations"],
        })
    out.sort(key=lambda r: (bool(r["new_violations"]), -r["delta_total"], r["person_id"]))
    return out if top is None else out[:top]


def best_additions(graph: MemoryGraph, S, C, params: MilpParams, entries: list[AssignEntry], project_id: str, n: int, *,
                   grade: Grade | None = None, pull_budget: int = 0, budget_add: int | None = None,
                   locked: set[tuple[str, str]] = frozenset()) -> dict:
    """대상 사업에 n명을 더 넣는 최선 조합(부분 재최적화). 다른 사업에서 빼 오는 인원은 pull_budget 이내, 빠진 자리는 메운다.

    budget_add=None이면 예산을 제약으로 쓰지 않고(가장 비싼 사람 n명분을 더해 둔다) 실제로 든 비용을 결과의 added_cost로
    보인다 -- 0을 기본으로 두면 예산이 막아 1명이 투입률 0.34로만 들어가 순위표와 모순돼 보였다(리뷰 SHOULD)."""
    if budget_add is None:
        budget_add = n * max(p.monthly_rate for p in graph.people)
    target = graph.projects[graph.project_index[project_id]]
    if grade is not None:
        g2 = graph_with_extra_seats(graph, project_id, {grade: n}, budget_add)
    else:
        # 등급을 정하지 않으면 **모든 등급**에 n자리씩 연다: 정원에 있던 등급은 정원 + n, 없던 등급은 지금 팀의 그 등급
        # 인원 + n. 일부 등급에만 열면 빈자리 감점(자리당 slack_penalty)을 줄이려고 그 등급만 고르는 왜곡이 생긴다
        # (2026-10-06 시험에서 발견: 더 잘 맞는 고급 두 명 대신 중급을 골랐다). 팀 인원 상한(현재 + n)이 추가 인원을 n으로
        # 묶으므로 어떤 등급을 넣든 줄어드는 빈자리 수가 같다. 채점은 실제로 들어간 등급만큼만 늘린 그래프로 한다.
        team_grades: dict = {}
        for e in entries:
            if e.project_id == project_id:
                gg = graph.people[graph.pid_index[e.person_id]].grade
                team_grades[gg] = team_grades.get(gg, 0) + 1
        hc = {g: target.grade_headcount.get(g, team_grades.get(g, 0)) + n for g in Grade}
        projects = [p.model_copy(update={"grade_headcount": hc, "monthly_budget": p.monthly_budget + int(budget_add)})
                    if p.id == project_id else p for p in graph.projects]
        g2 = replace(graph, projects=projects, _within_cache={})
    current = [CurrentAssignment(person_id=e.person_id, project_id=e.project_id, alloc=e.alloc,
                                 locked=(e.person_id, e.project_id) in locked) for e in entries]
    cb = move_budget_constraints(g2, current, pull_budget, extra_seats={project_id: n}, cap_unstaffed=True,
                                 movers_to=project_id, fill_open_seats=False)
    a = solve_milp_assessment(g2, S, C, exact(params), extra_constraints=cb)
    ev = a.native_capture.evidence if a.native_capture is not None else None
    if a.accepted is None:
        return {"accepted": False, "termination": getattr(ev, "termination_reason", None)}
    new = list(a.accepted.plan.entries)
    before = _parts(evaluate_plan(graph, S, C, params, entries))          # 보강 전 = 원래 정원·예산
    old_team = {e.person_id for e in entries if e.project_id == project_id}
    grade_of = {p.id: p.grade for p in graph.people}
    joined: dict = {}
    for e in new:
        if e.project_id == project_id and e.person_id not in old_team:
            joined[grade_of[e.person_id]] = joined.get(grade_of[e.person_id], 0) + 1
    g_eval = graph_with_extra_seats(graph, project_id, joined, budget_add)  # 실제로 늘어난 등급만
    after_eval = evaluate_plan(g_eval, S, C, params, new)
    after = _parts(after_eval)
    rate = {p.id: p.monthly_rate for p in graph.people}
    added_cost = sum(rate[e.person_id] * e.alloc for e in new if e.project_id == project_id) - \
        sum(rate[e.person_id] * e.alloc for e in entries if e.project_id == project_id)
    return {"accepted": True, "termination": getattr(ev, "termination_reason", None), "added_cost": round(added_cost, 2),
            "entries": new, "diff": moves_from(current, new),
            "before": before, "after": after, "delta": {k: after[k] - before[k] for k in before},
            "violations": [{"code": v.code, "location": v.location} for v in after_eval.violations]}
