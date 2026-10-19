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

import itertools
import math
from dataclasses import replace

from core.domain.models import CurrentAssignment, Grade
from core.evaluate.operating import exact, project_contributions
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.incremental import move_budget_constraints, moves_from
from core.optimize.milp import MilpParams, full_eligible, full_mode, solve_milp_assessment
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


def _busy_months(graph: MemoryGraph, entries: list[AssignEntry], person_id: str, project_id: str) -> int:
    """대상 사업 진행 달 중 그 사람이 다른 배치로 맡고 있는 사업 수의 최댓값."""
    months = graph.projects[graph.project_index[project_id]].months
    load = {m: 0 for m in months}
    for e in entries:
        if e.person_id != person_id or e.project_id == project_id:
            continue
        for m in graph.projects[graph.project_index[e.project_id]].months:
            if m in load:
                load[m] += 1
    return max(load.values(), default=0)


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
                    screen: int | None = SCREEN, locked: set[tuple[str, str]] = frozenset(),
                    offer_concurrency: bool = False) -> list[dict]:
    """대상 사업에 한 명씩 넣어 본 점수 변화 순위. include_pull이면 다른 사업 사람도(그 사업에서 빼 온다).

    budget_add가 None이면 후보의 월 비용만큼 대상 사업 예산을 늘린다(보강에는 예산이 따라붙는다) -- 필요한 예산은
    결과의 monthly_cost로 보인다. 숫자를 주면 그 금액만 늘리고, 투입률을 남는 예산에 맞춰 낮춘다(최소 투입률 밑이면 그대로
    두어 예산 초과가 위반으로 보이게). locked: (사람, 사업) 잠긴 배치 -- 빼 오지 않는다.

    offer_concurrency(전일 투입, 2026-10-19 사용자 결정): **허용이 필요한 동시 수행 후보만** 낸다 -- 지금 다른 사업에 있고 상한에 닿은
    사람을 "원래 사업 유지 + 이 사업 100%"로(같은 달 2곳까지로 보고 채점, 행에 needs_grant). 대기·빼 오기·이미 허용된 사람은 일반 호출이
    낸다(따로 부른다 -- 한 목록에 섞으면 상위 자리를 차지해 빼 오기·대기 후보가 사라졌다, 폴백 리뷰 P2). 고르면 확정이 허용(사유 "긴급 지원")을 함께 적는다.

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
    full = full_mode(params)
    screened = []
    for person in graph.people:
        pid = person.id
        if pid in team:
            continue
        elsewhere = sorted({e.project_id for e in entries if e.person_id == pid})
        i_ = graph.pid_index[pid]

        def option(subset: tuple[str, ...]):
            """subset 사업에서 빼 오면: (근사 점수, 빼는 배치, 투입률). 최소 투입률을 못 채우면 None."""
            rest = [e for e in entries if not (e.person_id == pid and e.project_id in subset)]
            if full:
                # 전일 투입(2026-10-19): 대상 사업 기간 가용률 1.0이고 그 기간 남은 배치 수가 사람별 상한보다 작으면 100%로 넣는다.
                # 예산을 정해 줘도 투입률을 낮추지 않는다(초과는 위반으로 보인다) -- Codex 리뷰 P2(동시 수행 허용자가 후보에서 빠짐)
                busy = _busy_months(graph, rest, pid, project_id)
                if not full_eligible(person, graph.projects[j]):
                    return None
                if offer_concurrency:              # 허용이 필요한 사람만: 원래 사업 유지(빼 오기 없음), 지금 상한에 닿음, 2곳까지
                    if subset or busy < person.max_concurrent or busy >= max(person.max_concurrent, 2):
                        return None
                elif busy >= person.max_concurrent:
                    return None
                alloc = 1.0
            else:
                alloc = math.floor(min(1.0, _free_alloc(graph, rest, pid, project_id)) * 10_000) / 10_000   # 내림: 반올림하면 남은 가용률을 넘을 수 있다
            if budget_add is not None and not full:                # 정해진 추가 예산 안으로 투입률을 맞춘다
                room = (graph.projects[j].monthly_budget + int(budget_add) - spent) / person.monthly_rate
                if room >= params.min_alloc - 1e-9:
                    alloc = min(alloc, math.floor(room * 10_000) / 10_000)
            if alloc < params.min_alloc - 1e-9:
                return None
            quick = float(S[i_, j]) * alloc + pair_value(i_, teams.get(project_id, []))
            for src in subset:                     # 빼 오면 원래 사업의 기여와 정원 자리를 잃는다
                src_e = next(e for e in entries if e.person_id == pid and e.project_id == src)
                quick -= float(S[i_, graph.project_index[src]]) * src_e.alloc + pair_value(i_, teams.get(src, []))
                if person.grade in listed[src]:
                    quick -= params.slack_penalty
            return quick, [(pid, p) for p in subset], alloc

        best = option(())
        if best is None:
            if not (elsewhere and include_pull) or offer_concurrency:
                continue                                  # 남는 가용률이 없다 -- 빼 오기 허용일 때만 후보
            # 동시 수행자는 필요한 사업에서만 빼 온다(잠긴 배치는 그대로) -- 예전엔 모든 사업에서 빼 빈자리·점수가 틀렸다(Codex 감사 2026-10-12)
            pullable = [p for p in elsewhere if (pid, p) not in locked]
            opts = [o for size in range(1, len(pullable) + 1) for sub in itertools.combinations(pullable, size)
                    if (o := option(sub)) is not None]
            if not opts:
                continue
            best = max(opts, key=lambda o: (o[0], -len(o[1])))
        quick, removes, alloc = best
        screened.append((quick, person, pid, elsewhere, removes, alloc))
    screened.sort(key=lambda t: (-t[0], t[2]))
    out = []
    for quick, person, pid, elsewhere, removes, alloc in (screened if screen is None else screened[:screen]):
        extra_budget = int(-(-person.monthly_rate * alloc // 1)) if budget_add is None else int(budget_add)
        g2 = graph_with_extra_seats(graph, project_id, {person.grade: 1}, extra_budget)
        rest = [e for e in entries if (e.person_id, e.project_id) not in set(removes)]
        needs_grant = bool(full and not removes and _busy_months(graph, rest, pid, project_id) >= person.max_concurrent)
        if needs_grant:                          # 허용을 함께 적는다고 보고 채점한다(그 사람만 같은 달 2곳까지)
            g2 = replace(g2, people=[p.model_copy(update={"max_concurrent": 2}) if p.id == pid else p for p in g2.people],
                         _within_cache={})
        sim = simulate(graph, S, C, params, entries, adds=[AssignEntry(person_id=pid, project_id=project_id, alloc=alloc)],
                       removes=removes, graph_after=g2)
        contrib = project_contributions(g2, S, C, params, sim["entries"]).get(project_id, {})
        teammates = [graph.pid_index[t] for t in team]
        i = graph.pid_index[pid]
        out.append({
            "person_id": pid, "grade": person.grade.value, "alloc": alloc,
            # 전일 투입에서 기간이 겹치는 다른 배치를 그대로 두고 들어오면 프로젝트 동시 수행(허용된 사람만). 겹치지 않으면(그 사업이
            # 먼저 끝남) 그 기간에는 비어 있는 사람이다 -- 동시 수행이 아니다(폴백 리뷰 P2)
            "source": "pull" if removes else (
                ("concurrent" if _busy_months(graph, entries, pid, project_id) > 0 else "bench") if full
                else ("partly_free" if elsewhere else "bench")),
            "needs_grant": needs_grant,
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
        # 고른 등급이 정원에 없으면 그 등급 정원을 "지금 팀의 그 등급 인원 + n"으로 둔다 -- 자리가 없으면 아무도 안 넣는 해(0명)도
        # "최선"이 됐다(Codex 리뷰 2026-10-12). 정원에 있으면 정원 + n(예전과 같음).
        have = sum(1 for e in entries if e.project_id == project_id
                   and graph.people[graph.pid_index[e.person_id]].grade == grade)
        hc = dict(target.grade_headcount)
        hc[grade] = hc.get(grade, have) + n
        projects = [p.model_copy(update={"grade_headcount": hc, "monthly_budget": p.monthly_budget + int(budget_add)})
                    if p.id == project_id else p for p in graph.projects]
        g2 = replace(graph, projects=projects, _within_cache={})
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
    # 달별 투입률이 있는 기준 배치(K 결과·달별 조정)는 달마다 그대로 지킨다 -- 그러려면 달별 변수(monthly)로 푼다(Codex 감사 2026-10-12)
    profiles = {(e.person_id, e.project_id): dict(e.monthly_alloc) for e in entries if e.monthly_alloc}
    solve_params = exact(params)
    if profiles and solve_params.allocation_mode == "fixed":    # 전일 투입은 투입률이 1.0뿐이라 바꾸지 않는다(Codex 리뷰 P2)
        solve_params = solve_params.model_copy(update={"allocation_mode": "monthly"})
    base_cb = move_budget_constraints(g2, current, pull_budget, extra_seats={project_id: n}, cap_unstaffed=True,
                                      movers_to=project_id, fill_open_seats=False, monthly_alloc=profiles)
    old_members = {e.person_id for e in entries if e.project_id == project_id}

    def cb(prob, z, alloc_vars):
        base_cb(prob, z, alloc_vars)
        if grade is not None:
            # 고른 추가 등급만 새로 들어온다 -- 정원에 없는 등급이면 자리를 늘려도 제약이 없어 다른 등급이 들어왔다(Codex 감사 2026-10-12)
            jt = graph.project_index[project_id]
            for i_, person in enumerate(graph.people):
                if person.grade != grade and person.id not in old_members:
                    prob += z[i_][jt] == 0
    a = solve_milp_assessment(g2, S, C, solve_params, extra_constraints=cb)
    ev = a.native_capture.evidence if a.native_capture is not None else None
    if a.accepted is None:
        return {"accepted": False, "termination": getattr(ev, "termination_reason", None)}
    new = list(a.accepted.plan.entries)
    before_eval = evaluate_plan(graph, S, C, params, entries)            # 보강 전 = 원래 정원·예산
    before = _parts(before_eval)
    grade_of = {p.id: p.grade for p in graph.people}

    def team_by_grade(plan: list[AssignEntry]) -> dict:
        out: dict = {}
        for pid in {e.person_id for e in plan if e.project_id == project_id}:
            out[grade_of[pid]] = out.get(grade_of[pid], 0) + 1
        return out

    # 늘린 자리는 등급별 **순증가**만큼: 빼 오기로 팀원이 다른 사업으로 가고 같은 등급 둘이 들어오면(순 +1) 새로 온 사람 수(2)로
    # 열면 자리 하나가 빈 것처럼 채점된다(2026-10-12 시연 리허설: 화면에 빈자리 감점 -100, 실제 변화는 +2.25)
    grown_before, grown_after = team_by_grade(entries), team_by_grade(new)
    joined = {g: c - grown_before.get(g, 0) for g, c in grown_after.items() if c > grown_before.get(g, 0)}
    g_eval = graph_with_extra_seats(graph, project_id, joined, budget_add)  # 실제로 늘어난 등급만
    after_eval = evaluate_plan(g_eval, S, C, params, new)
    after = _parts(after_eval)
    rate = {p.id: p.monthly_rate for p in graph.people}
    added_cost = sum(rate[e.person_id] * e.alloc for e in new if e.project_id == project_id) - \
        sum(rate[e.person_id] * e.alloc for e in entries if e.project_id == project_id)
    return {"accepted": True, "termination": getattr(ev, "termination_reason", None), "added_cost": round(added_cost, 2),
            "entries": new, "diff": moves_from(current, new),
            "before": before, "after": after, "delta": {k: after[k] - before[k] for k in before},
            "violations": [{"code": v.code, "location": v.location} for v in after_eval.violations],
            # simulate와 같은 모양: 보강으로 새로 생긴 위반만. 화면의 "새 위반"은 이 값을 써야 한다 -- violations는 보강 후 전체라
            # 기준 배치에 원래 있던 위반이 새 위반처럼 보였다(폴백 리뷰 2026-10-12)
            "new_violations": sorted({(v.code, v.location) for v in after_eval.violations}
                                     - {(v.code, v.location) for v in before_eval.violations})}
