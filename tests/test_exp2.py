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
    a = e2.count_tokens("짧은 문장")
    b = e2.count_tokens("짧은 문장 " * 50)
    assert 0 < a < b

def test_full_llm_payload_is_larger_than_hybrid():
    from core.config import FIXTURES_DIR
    from core.datagen.fixtures_io import load_fixtures
    ds, _ = load_fixtures(FIXTURES_DIR)
    p = e2.build_payloads(ds)
    assert len(p["full_llm"]) == len(p["hybrid"]) > 0
    full = sum(e2.count_tokens(t) for t in p["full_llm"])
    hyb = sum(e2.count_tokens(t) for t in p["hybrid"])
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
    """out_ratio가 절감률에 영향을 주지 않는다 — 방어적인 성질이 아니라 정반대다:
    두 arm 모두 output=input*out_ratio 형태를 유지하는 한 그 배수가 cost 비율에서
    상쇄되므로, savings_pct는 대수적으로 입력 토큰 비율(1 - tok_hyb/tok_full)과
    같다. 즉 out_ratio를 아무리 정교하게 실측해도 savings_pct는 더 정확해지지
    않는다 — 달러 금액에는 노출을 더하지만 정보는 더하지 않는다는 뜻이다(최종
    리뷰 Critical 1). 그래서 헤드라인의 방어선은 이 불변식이 아니라
    `output_model_sensitivity`(출력 모델 자체를 바꿔보는 민감도)가 맡는다."""
    a = e2.run(live=False)
    original = e2.MEASURED_OUT_RATIO
    try:
        e2.MEASURED_OUT_RATIO = 0.2
        b = e2.run(live=False)
    finally:
        e2.MEASURED_OUT_RATIO = original
    assert abs(a["savings_pct"] - b["savings_pct"]) < 0.01
    assert a["cost_usd"]["full_llm"] != b["cost_usd"]["full_llm"]   # 절대금액은 달라져야 함


def test_output_model_sensitivity_shows_savings_is_output_model_dependent():
    """savings_pct 자체는 out_ratio에 불변이지만(위 테스트), 이는 출력 토큰
    가정을 아예 다른 모델로 바꿔도 결과가 안 바뀐다는 뜻이 아니다 -- '출력이
    입력에 비례'라는 이 실험의 가정과 '출력이 두 arm에서 동일'이라는 정반대
    가정은 서로 다른 savings_pct를 낸다(최종 리뷰 Critical 1이 요구한 민감도).
    출력이 동일하다는 가정에서는 두 arm의 비용 차이가 입력 토큰 차이(작은 항)만
    남아 절감률이 훨씬 작아져야 한다."""
    out = e2.run(live=False)
    sens = {s["model"]: s for s in out["output_model_sensitivity"]}
    assert set(sens) == {"output_proportional_to_input", "output_constant_across_arms"}
    prop, const = sens["output_proportional_to_input"], sens["output_constant_across_arms"]
    # proportional 쪽은 run()의 headline savings_pct와 일치해야 한다(같은 가정).
    assert abs(prop["savings_pct"] - out["savings_pct"]) < 0.01
    # constant 쪽은 출력 토큰(큰 항)이 상쇄되고 입력 토큰(작은 항) 차이만 남으므로
    # 절감률이 훨씬 작아야 한다.
    assert 0 <= const["savings_pct"] < prop["savings_pct"]
    assert const["output_tokens"]["full_llm"] == const["output_tokens"]["hybrid"]
