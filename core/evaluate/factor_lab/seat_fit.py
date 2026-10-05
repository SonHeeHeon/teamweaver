"""Roadmap item 3 experiment: does the per-seat fit term (MilpParams.seat_fit_weight, beta) close the fit gap
that small, short and proposal projects suffer under the S x allocation objective, and what does it cost?

    uv run --group benchmark python -m core.evaluate.factor_lab.seat_fit --size 100 --betas 0 0.25 0.5 1 --time-limit 60

For each beta the service MILP (HiGHS) solves plan A; the plan is then described by
  - mean fit of the people placed, by project trait (robustness.breakdown) and the small-vs-large gaps,
  - the allocation-weighted skill term sum S*a under beta = 0 (what the old objective valued),
  - fill, and the simulated outcome under the held-out assumptions T6/T7 and T1 (simulator.py).
Writes rehearsal/results/factor_lab/seat_fit_n{size}.json.
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

import numpy as np

from api.settings import PlacementSettings
from core.evaluate.factor_lab.factors import compute_factors
from core.evaluate.factor_lab.robustness import breakdown
from core.evaluate.factor_lab.simulator import SCENARIOS, simulate
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from core.optimize.milp import _overfamiliar_pairs, solve_milp_assessment
from core.scoring.engine import ScoringEngine

OUT = Path(__file__).resolve().parents[3] / "rehearsal" / "results" / "factor_lab"
GAPS = (("phase", "제안", "실행"), ("seats", "정원 1~3", "정원 8+"), ("months", "기간 1~2개월", "기간 5~6개월"))


def _gap(table, trait, small, large):
    rows = {r["group"]: r for r in table[trait]}
    a, b = rows.get(small, {}).get("mean_fit"), rows.get(large, {}).get("mean_fit")
    return None if a is None or b is None else round(b - a, 3)


def run(size: int, betas, time_limit: int, seed: int = 2026) -> dict:
    root = generate_org_bundle(Path(tempfile.mkdtemp()) / "b", size, seed=seed)
    bundle, report = load_bundle(root)
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    F = compute_factors(graph, bundle)
    base = PlacementSettings().to_milp_params().model_copy(update={"time_limit": time_limit})
    overfam = {tuple(sorted(p)) for p in _overfamiliar_pairs(graph, base.clique_threshold_months)}
    held = [s for s in SCENARIOS if s.name in ("T1", "T6", "T7")]
    rows = []
    for beta in betas:
        params = base.model_copy(update={"seat_fit_weight": beta})
        t = time.perf_counter()
        a = solve_milp_assessment(graph, S, C, params)
        cand = a.accepted
        row = {"beta": beta, "solve_s": round(time.perf_counter() - t, 1),
               "termination": getattr(a.validation_candidate.evidence, "termination_reason", None)}
        if cand is None:
            row["error"] = a.refinement.reason
            rows.append(row)
            print(f"[seat-fit n{size}] beta={beta} no plan ({row['error']})", flush=True)
            continue
        entries, unfilled = cand.plan.entries, cand.plan.unfilled
        table = breakdown(graph, S, entries, unfilled)
        old = evaluate_plan(graph, S, C, base, entries)          # beta = 0: what the old objective valued
        row.update({
            "people": len({e.person_id for e in entries}), "entries": len(entries), "unfilled": len(unfilled),
            "old_objective_skill": round(old.objective.skill, 3), "old_objective_total": round(old.objective.total, 3),
            "gaps": {t: _gap(table, t, s_, l_) for t, s_, l_ in GAPS},
            "mean_fit_all": round(float(np.mean([S[graph.pid_index[e.person_id], graph.project_index[e.project_id]]
                                                 for e in entries])), 3),
            "outcomes": {sc.name: round(float(np.mean([simulate(graph, F, C, overfam, entries, sc, s, unfilled).total
                                                       for s in (1, 2, 3)])), 4) for sc in held},
            "breakdown": table})
        rows.append(row)
        print(f"[seat-fit n{size}] beta={beta} {row['solve_s']}s gaps={row['gaps']} fit={row['mean_fit_all']} "
              f"oldskill={row['old_objective_skill']} unfilled={row['unfilled']} out={row['outcomes']}", flush=True)
    return {"size": size, "seed": seed, "time_limit": time_limit, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=100, choices=(100, 200, 300))
    ap.add_argument("--betas", type=float, nargs="+", default=[0.0, 0.25, 0.5, 1.0])
    ap.add_argument("--time-limit", type=int, default=60)
    args = ap.parse_args()
    res = run(args.size, args.betas, args.time_limit)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"seat_fit_n{args.size}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n",
                                                     encoding="utf-8")


if __name__ == "__main__":
    main()
