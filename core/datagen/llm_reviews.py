import json
from core.domain.models import Dataset

_SYSTEM = (
    "너는 SI 회사의 피어리뷰 데이터를 만드는 작가다. 입력 JSON의 positive_items/negative_items에 "
    "일관되는 자연스러운 한국어 리뷰를 작성하라. 각 2~3문장, 구체적 업무 상황 포함, 항목 단어를 "
    "최소 1회 직접 언급. JSON {\"positive_text\": str, \"negative_text\": str}만 출력.")

def rewrite_reviews_with_llm(ds: Dataset, client, model: str, seed: int) -> Dataset:
    for i, r in enumerate(ds.reviews):
        payload = {"reviewer": r.reviewer_id, "reviewee": r.reviewee_id,
                   "positive_items": r.positive.items, "negative_items": r.negative.items,
                   "variation": (seed + i) % 97}
        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
        out = json.loads(resp.choices[0].message.content)
        r.positive.text = out["positive_text"]
        r.negative.text = out["negative_text"]
    return ds
