"""C6 측정: 월별 투입률을 허용하면 얼마나 좋아지고 얼마나 느려지는가 (실험 전용, 서비스 미반영).

사용자 답변은 "프로젝트 기간 중 월별로 투입률이 바뀌어도 됨"(허용)이다. 서비스 모델은 지금 한
(사람, 프로젝트)에 투입률 하나(a_ij)를 기간 내내 쓴다. 월별판은 a_ijm(그 프로젝트가 진행되는 달마다)을
둔다. 같은 입력·같은 솔버(CBC)·같은 시간 한도로 두 모델을 풀어 목적값·풀이 시간·변수 수를 비교한다.

월별판의 정의(서비스 식과 맞춘 부분):
- 기술항: S_ij × (진행 달 평균 a_ijm). 고정판에서 a_ijm = a_ij이면 같은 값이 된다 -- 고정판의 해는
  월별판에서도 그대로 가능한 해라서, 월별판 최적값 ≥ 고정판 최적값이어야 한다(측정의 건전성 확인).
- 협업·반복 협업·미충원 항과 정원·동시 프로젝트 제약은 z(배치 여부)에만 걸려 그대로다.
- 가용률: 달마다 Σ_j a_ijm ≤ 가용률_im. 예산: 달마다 Σ_i 단가_i × a_ijm ≤ 월 예산_j.
- 최소 투입률: 배치되면 진행 달마다 a_ijm ≥ min_alloc.

실행: uv run --group benchmark python -m experiments.c6.monthly_alloc outputs/c6-monthly-alloc.json
결과는 합성 데이터 기준이며 사업 효과는 NOT_CALIBRATED다."""
import argparse
import json
import platform
import time
from pathlib import Path

import pulp

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, _overfamiliar_pairs, pruned_pairs, solve_milp_diagnostic
from core.scoring.engine import ScoringEngine


def solve_monthly(graph, S, C, params: MilpParams) -> dict:
    people, projects = graph.people, graph.projects
    nP, nJ = len(people), len(projects)
    prob = pulp.LpProblem("teamweaver_monthly", pulp.LpMaximize)
    z = {(i, j): pulp.LpVariable(f"z_{i}_{j}", cat="Binary") for i in range(nP) for j in range(nJ)}
    a = {(i, j, m): pulp.LpVariable(f"a_{i}_{j}_{m}", 0.0, 1.0)
         for i in range(nP) for j, pj in enumerate(projects) for m in pj.months}
    pruned = pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)
    overfam = _overfamiliar_pairs(graph, params.clique_threshold_months)
    pairs = sorted(set(pruned) | overfam)
    y = {(p, q, j): pulp.LpVariable(f"y_{p}_{q}_{j}", 0.0, 1.0) for (p, q) in pairs for j in range(nJ)}
    slack = {(j, g): pulp.LpVariable(f"s_{j}_{g.value}", lowBound=0)
             for j, pj in enumerate(projects) for g in pj.grade_headcount}
    prob += (
        pulp.lpSum(S[i, j] * (1.0 / len(pj.months)) * a[(i, j, m)]
                   for i in range(nP) for j, pj in enumerate(projects) for m in pj.months)
        + params.lam * pulp.lpSum(C[p, q] * y[(p, q, j)] for (p, q) in pruned for j in range(nJ))
        - params.mu * pulp.lpSum(y[(p, q, j)] for (p, q) in overfam for j in range(nJ))
        - params.slack_penalty * pulp.lpSum(slack.values()))
    for (i, j, m), v in a.items():
        prob += v <= z[(i, j)]
        prob += v >= params.min_alloc * z[(i, j)]
    for (p, q) in pairs:
        for j in range(nJ):
            prob += y[(p, q, j)] <= z[(p, j)]
            prob += y[(p, q, j)] <= z[(q, j)]
            prob += y[(p, q, j)] >= z[(p, j)] + z[(q, j)] - 1
    for i, person in enumerate(people):
        for m in range(len(person.availability)):
            active = [j for j, pj in enumerate(projects) if m in pj.months]
            if active:
                prob += pulp.lpSum(a[(i, j, m)] for j in active) <= person.availability[m]
            if len(active) > params.max_concurrent_projects:
                prob += pulp.lpSum(z[(i, j)] for j in active) <= params.max_concurrent_projects
    for j, pj in enumerate(projects):
        for g, need in pj.grade_headcount.items():
            prob += pulp.lpSum(z[(i, j)] for i, pe in enumerate(people) if pe.grade == g) + slack[(j, g)] == need
        for m in pj.months:
            prob += pulp.lpSum(people[i].monthly_rate * a[(i, j, m)] for i in range(nP)) <= pj.monthly_budget
    t = time.monotonic()
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=params.time_limit, gapRel=params.gap, threads=1))
    elapsed = time.monotonic() - t
    status = pulp.LpStatus[prob.status]
    obj = pulp.value(prob.objective)
    varying = 0
    for i in range(nP):
        for j, pj in enumerate(projects):
            if (z[(i, j)].value() or 0) > 0.5:
                vals = [a[(i, j, m)].value() for m in pj.months]
                if max(vals) - min(vals) > 1e-4:
                    varying += 1
    return {"status": status, "time_limited": prob.sol_status == pulp.LpSolutionIntegerFeasible,
            "objective": obj, "seconds": elapsed, "variables": len(prob.variables()),
            "constraints": len(prob.constraints), "assignments_with_varying_alloc": varying,
            "assignments": sum(1 for v in z.values() if (v.value() or 0) > 0.5)}


