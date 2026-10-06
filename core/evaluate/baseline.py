"""최적화 결과 vs 단순 규칙 배치 — 같은 조건·같은 평가기로 나란히(시연 확장 A, 2026-10-06).

단순 규칙(core/optimize/greedy.solve_greedy): 사업 시작 순서대로, 등급 자리마다 기술 적합(S)이 가장 높은 사람부터 넣는다.
협업·익숙한 쌍·예산은 보지 않는다 -- 사람이 엑셀로 짤 때의 흔한 방식에 가깝다. 두 배치를 현행 평가기(plan_eval: MILP 전체
목적·제약)로 채점해 기술 적합·협업·익숙한 쌍·빈자리·제약 위반을 비교한다. 가상 데이터라도 "같은 조건에서 계산이 더 나은 배치를
찾고 제약을 지킨다"는 기술 주장은 성립한다(사업 효과는 NOT_CALIBRATED).
"""
from __future__ import annotations

from collections import Counter

from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.greedy import solve_greedy
from core.optimize.milp import MilpParams

BASELINE_RULE = "사업 시작 순서대로, 등급 자리마다 기술 적합이 가장 높은 사람부터 넣는다(협업·익숙한 쌍·예산은 보지 않음)"


def _summary(graph: MemoryGraph, S, C, params: MilpParams, entries) -> dict:
    ev = evaluate_plan(graph, S, C, params, entries)
    o = ev.objective
    pdx, jdx = graph.pid_index, graph.project_index
    fits = [float(S[pdx[e.person_id], jdx[e.project_id]]) for e in entries]
    return {
        "total": round(o.total, 4), "quality": round(o.skill + o.synergy + o.overfamiliarity, 4),
        "skill": round(o.skill, 4), "synergy": round(o.synergy, 4), "overfamiliarity": round(o.overfamiliarity, 4),
        "unfilled_seats": int(round(-o.unfilled / params.slack_penalty)) if params.slack_penalty else 0,
        "avg_seat_fit": round(sum(fits) / len(fits), 4) if fits else None,
        "assignments": len(entries), "people": len({e.person_id for e in entries}),
        "violations": dict(sorted(Counter(v.code for v in ev.violations).items())),
    }


def compare_with_baseline(graph: MemoryGraph, S, C, params: MilpParams, entries) -> dict:
    baseline = solve_greedy(graph, S, params.max_concurrent_projects, min_alloc=params.min_alloc)   # 같은 최소 투입률(리뷰)
    opt = _summary(graph, S, C, params, list(entries))
    base = _summary(graph, S, C, params, list(baseline.entries))
    diff = {k: round(opt[k] - base[k], 4) for k in ("total", "quality", "skill", "synergy", "overfamiliarity",
                                                     "unfilled_seats")}
    diff["violations"] = sum(opt["violations"].values()) - sum(base["violations"].values())
    return {"rule": BASELINE_RULE, "optimized": opt, "baseline": base, "difference": diff}
