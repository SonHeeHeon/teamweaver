"""C6 측정: 동시 프로젝트 상한(K)이 서비스 계산(Plan A + 대안 3개)의 시간·점수에 주는 영향.

동결 fixture(100명·20프로젝트), 서비스 설정 기본값(관리자 설정, 솔버 HiGHS)에서 K만 바꿔 같은 계산을
반복한다(K=6은 사실상 제한 없음 -- 기본 최소 투입률 0.3이면 4곳부터 가용률이 먼저 막는다).
실행: uv run --group benchmark python -m experiments.c6.concurrency_cost outputs/c6-concurrency-cost.json
합성 데이터, 목적값은 현행 모델 점수이며 사업 효과는 NOT_CALIBRATED."""
import argparse
import json
import platform
import time
from pathlib import Path

from api.settings import PlacementSettings
from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.graph.memory_graph import MemoryGraph
from core.optimize.alternatives import generate_plans
from core.scoring.engine import ScoringEngine


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output", type=Path)
    ap.add_argument("--order", default="6,3,6,3,1", help="측정 순서(반복으로 편차를 본다)")
    args = ap.parse_args()
    ds, parsed = load_fixtures(FIXTURES_DIR)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    rows = []
    for k in (int(x) for x in args.order.split(",")):
        params = PlacementSettings(max_concurrent_projects=k).to_milp_params()
        t = time.monotonic()
        outcome: dict = {}                     # 대안이 멈춘 사유(core.optimize.alternatives) -- 비면 정상 완료
        plans = generate_plans(g, S, C, params, 3, outcome)
        rows.append({"K": k, "seconds": round(time.monotonic() - t, 1), "labels": [p.label for p in plans],
                     "objectives": [round(p.objective, 3) for p in plans], "alternatives_stop": outcome})
        print(rows[-1], flush=True)
    out = {"experiment": "c6-concurrency-cost", "business_validity": "NOT_CALIBRATED", "fixture": "100x20",
           "params": PlacementSettings().to_milp_params().model_dump(), "varied": "max_concurrent_projects",
           "command": "uv run --group benchmark python -m experiments.c6.concurrency_cost " + str(args.output),
           "platform": platform.platform(), "measured_at": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
