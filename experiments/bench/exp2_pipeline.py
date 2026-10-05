"""실험 2: 파이프라인 비용 (Full-LLM vs Hybrid).

Full-LLM  : 정형 이력 + 리뷰 항목 선택 + 자유서술을 전부 LLM에 넣어 파싱
Hybrid    : 정형·항목 선택은 DB 직적재, 자유서술만 LLM 파싱

절감률은 "목표"가 아니라 "측정 결과"다. 우리 fixture의 정형:비정형 비중이 그
숫자를 결정하므로, 비중을 바꿔가며 민감도 곡선을 함께 낸다 — 단일 절감률만
제시하면 "데이터를 그렇게 만든 것 아니냐"는 반박에 답할 수 없다.

출력 토큰 비율(out_ratio): Task 6이 266건 전량을 실 호출로 재동결하며 측정한
`.omc/llm_checkpoint/seed42_p100_pr20.json`의 parse_usage 합계(gpt-5-nano,
당시 `pricing["parse_model"]`과 동일 모델) — prompt_tokens=74,390, completion_tokens=
429,783 → completion/prompt = 5.7774. gpt-5-nano/mini는 추론 모델이라 응답 이전
내부 추론 토큰이 completion에 과금되므로, 브리프가 가정한 0.2(출력≈입력의 20%)는
실측치보다 약 29배 작다 — 이 fixture(tok_full=87,896)에서 직접 비교하면
0.2 가정 시 full_llm 비용은 $0.0114264, 실측 비율 시 $0.2075196으로 **약
18.2배** 차이 난다(과소추정, 5배가 아니다 — 초기 초안이 자릿수를 잘못
어림잡았던 부분을 코디네이터 리뷰로 정정). 이 상수는 재실행할 때마다
체크포인트를 다시 읽지 않도록 여기 고정값으로 박아 두고, 그 근거를 이
주석과 리포트에 남긴다.

**이 비율이 왜 이 실험의 헤드라인(savings_pct)에 대해 위험한 가정인가
(최종 리뷰 Critical 1 반영, 2026-08-11):**
1. `savings_pct`는 output_per_1m·output_token_assumption과 무관하게
   대수적으로 `1 - tok_hyb/tok_full`(입력 토큰 비율)과 같다 — 출력 토큰이
   두 arm에 **같은 배수**로 곱해지기만 하면(즉 output=input*out_ratio 형태를
   그대로 유지하면) 그 배수가 cost_full/cost_hybrid 비의 분자·분모에서
   상쇄되기 때문이다. `tests/test_exp2.py::test_savings_pct_is_independent_of_out_ratio`가
   이를 회귀 테스트로 고정한다. 즉 out_ratio를 아무리 정교하게 실측해도
   savings_pct 자체는 한 걸음도 더 정확해지지 않는다 — 달러 금액에는
   노출을 더하지만 정보는 더하지 않는다.
2. 반대로 절대 달러 금액(`cost_usd`)은 out_ratio에 크게 좌우되고, 실측
   5.777은 이 실험의 페이로드가 아니라 `parse_reviews_llm`의 기존
   프롬프트(자유서술만 담은 hybrid-shaped 파싱 워크로드)에서 측정된
   값이다. 이를 훨씬 더 큰 입력을 가진 Full-LLM 페이로드(정형 이력+항목
   선택+서술 전부)에 그대로 적용하는 것은 **다른 형태의 페이로드로의
   외삽**이다 — 게다가 5.777이 큰 이유 자체가 추론 모델의 내부 추론
   토큰(reasoning tokens, 프롬프트 길이가 아니라 과제 난이도에 비례)이라서,
   "출력이 입력에 비례한다"는 가정 자체가 이 실험이 다루는 추출 과제
   (고정된 출력 스키마)에는 특히 부적합하다.
3. 그래서 `run()`은 출력 토큰 모델에 대한 민감도(`output_model_sensitivity`)를
   함께 낸다: (i) 이 파일이 실제로 쓰는 가정(출력 ∝ 입력, out_ratio 고정)과
   (ii) 정반대의 극단(출력이 두 arm에서 동일 — 실측 completion 토큰
   429,783을 그대로 사용)을 나란히 계산한다. 두 가정 아래 savings_pct가
   크게 갈리면(실측: 31.4% vs 0.78%), 그 격차 자체가 "출력 토큰 비율
   가정이 헤드라인을 얼마나 떠받치는지"를 보여주는 정직한 지표다.

시간(latency) 축: 이번 실험은 라이브 타이밍 호출을 하지 않는다(오프라인
토큰 카운팅만). `_latency()`는 Task 6에서 실측된 파싱 워크로드 지연을
"미측정 — 근거는 X" 형식으로 인용만 한다 — 정확도 축과 동일한 정직성
기준을 적용.
"""
import json

