"""최적화 배치 vs 단순 규칙 배치 — 시연 데이터 100/200/300명(시연 확장 A 측정, 2026-10-06).

    TEAMWEAVER_SOLVER_SEEDS=4 uv run --group benchmark python -m rehearsal.baseline_check   # -> rehearsal/results/baseline-check.json

같은 데이터·같은 설정(서비스 기본값)으로 MILP 안 A와 단순 규칙(core/optimize/greedy, 같은 최소 투입률)을 현행 평가기로 채점한다.
"""
import argparse
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

from rehearsal.run import RESULTS, _env_info, _now

DEMO = Path(__file__).resolve().parents[1] / "demo"
OUT = RESULTS / "baseline-check.json"


def _bundle(n: int) -> Path:
    if n == 100:
        return DEMO / "org-n100"
    d = Path(tempfile.mkdtemp(prefix=f"baseline-n{n}-"))
    zipfile.ZipFile(io.BytesIO((DEMO / f"org-n{n}.zip").read_bytes())).extractall(d)
    return d


def run(n: int) -> dict:
    from api.settings import PlacementSettings
    from core.evaluate.baseline import compare_with_baseline
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.milp import solve_milp_assessment
    from core.scoring.engine import ScoringEngine
    b, rep = load_bundle(_bundle(n))
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = PlacementSettings().to_milp_params(n_people=len(ds.people))
    t = time.perf_counter()
    a = solve_milp_assessment(g, S, C, params)
    elapsed = round(time.perf_counter() - t, 2)
    ev = a.native_capture.evidence if a.native_capture is not None else None
    return {"size": n, "dataset": f"demo/org-n{n}", "solve_s": elapsed,
            "termination": getattr(ev, "termination_reason", None),
            **compare_with_baseline(g, S, C, params, list(a.accepted.plan.entries))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 200, 300])
    args = ap.parse_args()
    runs = []
    for n in args.sizes:
        r = run(n)
        print(json.dumps({"size": n, "solve_s": r["solve_s"], "difference": r["difference"]}, ensure_ascii=False), flush=True)
        runs.append(r)
    OUT.write_text(json.dumps({"env": _env_info(), "finished_at": _now(), "runs": runs}, ensure_ascii=False, indent=1), "utf-8")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
