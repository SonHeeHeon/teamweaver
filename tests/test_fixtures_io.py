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


def test_meta_omits_seed_and_models_when_not_passed(tmp_path: Path):
    """기존 호출부(review_mode만 넘기는 경우)는 계속 동작해야 한다 -- seed/모델명은
    선택 인자다."""
    ds = generate_dataset(30, 6, seed=3)
    save_fixtures(ds, None, tmp_path, review_mode="template")
    meta = json.loads((tmp_path / "meta.json").read_text("utf-8"))
    assert "seed" not in meta and "gen_model" not in meta and "parse_model" not in meta


def test_meta_records_seed_and_models_when_passed(tmp_path: Path):
    """최종 리뷰 Important 13: seed·gen_model·parse_model이 meta.json에 기록돼야
    report.py가 '세 실험 공통 시드'를 손타이핑하지 않고 이 커밋된 파일에서 확인할
    수 있다."""
    ds = generate_dataset(30, 6, seed=3)
    save_fixtures(ds, None, tmp_path, review_mode="llm", seed=3,
                  gen_model="gpt-5-mini", parse_model="gpt-5-nano")
    meta = json.loads((tmp_path / "meta.json").read_text("utf-8"))
    assert meta["seed"] == 3
    assert meta["gen_model"] == "gpt-5-mini"
    assert meta["parse_model"] == "gpt-5-nano"
