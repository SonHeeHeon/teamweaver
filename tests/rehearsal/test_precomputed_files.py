"""The committed demo/precomputed/*.json files have the shape the server checks (api/demo_precomputed).

Whether they are *current* depends on the review-text LLM judgments on the demo machine (part of the dataset version),
so the server re-checks at boot and says why it skips a file (/api/datasets/active -> precomputed.skipped)."""
import json
from pathlib import Path

import pytest

from api.demo_precomputed import FORMAT

DEMO = Path(__file__).resolve().parents[2] / "demo"
FILES = sorted((DEMO / "precomputed").glob("*.json"))


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_precomputed_file_shape(path):
    data = json.loads(path.read_text("utf-8"))
    bundle = DEMO / data["preset"] if (DEMO / data["preset"]).is_dir() else DEMO / f"{data['preset']}.zip"
    assert data["format"] == FORMAT and path.stem == data["preset"] and bundle.exists()
    assert data["dataset_version"] and data["computed_at"] and data["commit"]
    opt = data["optimize"]
    assert opt["weights"] == {} and opt["plans"]
    for plan in opt["plans"]:
        assert {"entries", "objective", "eval_objective", "label", "time_limited"} <= set(plan)
        assert plan["eval_violations"] == []
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8")) if bundle.is_dir() else None
    if (manifest or {}).get("scenario") == "operating" or data["preset"].endswith("-operating"):
        rows = data["operating"]["rows"]
        assert [r["k"] for r in rows] == [0, 1, 2, 3] and all(r.get("accepted") for r in rows)
