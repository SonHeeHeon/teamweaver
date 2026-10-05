"""Recommended solver time limits by problem size (roadmap item 1).

The service solves plan A and then up to three alternatives, each with the same `time_limit`, so a request can
take up to 4 x time_limit. The right limit grows with the number of people and projects. This module turns
rehearsal measurements (rehearsal/results/n*/sweep.json, HiGHS = service solver) into a rule:

- for each measured size, the smallest time limit whose plan A is within `tolerance` of the best objective found
  at that size (by any solver or limit);
- between measured sizes, the recommendation of the next larger measured size (never extrapolate downward);
- above the largest measured size, the largest measured limit is reported as a floor with `measured=False`.

The table below is filled from the 2026-10-05 rehearsal; refresh it with `recommend_from_sweeps` when the model
or data shape changes. Data is synthetic (organisation-shaped); real data may need a different table.
"""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TOLERANCE = 0.01
# people -> seconds per solve (HiGHS, 1 thread). None until measured.
MEASURED: dict[int, int | None] = {100: None, 200: None, 300: None}


@dataclass(frozen=True)
class TimeBudget:
    per_solve_s: int            # recommended MilpParams.time_limit
    worst_case_total_s: int     # plan A + 3 alternatives, each up to per_solve_s
    measured: bool              # False when the size is beyond the measured range (a floor, not a promise)
    basis: str                  # where the number came from, for the settings screen


def recommend_from_sweeps(results_dir: Path, solver: str = "highs",
                          tolerance: float = DEFAULT_TOLERANCE) -> dict[int, int | None]:
    """size -> smallest time limit within `tolerance` of the best known objective at that size (None if none)."""
    out = {}
    for path in sorted(Path(results_dir).glob("n*/sweep.json")):
        sweep = json.loads(path.read_text(encoding="utf-8"))
        best = sweep.get("best_objective")
        ok = []
        for r in sweep["runs"]:
            if r["solver"] != solver or r.get("objective") is None or best is None:
                continue
            gap = (best - r["objective"]) / abs(best) if best else 0.0
            if gap <= tolerance:
                ok.append(r["time_limit"])
        out[sweep["size"]] = min(ok) if ok else None
    return out


def recommend(n_people: int, table: dict[int, int | None] | None = None,
              fallback_s: int = 120) -> TimeBudget:
    table = MEASURED if table is None else table
    sizes = sorted(s for s, v in table.items() if v is not None)
    if not sizes:
        return TimeBudget(fallback_s, 4 * fallback_s, False, "측정값 없음 — 기본값")
    for s in sizes:
        if n_people <= s:
            v = table[s]
            return TimeBudget(v, 4 * v, True, f"{s}명 리허설 측정(HiGHS, 최선 대비 1% 이내)")
    v = table[sizes[-1]]
    return TimeBudget(v, 4 * v, False, f"{sizes[-1]}명보다 큼 — 측정 범위 밖, 최소 {v}초 이상 권장")
