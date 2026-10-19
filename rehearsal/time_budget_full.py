"""전일 투입 시간 한도 측정(2026-10-19, claude-a -- 설계 .omc/plan/2026-10-19-concurrency.md 6절, claude-b 요청 (4)).

생성기 v3 시연 묶음(연초 100·200·300명 6개월 + 연간 100명 12개월)을 서비스 기본 설정(전일 투입, 초기해 포함)으로 안 A만 풀어
시간 한도·시드 수마다 빈자리·목적값·증명 상한·종료 사유를 잰다 → `core/optimize/time_budget.MEASURED_FULL`과
`api/settings.HORIZON_TIME_FACTOR`(12개월 배수)를 정하는 근거.

    TEAMWEAVER_SOLVER_SEEDS 무시 -- 시드 수는 인자로:
    uv run --group benchmark python -m rehearsal.time_budget_full --seeds 1 4 --limits 30 60 120 [--only org-n100 ...]
        → rehearsal/results/time-budget-full.json (실행마다 이어 쓴다)
리뷰 글은 항목 균형 판정(LLM 없음) -- 같은 입력이면 설정 간 비교가 된다. 사업 효과는 NOT_CALIBRATED.
"""
from __future__ import annotations

import argparse
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

from rehearsal.run import RESULTS, _env_info, _now

DEMO = Path(__file__).resolve().parents[1] / "demo"
OUT = RESULTS / "time-budget-full.json"
BUNDLES = ("org-n100", "org-n200", "org-n300", "org-n100-annual")


def _root(name: str) -> Path:
    if (DEMO / name).is_dir():
        return DEMO / name
    d = Path(tempfile.mkdtemp(prefix=f"tb-{name}-"))
    zipfile.ZipFile(io.BytesIO((DEMO / f"{name}.zip").read_bytes())).extractall(d)
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--limits", type=int, nargs="+", default=[30, 60, 120])
    ap.add_argument("--only", nargs="+", default=list(BUNDLES))
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    from api.settings import PlacementSettings
    from core.evaluate.plan_eval import evaluate_plan
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.optimize.milp import solve_milp_assessment
    from core.scoring.engine import ScoringEngine
    doc = json.loads(args.out.read_text("utf-8")) if args.out.exists() else {"runs": []}
    for name in args.only:
        b, rep = load_bundle(_root(name))
        ds, parsed = to_dataset(b, rep)
        graph = MemoryGraph.build(ds, parsed)
        eng = ScoringEngine(graph)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        base = PlacementSettings().to_milp_params()
        months = len(graph.people[0].availability)
        for seeds in args.seeds:
            for limit in args.limits:
                params = base.model_copy(update={"time_limit": limit, "solver_seeds": seeds})
                t = time.perf_counter()
                a = solve_milp_assessment(graph, S, C, params)
                elapsed = time.perf_counter() - t
                ev = a.native_capture.evidence if a.native_capture is not None else None
                row = {"bundle": name, "people": len(graph.people), "months": months, "seeds": seeds, "time_limit": limit,
                       "elapsed_s": round(elapsed, 1), "accepted": a.accepted is not None,
                       "termination": getattr(ev, "termination_reason", None),
                       "best_bound": None if ev is None or ev.best_bound is None else round(ev.best_bound, 3),
                       "warm_start_unfilled": None if ev is None else ev.options.get("warm_start_unfilled")}
                if a.accepted is not None:
                    plan = a.accepted.plan
                    e = evaluate_plan(graph, S, C, params, plan.entries)
                    row.update({"objective": round(plan.objective, 3),
                                "quality": round(e.objective.skill + e.objective.synergy, 3),
                                "unfilled_seats": sum(s.missing for s in e.shortfalls),
                                "violations": len(e.violations),
                                "gap": (round(abs(row["best_bound"] - plan.objective) / max(abs(plan.objective), 1e-9), 4)
                                        if row["best_bound"] is not None else None)})
                doc["runs"] = [r for r in doc["runs"] if not (r["bundle"] == name and r["seeds"] == seeds
                                                              and r["time_limit"] == limit)] + [row]
                print(json.dumps(row, ensure_ascii=False), flush=True)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps({"generated_at": _now(), "env": _env_info(),
                                                "note": "생성기 v3 시연 묶음, 서비스 기본(전일 투입·초기해), 안 A만. 동시 수행 허용 0명.",
                                                "runs": doc["runs"]}, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
