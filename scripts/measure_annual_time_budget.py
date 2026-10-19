"""12개월 계획 시간 한도 측정(claude-b 2026-10-19): 시연 원본(org-n200·n300 zip 등)으로 연간형 묶음을 만들어(scripts/make_annual_demo.make)
서비스 기본(전일 투입·초기해)으로 안 A만 풀고 rehearsal.time_budget_full과 같은 칸을 남긴다 → api/settings.MEASURED_FULL_12의 근거.

    uv run --group benchmark python scripts/measure_annual_time_budget.py org-n200,org-n300 [--limit 초] [--seeds 1 4]
        → rehearsal/results/time-budget-annual.json(실행마다 이어 쓴다). 한도를 안 주면 그 규모의 자동 한도(recommended_seconds).
리뷰 글은 항목 균형 판정(LLM 없음). 사업 효과는 NOT_CALIBRATED -- 계산 시간만 잰다.
"""
import io
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rehearsal" / "results" / "time-budget-annual.json"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("bundles", help="쉼표로 구분한 demo/ 원본 이름(폴더 또는 zip, 6개월 연초 계획 묶음)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 4])
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from rehearsal.run import _env_info, _now
    from make_annual_demo import make
    from api.settings import PlacementSettings, recommended_seconds
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.milp import solve_milp_assessment
    from core.scoring.engine import ScoringEngine
    runs = json.loads(OUT.read_text("utf-8"))["runs"] if OUT.exists() else []
    for name in args.bundles.split(","):
        if (ROOT / "demo" / name).is_dir():
            src = ROOT / "demo" / name
        else:
            src = Path(tempfile.mkdtemp(prefix=f"src-{name}-"))
            zipfile.ZipFile(io.BytesIO((ROOT / "demo" / f"{name}.zip").read_bytes())).extractall(src)
            if not (src / "manifest.json").exists():
                src = next(p.parent for p in src.rglob("manifest.json"))
        dst = make(src=src, dst=Path(tempfile.mkdtemp(prefix=f"annual-{name}-")) / "b")
        b, rep = load_bundle(dst)
        ds, parsed = to_dataset(b, rep)
        graph = MemoryGraph.build(ds, parsed)
        eng = ScoringEngine(graph)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        months = len(graph.people[0].availability)
        limit = args.limit or recommended_seconds(len(graph.people), "full", months)
        for seeds in args.seeds:
            params = PlacementSettings().to_milp_params().model_copy(update={"time_limit": limit, "solver_seeds": seeds})
            t = time.perf_counter()
            a = solve_milp_assessment(graph, S, C, params)
            elapsed = time.perf_counter() - t
            ev = a.native_capture.evidence if a.native_capture is not None else None
            row = {"bundle": f"{name}-annual", "people": len(graph.people), "projects": len(graph.projects), "months": months,
                   "seeds": seeds, "time_limit": limit, "elapsed_s": round(elapsed, 1), "accepted": a.accepted is not None,
                   "termination": getattr(ev, "termination_reason", None),
                   "best_bound": None if ev is None or ev.best_bound is None else round(ev.best_bound, 3),
                   "warm_start_unfilled": None if ev is None else ev.options.get("warm_start_unfilled")}
            if a.accepted is not None:
                plan = a.accepted.plan
                e = evaluate_plan(graph, S, C, params, plan.entries)
                row.update({"objective": round(plan.objective, 3), "unfilled_seats": sum(s.missing for s in e.shortfalls),
                            "violations": len(e.violations),
                            "gap": (round(abs(row["best_bound"] - plan.objective) / max(abs(plan.objective), 1e-9), 4)
                                    if row["best_bound"] is not None else None)})
            runs = [r for r in runs if not (r["bundle"] == row["bundle"] and r["seeds"] == seeds
                                            and r["time_limit"] == limit)] + [row]
            print(json.dumps(row, ensure_ascii=False), flush=True)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps({"generated_at": _now(), "env": _env_info(),
                                       "note": "생성기 v4 원본(org-n200·n300)으로 만든 12개월 연간형 묶음, 서비스 기본(전일 투입·초기해), 안 A만. "
                                               "동시 수행 허용 0명. 한도는 측정 당시 자동 한도(6개월 권장 × 4).",
                                       "runs": runs}, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
