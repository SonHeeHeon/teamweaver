"""Read-only actual API lifespan smoke, with native/final solver evidence capture."""
import argparse
import asyncio
from dataclasses import asdict
import os
from pathlib import Path
import time

from experiments.phase1.checkpoint import atomic_write_json


async def run():
    from api.main import app, lifespan
    from api.cache import ResultCache
    from core.optimize import milp
    original = milp.solve_milp_assessment
    captures = []
    def capture(*args,**kwargs):
        result = original(*args,**kwargs)
        captures.append({"native_validation":asdict(result.native_validation),
                         "initial_validation":asdict(result.initial_validation),
                         "final_validation":asdict(result.final_validation) if result.final_validation else None,
                         "refinement":asdict(result.refinement),
                         "objective":result.accepted.objective if result.accepted else None})
        return result
    saved = os.environ.get("TEAMWEAVER_SKIP_WARM")
    os.environ["TEAMWEAVER_SKIP_WARM"] = "0"
    milp.solve_milp_assessment = capture
    start = time.monotonic()
    try:
        async with lifespan(app):
            plans = app.state.cache.get(ResultCache.key({}, {}, 3))
            if not plans or not captures:
                raise RuntimeError("actual default warm-up did not populate cache")
            return {"status":"PASS","actual_lifespan":True,"skip_warm":False,
                    "elapsed_seconds":time.monotonic()-start,"plans":[p.model_dump(mode="json") for p in plans],
                    "assessments":captures,"business_validity":"NOT_CALIBRATED"}
    finally:
        milp.solve_milp_assessment = original
        if saved is None: os.environ.pop("TEAMWEAVER_SKIP_WARM",None)
        else: os.environ["TEAMWEAVER_SKIP_WARM"] = saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    result = asyncio.run(run())
    atomic_write_json(args.output,result)
    print(f"actual_lifespan={result['status']} plans={len(result['plans'])} elapsed={result['elapsed_seconds']:.3f}s")
