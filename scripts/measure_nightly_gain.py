"""밤 배치 "긴 시간으로 다시 풀기"가 안 A를 얼마나 낫게 하는지 잰다(2026-10-18, claude-b).

낮 시간 한도(관리자 기본 설정의 자동 한도: 100명 30초 · 200명 60초 · 300명 180초)와 밤 한도로 같은 데이터·같은 설정의
안 A를 각각 풀어 목적값·증명 상한·빈자리를 비교한다. 시연 묶음(가상 데이터)만 쓰고, 리뷰 글 판정은 동봉 파일
(demo/review_judgments.json)로 채워 AI를 부르지 않는다. 판정 캐시는 임시 폴더에 둔다(~/.teamweaver를 건드리지 않음).

    TEAMWEAVER_SOLVER_SEEDS=4 uv run python scripts/measure_nightly_gain.py --out outputs/nightly-long-solve.json
    TEAMWEAVER_SOLVER_SEEDS=4 uv run python scripts/measure_nightly_gain.py --only org-n100 --night 600

결과는 측정값일 뿐이다 -- 가상 데이터, 사업 효과 NOT_CALIBRATED.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_NIGHT = {"org-n100": 600, "org-n200": 900, "org-n300": 1800}


def _bundle(name: str) -> Path:
    for cand in (ROOT / "demo" / name, ROOT / "demo" / f"{name}.zip"):
        if cand.exists():
            return cand
    raise SystemExit(f"시연 묶음을 찾지 못했다: {name}")


def _solve_a(graph, S, C, params) -> dict:
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.alternatives import generate_plans
    t0 = time.perf_counter()
    outcome: dict = {}
    plans = generate_plans(graph, S, C, params, n_alternatives=0, outcome=outcome)
    elapsed = time.perf_counter() - t0
    a = plans[0]
    ev = evaluate_plan(graph, S, C, params, a.entries)
    bound = a.best_bound
    return {"time_limit": params.time_limit, "elapsed_s": round(elapsed, 1), "termination": a.termination,
            "objective": a.objective, "eval_objective": ev.objective.total, "best_bound": bound,
            "gap_to_bound": (None if bound in (None, 0) else (bound - a.objective) / abs(bound)),
            "unfilled": len(a.unfilled), "violations": len(ev.violations)}


def measure(name: str, night_s: int) -> dict:
    from api.demos import build_demo
    from api.settings import PlacementSettings
    from core.optimize.milp import effective_seeds
    from core.scoring.engine import ScoringEngine
    active = build_demo(_bundle(name))
    graph = active.graph
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    day = PlacementSettings().to_milp_params(n_people=len(graph.people))
    night = day.model_copy(update={"time_limit": int(night_s)})
    row = {"bundle": name, "n_people": len(graph.people), "n_projects": len(graph.projects),
           "dataset_version": active.info.version, "solver_seeds": effective_seeds(day),
           "day": _solve_a(graph, S, C, day)}
    print(f"{name} day  {row['day']}", flush=True)
    row["night"] = _solve_a(graph, S, C, night)
    print(f"{name} night {row['night']}", flush=True)
    d, n = row["day"], row["night"]
    row["gain"] = {"objective": n["objective"] - d["objective"],
                   "objective_rel": (n["objective"] - d["objective"]) / abs(d["objective"]) if d["objective"] else None,
                   "unfilled": n["unfilled"] - d["unfilled"]}
    active.retire()
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", default=list(DEFAULT_NIGHT))
    ap.add_argument("--night", type=int, default=None, help="밤 한도(초). 생략하면 묶음별 기본값")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "nightly-long-solve.json")
    args = ap.parse_args(argv)
    os.environ.setdefault("TEAMWEAVER_DATA_DIR", tempfile.mkdtemp(prefix="tw-nightly-measure-"))
    from core.config import load_env
    load_env()
    out = {"measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "note": "가상 데이터. 낮 한도 = 관리자 기본 설정의 자동 한도, 밤 한도 = 밤 배치 후보값. 사업 효과 NOT_CALIBRATED.",
           "rows": []}
    for name in args.only:
        out["rows"].append(measure(name, args.night or DEFAULT_NIGHT.get(name, 900)))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
