from pathlib import Path
from core.datagen.generator import generate_dataset
from core.datagen.fixtures_io import save_fixtures, load_fixtures

def test_roundtrip(tmp_path: Path):
    ds = generate_dataset(30, 6, seed=3)
    save_fixtures(ds, None, tmp_path)
    loaded, parsed = load_fixtures(tmp_path)
    assert loaded.model_dump() == ds.model_dump() and parsed == []
    assert (tmp_path / "meta.json").exists()
