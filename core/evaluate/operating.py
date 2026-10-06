"""운영 중 편성 비교: 변경 예산 K별 최선 편성과 K=0 대비 개선량(2026-10-06 사용자 장면 S1).

    rows = compare_move_budgets(graph, S, C, params, dataset.current, ks=(0, 1, 2, 3))

각 K마다 core/optimize/incremental의 제약으로 서비스 MILP를 풀고, 결과를 현행 평가기(plan_eval: MILP 전체 목적·제약)로
다시 채점한다. 사업별 기여(기술·협업 보상·익숙한 쌍 감점)도 계산해 "누가 어디로 옮겨 어느 사업이 얼마나 좋아졌나"를 보인다.
"""
from __future__ import annotations

import time

from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.incremental import move_budget_constraints, moves_from
from core.optimize.milp import MilpParams, _overfamiliar_pairs, pruned_pairs, solve_milp_assessment


def project_contributions(graph: MemoryGraph, S, C, params: MilpParams, entries) -> dict[str, dict]:
    """사업별 목적 기여: 기술(S×투입률) + 협업 보상(λ·C, 보상 쌍) − 익숙한 쌍 감점(μ). 합은 평가기 총점에서 빈자리 감점을 뺀 값."""
    pdx, jdx = graph.pid_index, graph.project_index
    reward = {tuple(sorted(p)) for p in pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)}
    penalty = {tuple(sorted(p)) for p in _overfamiliar_pairs(graph, params.clique_threshold_months,
                                                              params.clique_window_months)}
    out: dict[str, dict] = {}
    teams: dict[str, list[int]] = {}
    for e in entries:
        i, j = pdx[e.person_id], jdx[e.project_id]
        months = graph.projects[j].months
        alloc = (sum(e.monthly_alloc.values()) / len(months)) if getattr(e, "monthly_alloc", None) else e.alloc
        row = out.setdefault(e.project_id, {"skill": 0.0, "synergy": 0.0, "overfamiliarity": 0.0, "people": 0})
        # β·S·z(자리당 적합, 서비스 기본 0)도 기술 기여에 넣어 합계가 평가기와 맞게
        row["skill"] += float(S[i, j]) * alloc + params.seat_fit_weight * float(S[i, j])
        row["people"] += 1
        teams.setdefault(e.project_id, []).append(i)
    for pid, members in teams.items():
        row = out[pid]
        for a_ in range(len(members)):
            for b_ in range(a_ + 1, len(members)):
                pair = tuple(sorted((members[a_], members[b_])))
                if pair in reward:
                    row["synergy"] += params.lam * float(C[pair])
                if pair in penalty:
                    row["overfamiliarity"] -= params.mu
    for row in out.values():
        row["total"] = row["skill"] + row["synergy"] + row["overfamiliarity"]
    return out


EXACT_GAP = 0.0


def exact(params: MilpParams) -> MilpParams:
    """운영 중 편성·보강은 대부분이 고정된 작은 문제라 최적까지 푼다. 상대 갭 5%로는 빈자리 감점(자리당 100)이 섞인 큰
    목적값의 5%가 0.5점 같은 개선을 가려 '더 자유롭게 줬는데 더 나쁜' 결과가 나왔다(2026-10-06 실측)."""
    return params.model_copy(update={"gap": EXACT_GAP})


