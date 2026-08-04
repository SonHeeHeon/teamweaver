import os
import core.config as config
from core.config import load_review_items, load_pricing, load_env

def test_review_items_taxonomy():
    items = load_review_items()
    assert len(items) == 20 and "책임감" in items and len(set(items)) == 20

def test_pricing_has_models_and_date():
    p = load_pricing()
    assert "as_of" in p
    for m in (p["gen_model"], p["parse_model"]):
        assert m in p["models"]
        assert {"input_per_1m", "output_per_1m"} <= p["models"][m].keys()

def test_load_env_strips_quotes_and_reads_utf8(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'OPENAI_API_KEY="sk-quoted"\n'
        "NEO4J_AUTH='neo4j/teamweaver'\n"
        "PLAIN_VALUE=no-quotes\n"
        "KOREAN_VALUE=한글값\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    for k in ("OPENAI_API_KEY", "NEO4J_AUTH", "PLAIN_VALUE", "KOREAN_VALUE"):
        monkeypatch.delenv(k, raising=False)
    load_env()
    assert os.environ["OPENAI_API_KEY"] == "sk-quoted"
    assert os.environ["NEO4J_AUTH"] == "neo4j/teamweaver"
    assert os.environ["PLAIN_VALUE"] == "no-quotes"
    assert os.environ["KOREAN_VALUE"] == "한글값"
