import json
from core.domain.models import ParsedReview, PeerReview

_SYSTEM = (
    "피어리뷰 좋은점/나쁜점 서술을 읽고 JSON으로만 답하라: "
    "{\"text_polarity\": -1.0~1.0 (서술 전체의 감성 강도), "
    "\"evidence\": [원문에서 그대로 인용한 핵심 문장 1~2개]}")


def parse_reviews_llm(reviews: list[PeerReview], client, model: str) -> list[ParsedReview]:
    """Parse reviews using LLM with structured output."""
    out = []
    for r in reviews:
        resp = client.chat.completions.create(
            model=model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": json.dumps(
                          {"좋은점": r.positive.text, "나쁜점": r.negative.text},
                          ensure_ascii=False)}])
        try:
            d = json.loads(resp.choices[0].message.content)
            ev = d.get("evidence", [])
            if not isinstance(ev, list):
                raise ValueError("evidence must be a list")
            out.append(ParsedReview(reviewer_id=r.reviewer_id, reviewee_id=r.reviewee_id,
                                    text_polarity=max(-1.0, min(1.0, float(d["text_polarity"]))),
                                    evidence=list(ev)[:2]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"리뷰 파싱 실패 (reviewer={r.reviewer_id}, reviewee={r.reviewee_id}): {exc}"
            ) from exc
    return out


# Small, deterministic Korean sentiment-cue lexicon for the rule-based fallback.
# Weighted word -> intensity. `_template_text` (core/datagen/generator.py) always
# closes a positive section with "...측면이 뛰어나 함께 일하기 좋았습니다." and a
# negative section with "...측면이 아쉬워 협업에 어려움이 있었습니다." -- these are
# the cue words scored. Kept intentionally small; this is a stand-in for the LLM
# path (parse_reviews_llm), not an attempt to reproduce it.
_POSITIVE_CUES = {"뛰어나": 1.2, "좋았습니다": 1.0, "훌륭": 1.0, "우수": 1.0}
_NEGATIVE_CUES = {"아쉬워": 1.0, "아쉬웠": 1.0, "어려움": 0.8, "부족": 1.0, "미흡": 1.0}


def _text_polarity(text: str) -> float:
    """Score positive/negative cue-word occurrences in `text` and normalize to
    [-1, 1]. Derived purely from the text's own content (weighted substring
    counts), never from item counts -- so it cannot collapse into the same
    formula as `_item_score` (core/graph/memory_graph.py) the way the old
    `(n_pos - n_neg) / (n_pos + n_neg)` implementation did.
    """
    pos = sum(weight * text.count(word) for word, weight in _POSITIVE_CUES.items())
    neg = sum(weight * text.count(word) for word, weight in _NEGATIVE_CUES.items())
    total = pos + neg
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / total))


def parse_reviews_rule_based(reviews: list[PeerReview]) -> list[ParsedReview]:
    """Parse reviews using rule-based heuristic (deterministic fallback).

    text_polarity comes from `_text_polarity` over the review's own positive/
    negative narrative text (a small Korean sentiment-cue lexicon), not from
    len(items) -- see `_text_polarity` docstring for why.
    """
    out = []
    for r in reviews:
        pol = _text_polarity(r.positive.text + " " + r.negative.text)
        ev = []
        # Extract first sentence from positive text
        pos_sent = r.positive.text.split(".")[0].strip()
        if pos_sent:
            ev.append(pos_sent + ".")
        # Extract first sentence from negative text
        neg_sent = r.negative.text.split(".")[0].strip()
        if neg_sent:
            ev.append(neg_sent + ".")
        # Keep within 2-item convention
        ev = ev[:2]
        out.append(ParsedReview(reviewer_id=r.reviewer_id, reviewee_id=r.reviewee_id,
                                text_polarity=pol, evidence=ev))
    return out