def compare_move_budgets(graph: MemoryGraph, S, C, params: MilpParams, current, ks=(0, 1, 2, 3)) -> list[dict]:
    """K마다 최선 편성. 시간 한도에 걸려 K가 커졌는데 더 나쁜 해가 나오면(K−1의 해는 K에서도 가능하므로) 직전 해를 유지하고
    carried_from_k로 표시한다(그 행의 termination·proven_optimal도 유지한 해의 것). 한 K의 풀이가 실패해도 직전 해가 있으면
    그것을 유지하고(solve_failed), 없으면 실패 행만 남긴 채 나머지 K를 계속한다. 개선량은 K=0 행 기준(K=0이 실패하면 None)."""
    params = exact(params)
    rows, best_so_far = [], None
    carry_fields = ("objective", "quality", "unfilled_seats", "parts", "violations", "unfilled", "diff", "projects",
                    "entries", "termination", "proven_optimal", "best_bound")

    def carried(row: dict) -> dict:
        row.update({f: best_so_far[f] for f in carry_fields})
        row["carried_from_k"] = best_so_far.get("carried_from_k", best_so_far["k"])
        return row

    for k in sorted(ks):
        t = time.perf_counter()
        failure: dict | None = None
        try:
            a = solve_milp_assessment(graph, S, C, params, extra_constraints=move_budget_constraints(graph, current, k))
        except RuntimeError as exc:
            a, failure = None, {"error": str(exc)[:300]}
        elapsed = round(time.perf_counter() - t, 2)
        ev = a.native_capture.evidence if a is not None and a.native_capture is not None else None
        if failure is None and a.accepted is None:
            failure = {"termination": getattr(ev, "termination_reason", None)}
        if failure is not None:
            row = {"k": k, "elapsed_s": elapsed, **failure}
            if best_so_far is None:
                rows.append({**row, "accepted": False})
            else:                               # K−1의 해는 K에서도 가능한 해다
                rows.append(carried({**row, "accepted": True, "solve_failed": True, "own": failure}))
            continue
        row = _row(graph, S, C, params, current, list(a.accepted.plan.entries), list(a.accepted.plan.unfilled))
        row.update({"k": k, "elapsed_s": elapsed, "accepted": True,
                    "termination": getattr(ev, "termination_reason", None),
                    "proven_optimal": getattr(ev, "termination_reason", None) == "Optimal",
                    "best_bound": None if ev is None else ev.best_bound})
        if best_so_far is not None and row["objective"] < best_so_far["objective"] - 1e-9:
            row["own"] = {"objective": row["objective"], "termination": row["termination"]}
            carried(row)
        if best_so_far is None or row["objective"] >= best_so_far["objective"] - 1e-9:
            best_so_far = row
        rows.append(row)
    base = next((r for r in rows if r["k"] == 0 and r.get("accepted")), None)
    for row in rows:
        if not row.get("accepted"):
            continue
        if base is None:
            row["gain_vs_k0"] = row["quality_gain_vs_k0"] = row["quality_gain_pct_vs_k0"] = None
            row["unfilled_change_vs_k0"] = None
            row["project_change_vs_k0"] = {}
            continue
        row["gain_vs_k0"] = round(row["objective"] - base["objective"], 4)
        row["quality_gain_vs_k0"] = round(row["quality"] - base["quality"], 4)
        row["quality_gain_pct_vs_k0"] = (round(100 * row["quality_gain_vs_k0"] / abs(base["quality"]), 2)
                                         if abs(base["quality"]) > 1e-9 else None)
        row["unfilled_change_vs_k0"] = row["unfilled_seats"] - base["unfilled_seats"]
        row["project_change_vs_k0"] = {p: round(r["total"] - base["projects"].get(p, {}).get("total", 0.0), 4)
                                       for p, r in row["projects"].items()
                                       if abs(r["total"] - base["projects"].get(p, {}).get("total", 0.0)) > 1e-6}
    return rows


def _row(graph, S, C, params, current, entries, unfilled) -> dict:
    evaluation = evaluate_plan(graph, S, C, params, entries)
    o = evaluation.objective
    contrib = project_contributions(graph, S, C, params, entries)
    return {"objective": round(o.total, 4),
            # 빈자리 감점(자리당 slack_penalty)이 총점을 좌우하므로 배치 품질(기술+협업−익숙함)과 빈자리를 나눠 본다
            "quality": round(o.total - o.unfilled, 4),
            "unfilled_seats": int(round(-o.unfilled / params.slack_penalty)) if params.slack_penalty else 0,
            "parts": {"skill": round(o.skill, 4), "synergy": round(o.synergy, 4),
                      "overfamiliarity": round(o.overfamiliarity, 4), "unfilled": round(o.unfilled, 4)},
            "violations": [v.code for v in evaluation.violations], "unfilled": unfilled,
            "diff": moves_from(current, entries),
            "projects": {p: {k_: round(v, 4) for k_, v in r.items()} for p, r in sorted(contrib.items())},
            "entries": [e.model_dump() for e in entries]}
