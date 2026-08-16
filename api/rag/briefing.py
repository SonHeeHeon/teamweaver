"""Graph RAG 문맥 -> LLM structured output XAI 브리핑.

core/datagen/llm_reviews.py의 호출 패턴(response_format json_object)을
그대로 따른다 -- 이 코드베이스는 이미 이 방식으로 OpenAI structured output을
쓰고 있으므로 새 패턴(beta parse 등)을 도입하지 않는다."""
import json

_SYSTEM = (
    "너는 SI 인력 배치 시스템의 설명가능AI(XAI) 브리핑 작성자다. 주어진 그래프 문맥"
    "(스킬·협업이력·리뷰근거)만 근거로, 인력 교체(스왑)에 대한 명분·리스크·대안을"
    "한국어로 작성하라. 문맥에 없는 사실을 지어내지 마라."
    ' JSON {"rationale": str, "risks": list[str], "alternatives": list[str]}만 출력.'
)


def generate_briefing(client, model: str, ctx: dict, out_id: str, in_id: str) -> dict:
    payload = {"out_person": out_id, "in_person": in_id, "context": ctx}
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    try:
        out = json.loads(resp.choices[0].message.content)
        return {"rationale": out["rationale"], "risks": out["risks"],
                "alternatives": out["alternatives"]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"브리핑 생성 실패 ({out_id} -> {in_id}): {exc}") from exc