import tiktoken

from core.config import FIXTURES_DIR, load_pricing
from core.datagen.fixtures_io import load_fixtures
from experiments.bench import harness

_ENC = tiktoken.get_encoding("o200k_base")

# Task 6 체크포인트 실측치(gpt-5-nano, parse_model) 기반. 위 모듈 docstring 참고.
MEASURED_COMPLETION_TOKENS = 429_783
MEASURED_PROMPT_TOKENS = 74_390
MEASURED_OUT_RATIO = MEASURED_COMPLETION_TOKENS / MEASURED_PROMPT_TOKENS  # 5.7774...
# 위 실측이 나온 모델. pricing.json의 parse_model은 2026-10-05부터 gpt-5.5(단가 미확인)로
# 바뀌었지만, 이 실험의 비용 추정은 실측 비율과 같은 모델 단가로 계산해야 맞다.
MEASURED_MODEL = "gpt-5-nano"

# 최종 리뷰 Important 10 반영: .superpowers/, .omc/는 둘 다 .gitignore 대상이라
# 클론한 사람은 이 경로를 열어볼 수 없다 — exp1/exp3는 재실행해 검증할 수 있지만
# exp2의 이 상수 하나는 검증할 방법이 없었다. 대신 커밋되는
# experiments/results/llm_checkpoint_summary.json(토큰 총계·비용만 담은 redacted
# 요약, 프롬프트·리뷰 원문 없음 — experiments/bench/checkpoint_summary.py가
# 생성)을 인용한다.
_LATENCY_SOURCE = "experiments/results/llm_checkpoint_summary.json"


def count_tokens(text: str) -> int:
    """o200k_base로 고정 인코딩한다.

    이전 시그니처는 `model` 인자를 받았지만 실제로는 무시하고 항상 o200k_base를
    썼다(최종 리뷰 Minor 항목) — tiktoken이 "gpt-5-*" 계열 모델명을
    encoding_for_model()로 인식하지 못해 처음부터 고정 인코딩을 쓸 수밖에
    없었고, 그 사실을 시그니처가 숨기고 있었다. 파라미터를 제거해 실제 동작과
    시그니처를 일치시킨다.
    """
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


def _sensitivity(ds, pricing: dict) -> list[dict]:
    """자유서술 길이 비중을 배수로 조절하며 절감률이 어떻게 변하는지 본다."""
    payloads = build_payloads(ds)
    base_full = sum(count_tokens(t) for t in payloads["full_llm"])
    curve = []
    for mult in (0.25, 0.5, 1.0, 2.0, 4.0):
        hyb = sum(count_tokens(json.dumps({
            "지시": "아래 서술의 감성 강도와 근거 문장만 추출하라",
            "좋은점": r.positive.text * int(max(1, mult)) if mult >= 1 else r.positive.text[:max(1, int(len(r.positive.text) * mult))],
            "나쁜점": r.negative.text * int(max(1, mult)) if mult >= 1 else r.negative.text[:max(1, int(len(r.negative.text) * mult))],
        }, ensure_ascii=False)) for r in ds.reviews)
        extra = base_full - sum(count_tokens(t) for t in payloads["hybrid"])
        full = hyb + extra          # 정형 부분 토큰은 그대로 두고 비정형만 변화
        curve.append({"freetext_multiplier": mult, "full_llm_tokens": full,
                      "hybrid_tokens": hyb,
                      "savings_pct": (1 - hyb / full) * 100 if full else 0.0})
    return curve


