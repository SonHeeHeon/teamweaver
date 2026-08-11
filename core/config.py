import json, os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures"

# Default per-skill weight when a caller's `weights` dict has no entry for a skill.
# Shared by ScoringEngine.skill_matrix (core/scoring/engine.py) and
# matching_fulfillment (core/optimize/metrics.py) -- both must agree on the same
# default or S (the optimizer's objective input) and the fulfillment metric would
# silently score the same unweighted skill differently.
DEFAULT_WEIGHT = 3.0

# MILP optimization_ratio policy target from the PoC spec (Hybrid Optimization
# requirement: "MILP로 최적화율 90%+"). This is a policy threshold, not a
# measurement -- experiments/report.py compares measured optimization_ratio
# against it to flag which scales fall short. Pinned here (a tracked file)
# rather than left as a bare literal in the report generator.
OPT_RATIO_TARGET = 0.90

def load_review_items() -> list[str]:
    return json.loads((FIXTURES_DIR / "review_items.json").read_text("utf-8"))

def load_pricing() -> dict:
    return json.loads((FIXTURES_DIR / "pricing.json").read_text("utf-8"))

def load_env() -> None:
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                os.environ.setdefault(k.strip(), v)
