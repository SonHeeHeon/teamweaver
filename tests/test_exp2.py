import json
from experiments.bench import exp2_pipeline as e2

class _FakeCompletions:
    def create(self, **kw):
        out = {"pos_items": ["책임감"], "neg_items": ["꼼꼼함"], "text_polarity": 0.3}
        class Msg: content = json.dumps(out, ensure_ascii=False)
        class Choice: message = Msg()
        class Resp:
            choices = [Choice()]
            usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 20})()
        return Resp()

class FakeClient:
    class chat:
        completions = _FakeCompletions()

def test_count_tokens_monotonic():
    a = e2.count_tokens("짧은 문장", "gpt-5-mini")
    b = e2.count_tokens("짧은 문장 " * 50, "gpt-5-mini")
    assert 0 < a < b

def test_full_llm_payload_is_larger_than_hybrid():
    from core.config import FIXTURES_DIR
    from core.datagen.fixtures_io import load_fixtures
    ds, _ = load_fixtures(FIXTURES_DIR)
    p = e2.build_payloads(ds)
    assert len(p["full_llm"]) == len(p["hybrid"]) > 0
    full = sum(e2.count_tokens(t, "gpt-5-mini") for t in p["full_llm"])
    hyb = sum(e2.count_tokens(t, "gpt-5-mini") for t in p["hybrid"])
    assert full > hyb, "Full-LLM 이 항목 선택까지 토큰으로 태우므로 더 커야 한다"

def test_estimate_cost_matches_pricing_table():
    pricing = {"models": {"m": {"input_per_1m": 2.0, "output_per_1m": 10.0}}}
    # 1,000,000 입력 + 100,000 출력 = 2.0 + 1.0 = 3.0
    assert abs(e2.estimate_cost(1_000_000, 100_000, "m", pricing) - 3.0) < 1e-9

def test_run_offline_produces_all_axes():
    out = e2.run(sample_calls=2, live=False)
    for key in ("token_counts", "cost_usd", "accuracy", "sensitivity"):
        assert key in out
    assert out["sensitivity"], "자유서술 비중별 민감도 곡선이 있어야 한다"
    assert set(out["cost_usd"]) == {"full_llm", "hybrid"}
    assert "savings_pct" in out and 0 < out["savings_pct"] < 100


def test_savings_pct_is_independent_of_out_ratio():
    """out_ratio가 절감률에 영향을 주지 않아야 한다 — 이 불변식이 깨지면
    31.4%라는 수치가 추정 가정에 의존하게 되어 방어력을 잃는다."""
    a = e2.run(live=False)
    original = e2.MEASURED_OUT_RATIO
    try:
        e2.MEASURED_OUT_RATIO = 0.2
        b = e2.run(live=False)
    finally:
        e2.MEASURED_OUT_RATIO = original
    assert abs(a["savings_pct"] - b["savings_pct"]) < 0.01
    assert a["cost_usd"]["full_llm"] != b["cost_usd"]["full_llm"]   # 절대금액은 달라져야 함
