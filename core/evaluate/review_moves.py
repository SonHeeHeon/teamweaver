"""인사팀 검토 자료의 "옮겨 온 인력" 사실(2026-10-19, 사용자 요청 -- 설계 .omc/plan/2026-10-19-review-doc.md 2.2).

확정 직전 배치(before)와 확정한 배치(after)를 비교해, 대상 사업에 새로 들어온 사람마다 어디서 왔는지와
그 이동으로 대상 사업이 얻는 것·원래 사업이 잃는 것·원래 사업의 빈자리를 누가 채웠는지를 같은 평가 기준(현행 평가기·
사업별 기여)으로 잰다. 결론("얻는 것이 더 크다")은 만들지 않는다 -- 그 판단은 부서장 의견 칸의 몫이다(사용자 결정).
숫자는 계산상 비교일 뿐 실제 성과를 보장하지 않는다(NOT_CALIBRATED).
"""
from __future__ import annotations

from core.evaluate.operating import project_contributions
from core.evaluate.plan_eval import evaluate_plan


def _alloc_of(entries) -> dict[tuple[str, str], float]:
    return {(e.person_id, e.project_id): float(e.alloc) for e in entries}


def target_changes(before, after) -> list[str]:
    """확정으로 사람이 새로 들어간 사업 ID(정렬)."""
    old = set(_alloc_of(before))
    return sorted({j for (p, j) in _alloc_of(after) if (p, j) not in old})


def _coverage(kg, project_id: str, entries) -> dict[str, dict]:
    """요구 기술별 충족 인원(근거 화면과 같은 판정 -- core.kg.views.project_evidence). graph를 넘기지 않아 대안 계산은 하지 않는다."""
    from core.kg.views import project_evidence
    ev = project_evidence(kg, project_id, list(entries))
    return {r["skill"]: {"met": r["met_by"], "headcount": r["headcount"]} for r in ev["requirements"]}


def _unfilled(evaluation, project_id: str) -> int:
    return sum(s.missing for s in evaluation.shortfalls if s.project_id == project_id)


def _project_state(kg, contrib, evaluation, project_id: str, entries) -> dict:
    return {"score": round(contrib.get(project_id, {}).get("total", 0.0), 4),
            "unfilled": _unfilled(evaluation, project_id),
            "coverage": _coverage(kg, project_id, entries)}


def _delta(before: dict, after: dict) -> dict:
    skills = []
    for sk in sorted(set(before["coverage"]) | set(after["coverage"])):
        b, a = before["coverage"].get(sk), after["coverage"].get(sk)
        mb, ma = (b or {}).get("met", 0), (a or {}).get("met", 0)
        if mb != ma:
            skills.append({"skill": sk, "met_before": mb, "met_after": ma, "headcount": (a or b)["headcount"]})
    return {"score_before": before["score"], "score_after": after["score"], "skills": skills,
            "unfilled_before": before["unfilled"], "unfilled_after": after["unfilled"]}


def mover_effects(graph, S, C, params, kg, before, after, target: str) -> dict:
    """대상 사업(target)에 새로 들어온 사람마다 출처와 득실. 형식은 구현 계획 Task 1 Interfaces.

    옮겨 온 사람(origin="moved") = 전에 대상 밖 다른 사업 배치가 있었고, 후에 그 배치가 빠졌거나 투입률이 줄었다.
    전에 배치가 없었으면 대기 인력(origin="bench"), 기간이 겹치는 다른 배치가 그대로 남았으면 일부 여유(origin="partly_free" --
    전일 투입이면 프로젝트 동시 수행), 남은 배치가 대상 사업 전에 끝나면 이어서 투입(origin="follow_on"). 출처 사업이 여럿이면 출처마다 적는다."""
    b_alloc, a_alloc = _alloc_of(before), _alloc_of(after)
    grade_of = {p.id: p.grade.value if hasattr(p.grade, "value") else str(p.grade) for p in graph.people}
    name_of = {j.id: j.name for j in graph.projects}
    ev_b, ev_a = evaluate_plan(graph, S, C, params, list(before)), evaluate_plan(graph, S, C, params, list(after))
    con_b, con_a = project_contributions(graph, S, C, params, before), project_contributions(graph, S, C, params, after)
    states: dict[tuple[str, str], dict] = {}

    def state(which: str, project_id: str) -> dict:
        key = (which, project_id)
        if key not in states:
            states[key] = (_project_state(kg, con_b, ev_b, project_id, before) if which == "b"
                           else _project_state(kg, con_a, ev_a, project_id, after))
        return states[key]

    joined = []
    for p in sorted({p for (p, j) in a_alloc if j == target and (p, target) not in b_alloc}):
        sources = []
        for (q, j), alloc_b in sorted(b_alloc.items()):
            if q != p or j == target:
                continue
            alloc_a = a_alloc.get((p, j), 0.0)
            if alloc_a >= alloc_b - 1e-9:
                continue                                   # 그대로 남은 동시 수행은 출처가 아니다
            # 그 사업에 이 확정으로 새로 들어온 사람 = 메움(같은 확정 안에서)
            backfill = [{"person_id": r, "grade": grade_of.get(r), "from_bench": not any(rq == r for (rq, _) in b_alloc)}
                        for (r, jj) in sorted(a_alloc) if jj == j and (r, j) not in b_alloc]
            sources.append({"project_id": j, "project_name": name_of.get(j, j),
                            "alloc_before": round(alloc_b, 4), "alloc_after": round(alloc_a, 4),
                            "left": (p, j) not in a_alloc,
                            **_delta(state("b", j), state("a", j)), "backfill": backfill})
        kept = [graph.projects[graph.project_index[j]] for (q, j) in a_alloc
                if q == p and j != target and (p, j) in b_alloc and j in graph.project_index]
        tgt = graph.projects[graph.project_index[target]] if target in graph.project_index else None
        # 남은 다른 배치가 **모두 대상 사업 시작 전에 끝나면** 그 사업을 마친 뒤 이어서 들어오는 것(follow_on, 2026-10-19 --
        # 전일 투입에서 "일부 여유"는 틀린 말이었다). 겹치거나 대상 사업 뒤에 시작하면 예전처럼 일부 여유(겹치면 전일 투입의 동시 수행).
        # 시간 순서를 본다 -- 겹치지 않기만 보면 "대상 사업을 먼저 하고 다음 배치로 간다"도 이어서 투입으로 찍혔다(폴백 리뷰 M4)
        follow = bool(kept) and tgt is not None and max(k.end_month for k in kept) < tgt.start_month
        origin = ("moved" if sources else "follow_on" if follow
                  else "partly_free" if any(q == p for (q, _) in b_alloc) else "bench")
        joined.append({"person_id": p, "grade": grade_of.get(p), "alloc": round(a_alloc[(p, target)], 4),
                       "origin": origin, "sources": sources})
    return {"target": target, "joined": joined, "target_gain": _delta(state("b", target), state("a", target))}
