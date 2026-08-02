from core.config import load_review_items, load_pricing

def test_review_items_taxonomy():
    items = load_review_items()
    assert len(items) == 20 and "책임감" in items and len(set(items)) == 20

def test_pricing_has_models_and_date():
    p = load_pricing()
    assert "as_of" in p
    for m in (p["gen_model"], p["parse_model"]):
        assert m in p["models"]
        assert {"input_per_1m", "output_per_1m"} <= p["models"][m].keys()
