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


def parse_reviews_rule_based(reviews: list[PeerReview]) -> list[ParsedReview]:
    """Parse reviews using rule-based heuristic (deterministic fallback)."""
    out = []
    for r in reviews:
        np_, nn = len(r.positive.items), len(r.negative.items)
        pol = (np_ - nn) / (np_ + nn)
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
