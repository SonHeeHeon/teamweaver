"""Plan A solve quality per setting: incumbent, HiGHS's proven bound, gap and objective parts.

    uv run --group benchmark python -m rehearsal.solve_probe --size 100 --limits 60 \
        --configs '{}' '{"clique_threshold_months": 24}'

Why (2026-10-06): on the realistic 10-year history (core/ingest/org_profile) plan A always stops at the time
limit. Whether that means "a poor plan" or "a good plan the solver cannot prove" depends on the bound, so
every run records incumbent, bound and gap next to the objective parts from the independent plan evaluator.
Results go to rehearsal/results/n<size>/solve-probe.json (appended per run, newest last).
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

from rehearsal.run import RESULTS, _bundle, _env_info, _now


def probe(size: int, limits: list[int], configs: list[dict]) -> dict:
    from api.settings import PlacementSettings
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.milp import _overfamiliar_pairs, solve_milp_assessment
    from core.scoring.engine import ScoringEngine
    work = Path(tempfile.mkdtemp(prefix=f"solve-probe-n{size}-"))
    bundle, report = load_bundle(_bundle(size, work))
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    base = PlacementSettings().to_milp_params().model_copy(update={"solver": "highs"})
    runs = []
    for cfg in configs:
        for limit in limits:
            params = base.model_copy(update={"time_limit": limit, **cfg})
            row = {"config": cfg, "time_limit": limit,
                   "overfamiliar_pairs": len(_overfamiliar_pairs(graph, params.clique_threshold_months, getattr(params, "clique_window_months", None)))}
            t = time.perf_counter()
            try:
                a = solve_milp_assessment(graph, S, C, params)
                ev = a.native_capture.evidence if a.native_capture is not None else None
                cand = a.accepted
                row.update({"elapsed_s": round(time.perf_counter() - t, 1),
                            "termination": getattr(ev, "termination_reason", None),
                            "accepted": cand is not None,
                            "objective": None if cand is None else round(cand.objective, 4),
                            "best_bound": None if ev is None or ev.best_bound is None else round(ev.best_bound, 4)})
                if cand is not None:
                    o = evaluate_plan(graph, S, C, params, list(cand.plan.entries)).objective
                    row["parts"] = {k: round(getattr(o, k), 3) for k in ("skill", "synergy", "overfamiliarity", "unfilled")}
                    if row["best_bound"] is not None and abs(cand.objective) > 1e-9:
                        # HiGHS's mip_gap definition (what gapRel is compared with): |bound - incumbent| / |incumbent|
                        row["gap"] = round(abs(row["best_bound"] - cand.objective) / abs(cand.objective), 4)
            except RuntimeError as exc:
                row.update({"elapsed_s": round(time.perf_counter() - t, 1), "error": str(exc)[:200]})
            print(json.dumps(row, ensure_ascii=False), flush=True)
            runs.append(row)
    return {"size": size, "env": _env_info(), "started_at": _now(), "dataset": bundle.manifest.get("dataset_id"),
            "people": len(ds.people), "projects": len(ds.projects), "coworks": len(ds.coworks), "runs": runs}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, required=True, choices=(100, 200, 300))
    ap.add_argument("--limits", type=int, nargs="+", default=[60])
    ap.add_argument("--configs", nargs="+", default=["{}"], help="MilpParams overrides as JSON, one per run")
    args = ap.parse_args()
    out = probe(args.size, args.limits, [json.loads(c) for c in args.configs])
    path = RESULTS / f"n{args.size}" / "solve-probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(path.read_text("utf-8")) if path.exists() else []
    history.append(out)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=1), "utf-8")
    print(f"written to {path}")


if __name__ == "__main__":
    main()