def _output_model_sensitivity(tok_full: int, tok_hyb: int, out_ratio: float,
                              measured_completion_tokens: int,
                              model: str, pricing: dict) -> list[dict]:
    """최종 리뷰 Critical 1: savings_pct가 기대는 출력 토큰 모델 자체를 두 가지
    극단으로 바꿔가며 계산한다 — 하나는 이 파일이 실제로 쓰는 가정(출력이
    입력에 비례), 다른 하나는 정반대(출력이 두 arm에서 동일). 대수적으로
    (i)의 savings_pct는 out_ratio 값과 무관하게 입력 토큰 비율과 같다(자릿수가
    두 arm에서 상쇄) — 그래서 이 함수는 그 사실 자체를 데이터로 보여준다:
    (i)와 (ii)가 크게 다르면 헤드라인이 output-model 가정에 얼마나 기대고
    있었는지가 드러난다.
    """
    prop_full = int(tok_full * out_ratio)
    prop_hyb = int(tok_hyb * out_ratio)
    cost_prop = {"full_llm": estimate_cost(tok_full, prop_full, model, pricing),
                 "hybrid": estimate_cost(tok_hyb, prop_hyb, model, pricing)}
    savings_prop = (1 - cost_prop["hybrid"] / cost_prop["full_llm"]) * 100 \
        if cost_prop["full_llm"] else 0.0

    cost_const = {"full_llm": estimate_cost(tok_full, measured_completion_tokens, model, pricing),
                  "hybrid": estimate_cost(tok_hyb, measured_completion_tokens, model, pricing)}
    savings_const = (1 - cost_const["hybrid"] / cost_const["full_llm"]) * 100 \
        if cost_const["full_llm"] else 0.0

    return [
        {"model": "output_proportional_to_input",
         "description": "출력 토큰 = 입력 토큰 × out_ratio (이 실험이 실제로 쓰는 가정, "
                        "MEASURED_OUT_RATIO=5.777)",
         "output_tokens": {"full_llm": prop_full, "hybrid": prop_hyb},
         "cost_usd": cost_prop, "savings_pct": savings_prop},
        {"model": "output_constant_across_arms",
         "description": "출력 토큰이 두 arm에서 동일 — 체크포인트 실측 completion "
                        f"토큰 합계({measured_completion_tokens:,})를 두 arm 모두에 사용",
         "output_tokens": {"full_llm": measured_completion_tokens,
                           "hybrid": measured_completion_tokens},
         "cost_usd": cost_const, "savings_pct": savings_const},
    ]


