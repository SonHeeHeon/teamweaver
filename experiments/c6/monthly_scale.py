"""월별 투입률 서비스 반영 후 규모별 비용: 조직형 100·200·300명에서 Plan A를 고정·월별로 풀어 비교한다.

데이터는 claude-a 리허설과 같은 조직형 묶음(core/ingest/org_profile, seed 2026). 시간 한도는 인원별 권장값
(core/optimize/time_budget.recommend). 서비스 설정 기본값에서 allocation_mode만 바꾼다(HiGHS 1스레드).
실행: uv run --group benchmark python -m experiments.c6.monthly_scale outputs/c6-monthly-scale.json
합성 데이터, 목적값은 현행 모델 점수이며 사업 효과는 NOT_CALIBRATED."""
import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from api.settings import PlacementSettings
from core.graph.memory_graph import MemoryGraph
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import generate_org_bundle
from core.optimize.milp import solve_milp_assessment
from core.optimize.time_budget import recommend
from core.scoring.engine import ScoringEngine


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output", type=Path)
    ap.add_argument("--sizes", default="100,200,300")
    ap.add_argument("--modes", default="fixed,monthly")
    ap.add_argument("--limits", default="", help="권장값 대신 쓸 시간 한도들(쉼표) -- 규모마다 전부 시도")
    args = ap.parse_args()
    rows = []
    for size in (int(x) for x in args.sizes.split(",")):
        work = Path(tempfile.mkdtemp(prefix=f"monthly-n{size}-"))
        bundle, report = load_bundle(generate_org_bundle(work / "b", size, seed=2026))
        ds, parsed = to_dataset(bundle, report)
        g = MemoryGraph.build(ds, parsed)
        eng = ScoringEngine(g)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        limits = [int(x) for x in args.limits.split(",") if x] or [recommend(len(g.people)).per_solve_s]
        for mode, limit in ((m, lim) for lim in limits for m in args.modes.split(",")):
            params = PlacementSettings().to_milp_params().model_copy(
                update={"allocation_mode": mode, "time_limit": limit})
            t = time.perf_counter()
            row = {"people": len(g.people), "projects": len(g.projects), "mode": mode, "time_limit": params.time_limit}
            try:
                a = solve_milp_assessment(g, S, C, params)
                cand = a.accepted
                row.update({"accepted": cand is not None, "objective": None if cand is None else round(cand.objective, 3),
                            "unfilled": None if cand is None else len(cand.plan.unfilled),
                            "entries": None if cand is None else len(cand.plan.entries),
                            "monthly_entries": None if cand is None else sum(e.monthly_alloc is not None
                                                                            for e in cand.plan.entries),
                            "termination": a.native_capture.evidence.termination_reason if a.native_capture else None,
                            "refinement": a.refinement.reason, "variables": a.validation_candidate.variable_count})
            except Exception as exc:              # noqa: BLE001 -- 실패도 결과로 남긴다
                row.update({"accepted": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
            row["wall_s"] = round(time.perf_counter() - t, 1)
            rows.append(row)
            print(row, flush=True)
    out = {"experiment": "c6-monthly-scale", "business_validity": "NOT_CALIBRATED", "solver": "HiGHS 1 thread",
           "data": "core.ingest.org_profile seed 2026", "time_limit": "core.optimize.time_budget.recommend",
           "command": f"uv run --group benchmark python -m experiments.c6.monthly_scale {args.output}",
           "platform": platform.platform(), "measured_at": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
