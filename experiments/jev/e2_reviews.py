"""E2. Jev가 모델의 재료를 대신할 수 있나 -- 동료 리뷰 글 → 협업 평가 극성.

현행 모델에서 쌍 리뷰 점수 = 0.5 × 항목 점수 + 0.5 × 글 극성(text_polarity)이고, 글 극성은 LLM
파서가 글을 읽어 매긴다(core/datagen/parse_reviews.py). 이 "글을 읽고 판정"을 Jev(Score)로 바꿔 본다.

정답: 동결 fixture의 리뷰 266건은 좋은 점·아쉬운 점 **항목 목록**을 먼저 정하고 LLM이 그것으로 한국어
산문을 쓴 것이다. 그래서 (좋은 점 수 − 아쉬운 점 수) / 전체 항목 수 = 글이 뜻해야 하는 극성이다.
판정기는 산문만 본다(항목 목록은 주지 않는다). 이 정답은 "글을 만든 의도"이지 현실의 협업 성과가 아니다.

비교:
- jev: 산문 → Score 5단계(매우 부정 … 매우 긍정, 지시문은 LLM 파서와 같은 "서술 전체의 감성 강도") → [-1, 1]
- llm_fixture: fixture의 parsed_reviews.json(생성 당시 gpt-5-nano 파서, 기록된 값)
- llm_luna: 현행 파서 모델(gpt-6-luna)로 같은 산문을 다시 판정(OPENAI_API_KEY가 있을 때, 결과는 기록 파일)
지표: 정답과의 피어슨·스피어만 상관, 평균 절대 오차, 부호(긍/부/중립) 일치율, 그리고 그 극성으로 만든
협업 행렬 C가 정답 극성으로 만든 C와 얼마나 같은지(쌍 리뷰 점수 상관 -- 쌍 점수의 절반은 항목 점수라
어느 방법이든 높게 나온다. 방법끼리 비교용이지 절대값은 부풀려진 값이다)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.domain.models import ParsedReview
from core.graph.memory_graph import MemoryGraph
from experiments.jev.client import JevClient, score

# 지시문은 현행 LLM 파서(core/datagen/parse_reviews.py: "서술 전체의 감성 강도")와 같은 기준으로 맞춘다.
# 정답 정의(항목 수의 균형)를 설명하지 않는다 -- 그러면 지시문 차이와 모델 차이가 섞인다(리뷰 MUST).
INSTRUCTION = "피어리뷰 좋은점/나쁜점 서술 전체의 감성 강도"
LEVELS = [
    "매우 부정적",
    "다소 부정적",
    "중립",
    "다소 긍정적",
    "매우 긍정적",
]


def truth_polarity(review) -> float:
    np_, nn = len(review.positive.items), len(review.negative.items)
    return (np_ - nn) / (np_ + nn)


def _state(review) -> str:
    # LLM 파서와 같은 표기(좋은점/나쁜점)로 같은 글을 준다.
    return f"피어리뷰 -- 좋은점: {review.positive.text}\n나쁜점: {review.negative.text}"


def judge_with_jev(client: JevClient, reviews) -> tuple[list[float], list[dict]]:
    out, log = [], []
    for r in reviews:
        ans = client.ask(_state(r), {"polarity": score(INSTRUCTION, LEVELS)})
        a = ans.answers["polarity"]
        s = float(a["score"])
        # 문서 예시상 score는 등급 인덱스(0..4, 소수 가능)다. 척도가 다르면(0..1, 1..5 등) clamp로 조용히
        # 틀리지 않게 멈춘다(리뷰 MUST). 원시 값은 기록에 남는다.
        if not 0.0 <= s <= len(LEVELS) - 1:
            raise ValueError(f"Jev score {s}가 예상 척도 0..{len(LEVELS) - 1} 밖이다 -- 척도 가정을 다시 확인할 것")
        probs = a.get("probabilities")
        expected = None
        if isinstance(probs, dict) and len(probs) == len(LEVELS):
            expected = sum(i * float(probs.get(str(i), probs.get(LEVELS[i], 0.0))) for i in range(len(LEVELS)))
            # 확률이 등급별로 오면 그 기댓값(Σ i·p_i)과 score가 같은 척도인지 본다(0..1 척도면 크게 어긋난다).
            if abs(expected - s) > 1.0:
                raise ValueError(f"Jev score {s}와 확률 기댓값 {expected:.2f}의 척도가 다르다 -- 척도 가정을 확인할 것")
        # 판정값은 확률 기댓값을 쓴다. 실측(jev-1.13.0)에서 score도 사실상 기댓값(266건 차이 최대 0.02)이라 결과는
        # 거의 같고, 소수점 정밀도를 확률에서 직접 얻으려는 것이다. 확률이 없으면 score를 쓴다.
        level = expected if expected is not None else s
        out.append(level / (len(LEVELS) - 1) * 2 - 1)
        log.append({"latency_s": ans.latency_s, "input_tokens": ans.input_tokens, "replayed": ans.replayed,
                    "raw_score": s, "probabilities": a.get("probabilities")})
    if len(log) >= 20 and max(x["raw_score"] for x in log) <= 1.0:
        # 266건이 전부 0..1에 있으면 0..4 척도라는 가정이 틀렸을 가능성이 크다(리뷰 2차 S1).
        raise ValueError("Jev score가 모두 0..1 안이다 -- 0..4 등급 척도 가정을 다시 확인할 것")
    return out, log


def judge_with_luna(reviews, cache: Path) -> tuple[list[float] | None, dict]:
    """현행 LLM 파서(gpt-6-luna)로 같은 글을 다시 판정. 결과·시간을 기록 파일에 남긴다."""
    if cache.exists():
        rec = json.loads(cache.read_text("utf-8"))
        return rec["polarity"], {**rec["meta"], "replayed": True}
    import os
    if not os.environ.get("OPENAI_API_KEY"):
        return None, {"skipped": "OPENAI_API_KEY 없음"}
    from openai import OpenAI
    from core.config import load_pricing
    from core.datagen.parse_reviews import parse_reviews_llm
    model = load_pricing()["parse_model"]
    client = OpenAI()
    t = time.monotonic()
    pol, lat = [], []
    for r in reviews:                                  # 한 건씩 재며 부른다(지연 분포를 남기려고)
        s = time.monotonic()
        pol.append(parse_reviews_llm([r], client, model)[0].text_polarity)
        lat.append(time.monotonic() - s)
    meta = {"model": model, "seconds_total": time.monotonic() - t, "latency_s": lat}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"polarity": pol, "meta": meta}, ensure_ascii=False), "utf-8")
    return pol, {**meta, "replayed": False}


def _metrics(pred: list[float], truth: list[float]) -> dict:
    p, t = np.asarray(pred), np.asarray(truth)
    rank = lambda x: np.argsort(np.argsort(x, kind="stable"), kind="stable")

    def sign(x):
        return np.where(x > 0.1, 1, np.where(x < -0.1, -1, 0))
    return {"pearson": float(np.corrcoef(p, t)[0, 1]), "spearman": float(np.corrcoef(rank(p), rank(t))[0, 1]),
            "mae": float(np.mean(np.abs(p - t))), "sign_agreement": float(np.mean(sign(p) == sign(t))),
            "n": int(len(p))}


def _bootstrap_delta(a: list[float], b: list[float], truth: list[float], n: int = 2000, seed: int = 0) -> list[float]:
    """같은 리뷰를 다시 뽑는 쌍 부트스트랩으로 Δ피어슨(a − b)의 95% 구간."""
    rng = np.random.default_rng(seed)
    A, B, T = np.asarray(a), np.asarray(b), np.asarray(truth)
    d = []
    for _ in range(n):
        idx = rng.integers(0, len(T), len(T))
        d.append(np.corrcoef(A[idx], T[idx])[0, 1] - np.corrcoef(B[idx], T[idx])[0, 1])
    return [float(np.nanpercentile(d, 2.5)), float(np.nanpercentile(d, 97.5))]


def _pair_scores(ds, polarity: list[float]) -> dict:
    parsed = [ParsedReview(reviewer_id=r.reviewer_id, reviewee_id=r.reviewee_id, text_polarity=v)
              for r, v in zip(ds.reviews, polarity)]
    return MemoryGraph.build(ds, parsed).pair_review_score


def _pair_agreement(ds, pred: list[float], truth: list[float]) -> float:
    a, b = _pair_scores(ds, pred), _pair_scores(ds, truth)
    keys = sorted(set(a) & set(b))
    return float(np.corrcoef([a[k] for k in keys], [b[k] for k in keys])[0, 1])


def run(client: JevClient | None, luna_cache: Path) -> dict:
    ds, parsed = load_fixtures(FIXTURES_DIR)
    truth = [truth_polarity(r) for r in ds.reviews]
    out = {"n_reviews": len(ds.reviews), "methods": {}}
    fixture_pol = [p.text_polarity for p in parsed]
    out["methods"]["llm_fixture"] = {**_metrics(fixture_pol, truth), "model": "gpt-5-nano(기록값)",
                                     "pair_score_corr": _pair_agreement(ds, fixture_pol, truth)}
    luna, meta = judge_with_luna(ds.reviews, luna_cache)
    if luna is not None:
        out["methods"]["llm_luna"] = {**_metrics(luna, truth), "pair_score_corr": _pair_agreement(ds, luna, truth),
                                      "model": meta.get("model"),
                                      "mean_latency_s": float(np.mean(meta["latency_s"])) if meta.get("latency_s") else None}
    else:
        out["methods"]["llm_luna"] = meta
    if client is not None:
        pol, log = judge_with_jev(client, ds.reviews)
        out["methods"]["jev"] = {**_metrics(pol, truth), "pair_score_corr": _pair_agreement(ds, pol, truth),
                                 "mean_latency_s": float(np.mean([x["latency_s"] for x in log])),
                                 "input_tokens": sum(x["input_tokens"] for x in log), "calls": len(log)}
        out["jev_predictions"] = pol
        out["jev_raw"] = [{"score": x["raw_score"], "probabilities": x["probabilities"]} for x in log]
        if luna is not None:
            out["delta_pearson_jev_minus_luna_ci95"] = _bootstrap_delta(pol, luna, truth)
    out["truth"] = truth
    return out