def solve_fixed(graph, S, C, params: MilpParams) -> dict:
    t = time.monotonic()
    raw = solve_milp_diagnostic(graph, S, C, params)
    elapsed = time.monotonic() - t
    return {"status": raw.status, "time_limited": raw.evidence.termination_reason == "time_limit_incumbent",
            "objective": raw.objective, "seconds": elapsed, "variables": raw.variable_count,
            "constraints": raw.constraint_count, "assignments": len(raw.plan.entries)}


SIZES = [(25, 5, 2), (50, 10, 42), (100, 20, 42)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output", type=Path)
    ap.add_argument("--time-limit", type=int, default=120)
    ap.add_argument("--gap", type=float, default=0.01)
    args = ap.parse_args()
    params = MilpParams(min_alloc=0.3, time_limit=args.time_limit, gap=args.gap)
    rows = []
    for n, j, seed in SIZES:
        ds = generate_dataset(n, j, seed=seed)
        g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
        eng = ScoringEngine(g)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        fixed = solve_fixed(g, S, C, params)
        monthly = solve_monthly(g, S, C, params)
        gain = (monthly["objective"] - fixed["objective"]) / abs(fixed["objective"]) if fixed["objective"] else None
        rows.append({"people": n, "projects": j, "seed": seed, "fixed": fixed, "monthly": monthly,
                     "objective_gain_ratio": gain,
                     "time_ratio": monthly["seconds"] / fixed["seconds"] if fixed["seconds"] else None})
        print(f"{n}/{j} seed{seed}: fixed {fixed['objective']:.3f} ({fixed['seconds']:.1f}s, "
              f"{fixed['variables']} vars) | monthly {monthly['objective']:.3f} ({monthly['seconds']:.1f}s, "
              f"{monthly['variables']} vars, 월별로 달라진 배치 {monthly['assignments_with_varying_alloc']}"
              f"/{monthly['assignments']}) | gain {gain:+.2%}", flush=True)
    out = {"experiment": "c6-monthly-allocation", "business_validity": "NOT_CALIBRATED",
           "params": params.model_dump(), "solver": "CBC (PuLP), threads=1",
           "platform": platform.platform(), "rows": rows,
           "note": "합성 데이터. 목적값은 현행 모델 점수이지 현실 성과가 아니다. gap 내 해라 작은 차이는 잡음일 수 있다."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
