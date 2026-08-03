import json
from pathlib import Path
from core.datagen.generator import generate_dataset
from core.datagen.fixtures_io import save_fixtures, load_fixtures

def test_roundtrip(tmp_path: Path):
    ds = generate_dataset(30, 6, seed=3)
    save_fixtures(ds, None, tmp_path)
    loaded, parsed = load_fixtures(tmp_path)
    assert loaded.model_dump() == ds.model_dump() and parsed == []
    assert (tmp_path / "meta.json").exists()


def test_meta_records_review_mode_default(tmp_path: Path):
    """review_mode defaults to 'template' when the caller doesn't specify it
    (machine-readable provenance -- see fixtures_io.py::save_fixtures)."""
    ds = generate_dataset(30, 6, seed=3)
    save_fixtures(ds, None, tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text("utf-8"))
    assert meta["review_mode"] == "template"


def test_meta_records_review_mode_llm_when_passed(tmp_path: Path):
    """review_mode is threaded through explicitly (not inferred) so provenance
    can't silently drift from what was actually run -- e.g. scripts/generate_fixtures.py
    passes args.review_mode straight through."""
    ds = generate_dataset(30, 6, seed=3)
    save_fixtures(ds, None, tmp_path, review_mode="llm")
    meta = json.loads((tmp_path / "meta.json").read_text("utf-8"))
    assert meta["review_mode"] == "llm"
