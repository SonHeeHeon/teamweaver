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


@pytest.mark.parametrize("name", ["org-n100", "org-n100-operating", "org-n100-yearend", "org-n100-annual"])
def test_bundled_precomputed_loads_without_skips(name, monkeypatch, tmp_path):
    """Codex 리뷰(2026-10-19): 감점 규칙을 바꾸고 미리 계산을 다시 만들지 않으면 서버 로더가 점수 불일치로 묶음 전체를 조용히 버린다
    (plans=0, operating=None) -- 동봉 판정값(외부 AI 없이)으로 서버와 같은 경로를 밟아 안 A~D·K 행이 모두 적재되는지 본다. 100명 묶음만."""
    for key in ("OPENAI_API_KEY", "ZAI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("TEAMWEAVER_DATA_DIR", str(tmp_path))
    from api.demo_precomputed import load_precomputed
    from api.demos import build_demo
    from api.settings import PlacementSettings, horizon_months_of
    root = DEMO / name
    active = build_demo(root)
    try:
        params = PlacementSettings().to_milp_params(n_people=len(active.graph.people), months=horizon_months_of(active.graph))
        pre = load_precomputed(root, active, params)
        assert pre is not None and pre.skipped == [] and len(pre.plans) == 4, pre and pre.skipped
        if name.endswith(("-operating", "-yearend")):
            assert pre.operating is not None and len(pre.operating["rows"]) == 4
    finally:
        active.retire()
