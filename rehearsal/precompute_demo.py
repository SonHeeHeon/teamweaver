"""시연 묶음의 결과를 미리 계산한다(시연 확장 E, 2026-10-06) -- 서버가 api/demo_precomputed로 검증해 "미리 계산"으로 보여 준다.

    TEAMWEAVER_SOLVER_SEEDS=4 uv run --group benchmark python -m rehearsal.precompute_demo            # demo/의 모든 묶음
    TEAMWEAVER_SOLVER_SEEDS=4 uv run --group benchmark python -m rehearsal.precompute_demo --only org-n300 org-n300-operating

**시연 기기에서, 서버와 같은 환경으로 돌린다**: 저장소 .env(리뷰 글 판정 LLM), ~/.teamweaver의 관리자 설정·판정 캐시.
데이터셋 버전에 리뷰 글 판정값이 들어가므로 환경이 다르면 서버가 버전 불일치로 쓰지 않는다(로그·/api/datasets/active).
관리자 설정을 바꾸면 다시 돌린다. 걸리는 시간(시드 4): 100명 약 2분, 200명 약 4분, 300명 약 12분 + 운영 중 비교 1~2분씩.
결과: demo/precomputed/<묶음 id>.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run(preset) -> dict:
    from api.demo_precomputed import FORMAT
    from core.evaluate.plan_eval import evaluate_plan
    from api.demo_presets import build_demo_active
    from api.settings import SettingsStore, default_settings_path
    from core.evaluate.operating import compare_move_budgets
    from core.optimize.alternatives import generate_plans_streaming
    from core.optimize.milp import effective_seeds
    from core.scoring.engine import ScoringEngine

    t0 = time.perf_counter()
    active = build_demo_active(preset.path)
    graph = active.graph
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = SettingsStore(default_settings_path()).current().settings.to_milp_params(n_people=len(graph.people))
    out = {"format": FORMAT, "preset": preset.id, "dataset_id": active.info.dataset_id,
           "dataset_version": active.info.version, "review_judge": active.info.review_judge,
           "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "commit": _commit(),
           "solver_seeds": effective_seeds(params), "build_s": round(time.perf_counter() - t0, 2)}
    plans, outcome, t = [], {}, time.perf_counter()
    for plan in generate_plans_streaming(graph, S, C, params, 3, outcome):
        # 서버는 이 평가기 점수와 다시 채점한 점수를 대조한다(솔버 목적값은 내림 전 투입률 기준이라 다르다)
        ev = evaluate_plan(graph, S, C, params, plan.entries)
        plans.append({**plan.model_dump(), "eval_objective": ev.objective.total,
                      "eval_violations": [v.code for v in ev.violations],
                      "elapsed_s": round(time.perf_counter() - t, 2)})
        print(f"  {preset.id} 안 {plan.label}: {plans[-1]['elapsed_s']}초 · 목적 {plan.objective:.2f} · "
              f"미충원 항목 {len(plan.unfilled)} · 시간 한도 {plan.time_limited}", flush=True)
    out["optimize"] = {"weights": {}, "n_alternatives": 3, "milp_params": params.model_dump(),
                       "elapsed_s": round(time.perf_counter() - t, 2), "stop_reason": outcome.get("stop_reason"),
                       "plans": plans}
    if preset.manifest.get("scenario") == "operating":
        t = time.perf_counter()
        rows = compare_move_budgets(graph, S, C, params, active.current, ks=(0, 1, 2, 3))
        out["operating"] = {"ks": [0, 1, 2, 3], "milp_params": params.model_dump(),
                            "elapsed_s": round(time.perf_counter() - t, 2), "rows": rows}
        print(f"  {preset.id} 운영 중 비교: {out['operating']['elapsed_s']}초 · "
              + ", ".join(f"K={r['k']} 품질 {r.get('quality')} 빈자리 {r.get('unfilled_seats')}" for r in rows), flush=True)
    else:
        out["operating"] = None
    active.retire()
    return out


def main() -> None:
    from core.config import load_env
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="묶음 id(기본: demo/의 모든 시연 묶음)")
    args = ap.parse_args()
    load_env()
    os.environ.setdefault("TEAMWEAVER_DEMO_DIR", str(DEMO))
    from api.demo_presets import list_presets
    presets = [p for p in list_presets() if not args.only or p.id in args.only]
    from api.demo_precomputed import precomputed_path
    for preset in presets:
        print(f"{preset.id} ...", flush=True)
        data = run(preset)
        path = precomputed_path(preset.path)            # 서버가 찾는 곳(묶음 폴더 옆 precomputed/)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        print(f"written {path}", flush=True)


if __name__ == "__main__":
    main()
