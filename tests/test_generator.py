from core.datagen.generator import generate_dataset, capacity_ratio
from core.domain.models import ProjectPhase

def test_deterministic_same_seed():
    a = generate_dataset(50, 10, seed=7)
    b = generate_dataset(50, 10, seed=7)
    assert a.model_dump() == b.model_dump()

def test_counts_and_phase_mix():
    ds = generate_dataset(100, 20, seed=42)
    assert len(ds.people) == 100 and len(ds.projects) == 20
    proposals = [p for p in ds.projects if p.phase == ProjectPhase.PROPOSAL]
    assert 5 <= len(proposals) <= 9          # 제안 ≈ 7

def test_reviews_per_person_2_to_4():
    ds = generate_dataset(100, 20, seed=42)
    from collections import Counter
    c = Counter(r.reviewee_id for r in ds.reviews)
    assert all(2 <= n <= 4 for n in c.values()) and len(c) == 100

def test_reviews_reference_taxonomy_and_have_korean_text():
    from core.config import load_review_items
    items = set(load_review_items())
    ds = generate_dataset(50, 10, seed=1)
    r = ds.reviews[0]
    assert set(r.positive.items) <= items and set(r.negative.items) <= items
    assert len(r.positive.text) > 5 and len(r.negative.text) > 5

def test_capacity_calibrated_feasible():
    ds = generate_dataset(100, 20, seed=42)
    assert capacity_ratio(ds) <= 0.85   # 월별 수요가 공급의 85% 이하로 보정됨

def test_rejects_tiny_people_pool():
    import pytest
    with pytest.raises(ValueError):
        generate_dataset(2, 2, seed=1)

def test_calibration_contract_small_pool():
    # 공급이 극단적으로 부족한 구성: 보정 성공(≤0.85) 또는 명시적 ValueError만 허용
    try:
        ds = generate_dataset(3, 10, seed=1)
    except ValueError:
        return
    assert capacity_ratio(ds) <= 0.85
