"""실험 2: 파이프라인 비용 (Full-LLM vs Hybrid).

Full-LLM  : 정형 이력 + 리뷰 항목 선택 + 자유서술을 전부 LLM에 넣어 파싱
Hybrid    : 정형·항목 선택은 DB 직적재, 자유서술만 LLM 파싱

절감률은 "목표"가 아니라 "측정 결과"다. 우리 fixture의 정형:비정형 비중이 그
숫자를 결정하므로, 비중을 바꿔가며 민감도 곡선을 함께 낸다 — 단일 절감률만
제시하면 "데이터를 그렇게 만든 것 아니냐"는 반박에 답할 수 없다.

출력 토큰 비율(out_ratio): Task 6이 266건 전량을 실 호출로 재동결하며 측정한
`.omc/llm_checkpoint/seed42_p100_pr20.json`의 parse_usage 합계(gpt-5-nano,
`pricing["parse_model"]`과 동일 모델) — prompt_tokens=74,390, completion_tokens=
429,783 → completion/prompt = 5.7774. gpt-5-nano/mini는 추론 모델이라 응답 이전
내부 추론 토큰이 completion에 과금되므로, 브리프가 가정한 0.2(출력≈입력의 20%)는
실측치보다 약 29배 작다 — 그 가정을 그대로 쓰면 비용을 5배 넘게 과소추정한다.
이 상수는 재실행할 때마다 체크포인트를 다시 읽지 않도록 여기 고정값으로 박아
두고, 그 근거를 이 주석과 리포트에 남긴다.
"""
import json

import tiktoken

from core.config import FIXTURES_DIR, load_pricing
from core.datagen.fixtures_io import load_fixtures
from experiments.bench import harness

_ENC = tiktoken.get_encoding("o200k_base")

# Task 6 체크포인트 실측치(gpt-5-nano, parse_model) 기반. 위 모듈 docstring 참고.
MEASURED_OUT_RATIO = 429_783 / 74_390  # 5.7774...


def count_tokens(text: str, model: str) -> int:
    return len(_ENC.encode(text))


def build_payloads(ds) -> dict:
    """리뷰별로 두 방식의 LLM 입력 페이로드를 만든다."""
    full, hyb = [], []
    people = {p.id: p for p in ds.people}
    for r in ds.reviews:
        reviewee = people[r.reviewee_id]
        skills = ", ".join(f"{k}:{v}" for k, v in reviewee.skills.items())
        full.append(json.dumps({
            "지시": "아래 인력 정보와 리뷰 전체를 파싱해 항목과 감성을 추출하라",
            "등급": reviewee.grade.value, "보유스킬": skills,
            "월단가": reviewee.monthly_rate,
            "좋은점_항목": r.positive.items, "좋은점_서술": r.positive.text,
            "나쁜점_항목": r.negative.items, "나쁜점_서술": r.negative.text,
        }, ensure_ascii=False))
        hyb.append(json.dumps({
            "지시": "아래 서술의 감성 강도와 근거 문장만 추출하라",
            "좋은점": r.positive.text, "나쁜점": r.negative.text,
        }, ensure_ascii=False))
    return {"full_llm": full, "hybrid": hyb}


def estimate_cost(n_input_tokens: int, n_output_tokens: int,
                  model: str, pricing: dict) -> float:
    m = pricing["models"][model]
    return (n_input_tokens / 1_000_000) * m["input_per_1m"] + \
           (n_output_tokens / 1_000_000) * m["output_per_1m"]


def _sensitivity(ds, model: str, pricing: dict) -> list[dict]:
    """자유서술 길이 비중을 배수로 조절하며 절감률이 어떻게 변하는지 본다."""
    payloads = build_payloads(ds)
    base_full = sum(count_tokens(t, model) for t in payloads["full_llm"])
    curve = []
    for mult in (0.25, 0.5, 1.0, 2.0, 4.0):
        hyb = sum(count_tokens(json.dumps({
            "지시": "아래 서술의 감성 강도와 근거 문장만 추출하라",
            "좋은점": r.positive.text * int(max(1, mult)) if mult >= 1 else r.positive.text[:max(1, int(len(r.positive.text) * mult))],
            "나쁜점": r.negative.text * int(max(1, mult)) if mult >= 1 else r.negative.text[:max(1, int(len(r.negative.text) * mult))],
        }, ensure_ascii=False), model) for r in ds.reviews)
        extra = base_full - sum(count_tokens(t, model) for t in payloads["hybrid"])
        full = hyb + extra          # 정형 부분 토큰은 그대로 두고 비정형만 변화
        curve.append({"freetext_multiplier": mult, "full_llm_tokens": full,
                      "hybrid_tokens": hyb,
                      "savings_pct": (1 - hyb / full) * 100 if full else 0.0})
    return curve


def run(sample_calls: int = 10, live: bool = False, client=None) -> dict:
    pricing = load_pricing()
    model = pricing["parse_model"]
    ds, _ = load_fixtures(FIXTURES_DIR)
    payloads = build_payloads(ds)

    tok_full = sum(count_tokens(t, model) for t in payloads["full_llm"])
    tok_hyb = sum(count_tokens(t, model) for t in payloads["hybrid"])
    # 출력≈입력의 20%라는 안이한 가정 대신, Task 6에서 266건 전량 실호출로 측정한
    # gpt-5-nano(parse_model) completion/prompt 비율을 쓴다 (모듈 docstring 참고).
    out_ratio = MEASURED_OUT_RATIO
    accuracy = {"checked": 0, "item_match_rate": None,
                "note": "live=False 이므로 미측정"}

    if live and client is not None:
        from core.datagen.parse_reviews import parse_reviews_llm
        sample = ds.reviews[:sample_calls]
        parsed = parse_reviews_llm(sample, client, model)
        assert len(parsed) == len(sample)
        accuracy = {"checked": len(sample),
                    "item_match_rate": None,
                    "note": "Full-LLM 재추출 항목 대조는 live 실행 결과로 채운다"}

    cost = {"full_llm": estimate_cost(tok_full, int(tok_full * out_ratio), model, pricing),
            "hybrid": estimate_cost(tok_hyb, int(tok_hyb * out_ratio), model, pricing)}
    savings = (1 - cost["hybrid"] / cost["full_llm"]) * 100 if cost["full_llm"] else 0.0

    return {"model": model, "pricing_as_of": pricing["as_of"],
            "token_counts": {"full_llm": tok_full, "hybrid": tok_hyb,
                             "review_count": len(ds.reviews)},
            "cost_usd": cost, "savings_pct": savings,
            "accuracy": accuracy,
            "sensitivity": _sensitivity(ds, model, pricing),
            "output_token_assumption": out_ratio,
            "output_token_assumption_basis":
                "Task 6 체크포인트 실측 gpt-5-nano(parse_model) completion/prompt "
                "= 429,783/74,390 (266건 전량, .omc/llm_checkpoint/seed42_p100_pr20.json)"}


def main():
    out = run()
    path = harness.save_result("exp2_pipeline", out)
    print(f"saved: {path}  savings={out['savings_pct']:.1f}%  "
          f"full={out['token_counts']['full_llm']:,} hybrid={out['token_counts']['hybrid']:,}")


if __name__ == "__main__":
    main()
