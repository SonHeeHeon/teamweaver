"""동시 수행(한 사람이 같은 달 여러 사업) 줄이기 설계 측정(2026-10-19, claude-a -- claude-b 요청 (1)·(3), 설계 .omc/plan/2026-10-19-concurrency.md).

    uv run --group benchmark python -m rehearsal.concurrency_compare [--demo DIR] [--sizes 100 200] [--limits 30 120]
        → rehearsal/results/concurrency-compare.json

배경: 사용자 "100명 중 20명은 일단 너무 많아 … 3% 정도로", "여러 플젝 뛰는 사람들은 거의 없기도 하고 그 사람들은 두 플젝 다
100프로로 뛰는거라고 생각하고 해야돼". 지금 모델은 한 사람을 같은 달 최대 3개 사업에 30% 이상씩 나눠 넣고, 등급 정원은
사람 수(z)로 세서 30%짜리도 한 자리를 채운다 -- 예산이 빠듯하면 싼 30% 배치로 자리를 채우는 쪽이 목적값에 유리하다.

같은 데이터·같은 설정(서비스 기본값)에서 안 A 하나만 비교한다. 이 측정용 모델은 서비스 MILP(fixed 투입률)와 같은 식을 따로 쓴다 --
변형(동시 수행 상한·감점·전일 투입)과 초기해(warm start)를 한 경로에서 비교하려고. 같은 식인지는 `now` 행과 서비스 `solve_milp`
(`service` 행, 같은 시드 1)의 목적값으로 확인한다.

  now        지금 서비스: 투입률 0.30~1.00, 같은 달 최대 3곳
  cap        + 동시 수행 인원 상한(같은 달 2곳 이상인 사람 ≤ ⌈3%·인원⌉) -- 투입률은 그대로 나뉜다
  pen        + 동시 수행 감점(동시 수행한 사람마다 목적에서 ν=2, 상한 없음)
  full       전일 투입: 들어가면 그 사업 기간 100%, 동시 수행은 ⌈3%·인원⌉명까지 두 곳 모두 100%(예산도 두 곳에서 전액)
  full0      전일 투입, 동시 수행 없음
  *_warm     탐욕 해(사업 시작 순, 등급 자리마다 S가 가장 높은 가능한 사람, 예산 안, 동시 수행 없음)를 초기해로 -- 요청 (3)
사업 효과는 NOT_CALIBRATED: 어느 쪽이 실제로 좋은지는 이 측정으로 말할 수 없다. 계산 비용과 편성 모양의 차이만 본다.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import tempfile
import time
import zipfile
from pathlib import Path

import pulp

from rehearsal.run import RESULTS, _env_info, _now

ROOT = Path(__file__).resolve().parents[1]
OUT = RESULTS / "concurrency-compare.json"
SHARE = 0.03
PENALTY = 2.0
VARIANTS = ("service", "now", "now_warm", "cap", "cap_warm", "pen", "full", "full0", "full_warm")


def _bundle(demo: Path, n: int) -> Path:
    if (demo / f"org-n{n}").is_dir():
        return demo / f"org-n{n}"
    d = Path(tempfile.mkdtemp(prefix=f"concurrency-n{n}-"))
    zipfile.ZipFile(io.BytesIO((demo / f"org-n{n}.zip").read_bytes())).extractall(d)
    return d


def shift_budgets(root: Path, shift: float) -> Path:
    """결정 2(가) 흉내: 생성기 예산 범위 0.9~1.15 → (0.9+shift)~(1.15+shift). 생성기는 기준 단가 × 정원 × uniform(a, a+0.25)라
    같은 난수면 예산이 shift × 기준 비용만큼 늘어난다(사업·사람·다른 난수는 그대로). 사본을 고쳐 돌려준다."""
    import csv
    import shutil
    from core.ingest.synthetic import BASE_RATE
    out = Path(tempfile.mkdtemp(prefix="concurrency-budget-")) / root.name
    shutil.copytree(root, out)
    base: dict[str, float] = {}
    with (out / "project_grade_requirements.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            base[r["project_id"]] = base.get(r["project_id"], 0.0) + BASE_RATE[r["career_grade"]] * int(r["headcount"])
    with (out / "projects.csv").open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    for r in rows:
        r["monthly_budget"] = str(int(r["monthly_budget"]) + int(shift * base.get(r["project_id"], 0.0)))
    with (out / "projects.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    if "files" in manifest:                         # 원천 파일 해시를 고친 사본에 맞춘다(측정용 사본)
        manifest["files"]["projects.csv"] = hashlib.sha256((out / "projects.csv").read_bytes()).hexdigest()
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return out


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def cap_for(n: int) -> int:
    return max(1, math.ceil(SHARE * n - 1e-9))


class WarmHiGHS(pulp.HiGHS):
    """pulp.HiGHS + 초기해(이름 → 값). 1스레드(서비스와 같은 조건)."""

    def __init__(self, warm: dict[str, float] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.warm = warm

    def callSolver(self, lp):
        if self.warm is not None:
            import highspy
            sol = highspy.HighsSolution()
            values = [0.0] * len(lp.variables())
            for var in lp.variables():
                values[var.index] = float(self.warm.get(var.name, 0.0))
            sol.col_value = values
            sol.value_valid = True
            lp.solverModel.setSolution(sol)
        super().callSolver(lp)


def build(graph, S, C, params, variant: str):
    """측정용 모델. 반환: (prob, z, a(i,j)→표현식, w, names)."""
    from core.optimize.milp import _overfamiliar_pairs, pruned_pairs
    people, projects = graph.people, graph.projects
    nP, nJ = len(people), len(projects)
    full = variant.startswith("full")
    cap = 0 if variant == "full0" else cap_for(nP)
    prob = pulp.LpProblem("concurrency", pulp.LpMaximize)
    if full:
        # 전일 투입은 사업 기간 내내 가용률 100%일 때만(가용률 0인 달 = 육아휴직 등 → 그 사업 불가)
        ok = {(i, j) for i, pe in enumerate(people) for j, pj in enumerate(projects)
              if all(pe.availability[m] >= 1.0 - 1e-9 for m in pj.months)}
    else:
        ok = {(i, j) for i in range(nP) for j in range(nJ)}
    z = {(i, j): pulp.LpVariable(f"z_{i}_{j}", cat="Binary") for (i, j) in sorted(ok)}
    a = ({k: v for k, v in z.items()} if full
         else {k: pulp.LpVariable(f"a_{k[0]}_{k[1]}", 0.0, 1.0) for k in z})
    uses_w = variant in ("cap", "pen", "full")
    w = {i: pulp.LpVariable(f"w_{i}", cat="Binary") for i in range(nP)} if uses_w else {}
    pruned = pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)
    overfam = _overfamiliar_pairs(graph, params.clique_threshold_months, params.clique_window_months)
    pairs = sorted(set(pruned) | overfam)
    y = {(p, q, j): pulp.LpVariable(f"y_{p}_{q}_{j}", 0.0, 1.0)
         for (p, q) in pairs for j in range(nJ) if (p, j) in z and (q, j) in z}
    slack = {(j, g): pulp.LpVariable(f"s_{j}_{g.value}", lowBound=0)
             for j, pj in enumerate(projects) for g in pj.grade_headcount}
    obj = (pulp.lpSum(S[i, j] * a[(i, j)] for (i, j) in a)
           + params.lam * pulp.lpSum(C[p, q] * y[(p, q, j)] for (p, q) in pruned for j in range(nJ) if (p, q, j) in y)
           - params.mu * pulp.lpSum(y[(p, q, j)] for (p, q) in overfam for j in range(nJ) if (p, q, j) in y)
           - params.slack_penalty * pulp.lpSum(slack.values()))
    if variant == "pen":
        obj -= PENALTY * pulp.lpSum(w.values())
    prob += obj
    if not full:
        for k, v in a.items():
            prob += v <= z[k]
            prob += v >= params.min_alloc * z[k]
    for (p, q, j), v in y.items():
        prob += v <= z[(p, j)]
        prob += v <= z[(q, j)]
        prob += v >= z[(p, j)] + z[(q, j)] - 1
    for i, pe in enumerate(people):
        for m in range(len(pe.availability)):
            active = [j for j, pj in enumerate(projects) if m in pj.months and (i, j) in z]
            if not active:
                continue
            if not full:
                prob += pulp.lpSum(a[(i, j)] for j in active) <= pe.availability[m]
                if len(active) > params.max_concurrent_projects:
                    prob += pulp.lpSum(z[(i, j)] for j in active) <= params.max_concurrent_projects
            if len(active) < 2:
                continue
            if full:
                prob += pulp.lpSum(z[(i, j)] for j in active) <= 1 + (w[i] if w else 0)
            elif w:
                k = min(params.max_concurrent_projects, len(active))
                prob += pulp.lpSum(z[(i, j)] for j in active) <= 1 + (k - 1) * w[i]
    if variant in ("cap", "full"):
        prob += pulp.lpSum(w.values()) <= cap
    for j, pj in enumerate(projects):
        for g, need in pj.grade_headcount.items():
            prob += pulp.lpSum(z[(i, j)] for i, pe in enumerate(people) if pe.grade == g and (i, j) in z) \
                    + slack[(j, g)] == need
        prob += pulp.lpSum(people[i].monthly_rate * a[(i, j)] for i in range(nP) if (i, j) in a) <= pj.monthly_budget
    return prob, z, a, w, y, slack


def greedy_full(graph, S, z, y, slack) -> dict[str, float]:
    """전일 투입 탐욕 해(동시 수행 없음, 예산 안): 사업 시작 순, 등급 자리마다 S가 가장 높은 가능한 사람."""
    people, projects = graph.people, graph.projects
    busy = {i: set() for i in range(len(people))}
    chosen: set[tuple[int, int]] = set()
    for j, pj in sorted(enumerate(projects), key=lambda t: (t[1].start_month, t[0])):
        left = pj.monthly_budget
        for g, need in sorted(pj.grade_headcount.items(), key=lambda t: t[0].value):
            cands = sorted((i for i, pe in enumerate(people) if pe.grade == g and (i, j) in z
                            and not (busy[i] & set(pj.months))), key=lambda i: (-S[i, j], people[i].id))
            got = 0
            for i in cands:
                if got >= need:
                    break
                if people[i].monthly_rate > left + 1e-9:
                    continue
                chosen.add((i, j))
                busy[i] |= set(pj.months)
                left -= people[i].monthly_rate
                got += 1
    warm = {z[k].name: 1.0 for k in chosen}
    for (p, q, j), v in y.items():
        if (p, j) in chosen and (q, j) in chosen:
            warm[v.name] = 1.0
    for (j, g), v in slack.items():
        filled = sum(1 for (i, jj) in chosen if jj == j and people[i].grade == g)
        warm[v.name] = float(projects[j].grade_headcount[g] - filled)
    return warm


def greedy_frac(graph, S, params, z, a, w, y, slack) -> dict[str, float]:
    """지금 모델(투입률 나눔)의 탐욕 해: 사업 시작 순, 등급 자리마다 S가 가장 높은 사람에게 남은 가용률·남은 예산 안에서
    가능한 만큼(최소 투입률 이상). 동시 수행은 만들지 않는다(동시 수행 상한 변형에서도 그대로 가능한 해)."""
    people, projects = graph.people, graph.projects
    free = {i: list(pe.availability) for i, pe in enumerate(people)}
    busy = {i: set() for i in range(len(people))}
    chosen: dict[tuple[int, int], float] = {}
    for j, pj in sorted(enumerate(projects), key=lambda t: (t[1].start_month, t[0])):
        left = pj.monthly_budget
        for g, need in sorted(pj.grade_headcount.items(), key=lambda t: t[0].value):
            cands = sorted((i for i, pe in enumerate(people) if pe.grade == g and not (busy[i] & set(pj.months))),
                           key=lambda i: (-S[i, j], people[i].id))
            got = 0
            for i in cands:
                if got >= need:
                    break
                room = min(free[i][m] for m in pj.months)
                alloc = math.floor(min(1.0, room, left / people[i].monthly_rate) * 100) / 100
                if alloc < params.min_alloc:
                    continue
                chosen[(i, j)] = alloc
                busy[i] |= set(pj.months)
                for m in pj.months:
                    free[i][m] -= alloc
                left -= people[i].monthly_rate * alloc
                got += 1
    warm = {}
    for k, v in chosen.items():
        warm[z[k].name] = 1.0
        warm[a[k].name] = v
    for (p, q, j), v in y.items():
        if (p, j) in chosen and (q, j) in chosen:
            warm[v.name] = 1.0
    for (j, g), v in slack.items():
        filled = sum(1 for (i, jj) in chosen if jj == j and people[i].grade == g)
        warm[v.name] = float(projects[j].grade_headcount[g] - filled)
    return warm


def budget_unfillable(graph) -> int:
    """전일 투입이면 예산 때문에 끝까지 풀어도 비는 자리 수의 하한: 사업마다 등급별 가장 싼 사람으로 채우고, 예산을 넘으면
    비싼 자리부터 비운다(사업끼리 사람을 다투는 것은 보지 않는다 -- 하한)."""
    rates: dict = {}
    for p in graph.people:
        rates.setdefault(p.grade, []).append(p.monthly_rate)
    total = 0
    for pj in graph.projects:
        seats = sorted(r for g, k in pj.grade_headcount.items() for r in sorted(rates.get(g, []))[:k])
        while seats and sum(seats) > pj.monthly_budget + 1e-9:
            seats.pop()
            total += 1
        total += sum(pj.grade_headcount.values()) - sum(min(k, len(rates.get(g, []))) for g, k in pj.grade_headcount.items())
    return total


def shape(graph, S, entries) -> dict:
    people = {p.id: p for p in graph.people}
    months_of = {p.id: set(p.months) for p in graph.projects}
    per: dict[str, list] = {}
    for e in entries:
        per.setdefault(e.person_id, []).append(e)
    concurrent = 0
    for pid, es in per.items():
        if any(len([e for e in es if m in months_of[e.project_id]]) >= 2 for m in range(24)):
            concurrent += 1
    seats = len(entries)
    fit = [float(S[graph.pid_index[e.person_id], graph.project_index[e.project_id]]) for e in entries]
    cost = sum(people[e.person_id].monthly_rate * e.alloc for e in entries)
    budget = sum(p.monthly_budget for p in graph.projects)
    return {"people_used": len(per), "seats": seats, "concurrent_people": concurrent,
            "concurrent_share": round(concurrent / len(graph.people), 3),
            "partial_alloc_entries": sum(1 for e in entries if e.alloc < 0.995),
            "mean_seat_fit": round(sum(fit) / seats, 4) if seats else None,
            "budget_use": round(cost / budget, 3) if budget else None}


def solve(graph, S, C, params, variant: str, limit: int) -> dict:
    from core.optimize.types import AssignEntry
    t0 = time.perf_counter()
    if variant == "service":
        from core.optimize.milp import solve_milp_assessment
        a = solve_milp_assessment(graph, S, C, params.model_copy(update={"time_limit": limit, "solver_seeds": 1}))
        elapsed = time.perf_counter() - t0
        if a.accepted is None:
            return {"variant": variant, "time_limit": limit, "accepted": False, "elapsed_s": round(elapsed, 1)}
        ev = a.native_capture.evidence
        plan = a.accepted.plan
        unfilled = sum(int(u.split(":")[2].rstrip("명 미충원")) for u in plan.unfilled)
        return {"variant": variant, "time_limit": limit, "accepted": True, "elapsed_s": round(elapsed, 1),
                "termination": ev.termination_reason, "objective": round(plan.objective, 3),
                "best_bound": None if ev.best_bound is None else round(ev.best_bound, 3),
                "unfilled_seats": unfilled, **shape(graph, S, plan.entries)}
    base = variant.removesuffix("_warm")
    prob, z, a, w, y, slack = build(graph, S, C, params, base)
    warm = (None if variant == base
            else greedy_full(graph, S, z, y, slack) if base.startswith("full")
            else greedy_frac(graph, S, params, z, a, w, y, slack))
    solver = WarmHiGHS(warm=warm, msg=False, timeLimit=limit, gapRel=params.gap, threads=1)
    t_build = time.perf_counter() - t0
    prob.solve(solver)
    elapsed = time.perf_counter() - t0
    status = pulp.LpStatus[prob.status]
    has = prob.sol_status in (pulp.LpSolutionOptimal, pulp.LpSolutionIntegerFeasible)
    row = {"variant": variant, "time_limit": limit, "elapsed_s": round(elapsed, 1), "build_s": round(t_build, 1),
           "status": status, "proven": prob.sol_status == pulp.LpSolutionOptimal, "accepted": has,
           "variables": len(prob.variables()), "constraints": len(prob.constraints)}
    if warm is not None:
        start_unfilled = sum(warm.get(v.name, 0.0) for v in slack.values())
        row["warm_start_unfilled"] = int(round(start_unfilled))
    if not has:
        return row
    try:
        bound = -float(prob.solverModel.getInfo().mip_dual_bound)
    except Exception:                                   # noqa: BLE001 -- 측정 보조값
        bound = None
    entries = [AssignEntry(person_id=graph.people[i].id, project_id=graph.projects[j].id,
                           alloc=round(float(a[(i, j)].value()), 2) if not variant.startswith("full") else 1.0)
               for (i, j), v in z.items() if (v.value() or 0) > 0.5]
    obj = float(pulp.value(prob.objective))
    row.update({"objective": round(obj, 3), "best_bound": None if bound is None else round(bound, 3),
                "gap": None if bound is None or not obj else round(abs(bound - obj) / abs(obj), 4),
                "unfilled_seats": int(round(sum((v.value() or 0.0) for v in slack.values()))),
                "concurrent_flags": int(round(sum((v.value() or 0.0) for v in w.values()))) if w else None,
                "skill_term": round(sum(float(S[i, j]) * float(a[(i, j)].value() or 0.0) for (i, j) in a), 3),
                **shape(graph, S, entries)})
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", type=Path, default=ROOT / "demo")
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 200, 300])
    ap.add_argument("--limits", type=int, nargs="+", default=[30, 120])
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS))
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--budget-shift", type=float, default=0.0, help="결정 2(가) 흉내: 예산 범위를 이만큼 올린다(0.1 → 1.0~1.25)")
    args = ap.parse_args()
    from api.settings import PlacementSettings
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.scoring.engine import ScoringEngine
    runs, data = [], {}
    if args.out.exists():
        prev = json.loads(args.out.read_text("utf-8"))
        runs, data = prev.get("runs", []), prev.get("data", {})
    for n in args.sizes:
        root = _bundle(args.demo, n)
        if args.budget_shift:
            root = shift_budgets(root, args.budget_shift)
        b, rep = load_bundle(root)
        ds, parsed = to_dataset(b, rep)
        graph = MemoryGraph.build(ds, parsed)
        eng = ScoringEngine(graph)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        params = PlacementSettings(allocation_mode="fixed").to_milp_params()     # "지금" = 예전 투입률 방식(비교 기준)
        key = f"{n}+budget{args.budget_shift:g}" if args.budget_shift else str(n)
        data[key] = {"source": str(args.demo / f"org-n{n}"), "digest": _digest(root), "budget_shift": args.budget_shift,
                        "people": len(graph.people), "projects": len(graph.projects),
                        "seats": sum(sum(p.grade_headcount.values()) for p in graph.projects),
                        "leave_people": sum(1 for p in graph.people if min(p.availability) < 1.0),
                        "full_time_budget_unfillable": budget_unfillable(graph),
                        "cap": cap_for(len(graph.people)), "params": params.model_dump(mode="json")}
        for limit in args.limits:
            for variant in args.variants:
                runs = [r for r in runs if not (r["size"] == n and r["time_limit"] == limit and r["variant"] == variant
                                                and r.get("budget_shift", 0.0) == args.budget_shift)]
                row = {"size": n, "budget_shift": args.budget_shift, **solve(graph, S, C, params, variant, limit)}
                runs.append(row)
                print(json.dumps({k: row.get(k) for k in ("size", "variant", "time_limit", "elapsed_s", "status",
                                                            "termination", "objective", "gap", "unfilled_seats",
                                                            "concurrent_people", "people_used", "partial_alloc_entries",
                                                            "mean_seat_fit", "budget_use", "warm_start_unfilled")},
                                 ensure_ascii=False), flush=True)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps({"generated_at": _now(), "env": _env_info(), "share": SHARE,
                                                "penalty": PENALTY, "data": data, "runs": runs},
                                               ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