def _latency() -> dict:
    """스펙 §5의 "비용·시간" 중 시간 축. 이번 실험은 라이브 타이밍 호출을 하지
    않으므로(오프라인 토큰 카운팅만 수행) 직접 측정값이 없다. 정확도 축과
    동일한 정직성 기준으로 "미측정 — 근거는 X" 형태로만 인용한다.
    """
    return {
        "measured": False,
        "note": ("이번 실험은 라이브 타이밍 호출을 하지 않았다(비용 절감을 위해 "
                 "오프라인 토큰 카운팅만 수행). 아래는 Task 6에서 실측된 파싱 "
                 "워크로드 지연을 참고치로 인용한 것이며, 이번 실험의 Full-LLM/"
                 "Hybrid 페이로드 형태로 직접 측정된 값이 아니다."),
        "basis": ("Task 6 체크포인트 실행(gpt-5-mini gen + gpt-5-nano parse, "
                  "266건 리뷰 = 532회 API 호출): 리뷰당(gen+parse 2회 합산) "
                  "관측 지연 ~24-28초, foreground 배치 실행 1회당 5.1-8.5분"
                  "(회당 3~18건, 20회 실행). 개별 API 호출 단위로는 ~10-28초로 "
                  "추정(추론 모델 특성상 completion에 내부 추론 토큰이 실려 "
                  "지연이 커짐 — MEASURED_OUT_RATIO가 큰 것과 동일한 원인)."),
        "source": _LATENCY_SOURCE,
        "caveat": ("이 지연은 parse_reviews_llm의 기존 프롬프트(좋은점/나쁜점 "
                  "서술만 전달)로 측정된 것이다. 이번 실험의 Full-LLM 페이로드는 "
                  "정형 이력+항목선택+서술 전부를 담아 입력 토큰이 더 크므로 "
                  "지연이 이보다 클 것으로 추정되나 직접 측정되지 않았다 — "
                  "Full-LLM 페이로드 형태의 실측이 아니라 parse 워크로드 실측을 "
                  "그대로 인용한 하한(lower-bound) 성격의 참고치다."),
    }


def run(sample_calls: int = 10, live: bool = False, client=None) -> dict:
    pricing = load_pricing()
    model = MEASURED_MODEL
    ds, _ = load_fixtures(FIXTURES_DIR)
    payloads = build_payloads(ds)

    tok_full = sum(count_tokens(t) for t in payloads["full_llm"])
    tok_hyb = sum(count_tokens(t) for t in payloads["hybrid"])
    # 출력≈입력의 20%라는 안이한 가정 대신, Task 6에서 266건 전량 실호출로 측정한
    # gpt-5-nano(당시 parse_model) completion/prompt 비율을 쓴다 (모듈 docstring 참고).
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

    # output_model_sensitivity[0](output_proportional_to_input)이 바로 이 실험이 실제로
    # 채택한 가정과 동일한 계산이므로, cost_usd/savings_pct는 그 결과에서 그대로 가져온다
    # -- 같은 공식을 두 곳에 중복해서 적으면 하나만 고치는 실수가 생기기 쉽다.
    oms = _output_model_sensitivity(tok_full, tok_hyb, out_ratio, MEASURED_COMPLETION_TOKENS,
                                    model, pricing)
    proportional = next(s for s in oms if s["model"] == "output_proportional_to_input")
    cost, savings = proportional["cost_usd"], proportional["savings_pct"]

    return {"model": model, "pricing_as_of": pricing["as_of"],
            "token_counts": {"full_llm": tok_full, "hybrid": tok_hyb,
                             "review_count": len(ds.reviews)},
            "cost_usd": cost, "savings_pct": savings,
            "accuracy": accuracy,
            "latency": _latency(),
            "sensitivity": _sensitivity(ds, pricing),
            "output_token_assumption": out_ratio,
            "output_token_assumption_basis":
                "Task 6 체크포인트 실측 gpt-5-nano(당시 parse_model) completion/prompt "
                f"= {MEASURED_COMPLETION_TOKENS:,}/{MEASURED_PROMPT_TOKENS:,} (266건 전량, "
                f"{_LATENCY_SOURCE} — hybrid-shaped parse 워크로드에서 측정, "
                "Full-LLM 페이로드로의 적용은 다른 형태 간 외삽임)",
            "output_model_sensitivity": oms}


def main():
    out = run()
    path = harness.save_result("exp2_pipeline", out)
    oms = {s["model"]: s["savings_pct"] for s in out["output_model_sensitivity"]}
    print(f"saved: {path}  savings={out['savings_pct']:.1f}%  "
          f"full={out['token_counts']['full_llm']:,} hybrid={out['token_counts']['hybrid']:,}")
    print("output-model sensitivity: "
          f"proportional={oms.get('output_proportional_to_input', float('nan')):.2f}%  "
          f"constant={oms.get('output_constant_across_arms', float('nan')):.2f}%")


if __name__ == "__main__":
    main()
