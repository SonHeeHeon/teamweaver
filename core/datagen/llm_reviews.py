import json
from core.domain.models import Dataset

# 2026-10-05 튜닝: "항목 단어를 최소 1회 직접 언급" 지시 때문에 gpt-5.5가 "꼼꼼함 있게",
# "창의성 있는" 같은 어색한 문장을 냈다. 의미는 모두 담되 활용형을 허용한다. 동결 fixture는
# 예전 프롬프트로 만든 것이며 다시 만들지 않는다.

_SYSTEM = (
    "너는 SI 회사의 피어리뷰 데이터를 만드는 작가다. 입력 JSON의 positive_items/negative_items에 "
    "일관되는 자연스러운 한국어 리뷰를 작성하라. 각 2~3문장, 구체적 업무 상황 포함. 모든 항목의 의미가 "
    "드러나야 하지만 항목 단어를 억지로 끼워 넣지 말고 자연스러운 활용형으로 써라(예: 꼼꼼함→꼼꼼하게, "
    "창의성→창의적인 제안). JSON {\"positive_text\": str, \"negative_text\": str}만 출력.")

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
        try:
            out = json.loads(resp.choices[0].message.content)
            r.positive.text = out["positive_text"]
            r.negative.text = out["negative_text"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(
                f"리뷰 재작성 실패 (index={i}, reviewer={r.reviewer_id}, reviewee={r.reviewee_id}): {exc}"
            ) from exc
    return ds
