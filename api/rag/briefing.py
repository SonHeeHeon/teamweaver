"""Graph RAG 문맥 -> LLM structured output XAI 브리핑.

core/datagen/llm_reviews.py의 호출 패턴(response_format json_object)을
그대로 따른다 -- 이 코드베이스는 이미 이 방식으로 OpenAI structured output을
쓰고 있으므로 새 패턴(beta parse 등)을 도입하지 않는다.

K5(근거의 사실성): 근거 색인(api/rag/evidence.py)을 넘기면 LLM은 근거를 [출처 ID]로 가리키고
인용은 citations에 따로 낸다. 서버가 인용을 원문과 글자 그대로 대조하고, 하나라도 맞지 않으면
(없는 출처, 바꿔 쓴 인용, 본문 속 지어낸 따옴표 인용) 브리핑 전체를 버린다 -- 호출부가 규칙 기반
브리핑으로 전환한다(사용자 결정 2026-10-05: 틀린 인용만 지우면 그 인용에 기댄 주장이 남는다).
원문 숨김 모드(실데이터)에서는 문맥에 문장이 없고 인용 자체를 허용하지 않는다."""
import json
import logging
import re

from pydantic import ValidationError

from api.rag.evidence import MIN_QUOTE_CHARS, EvidenceIndex
from api.schemas import BriefingOut

log = logging.getLogger(__name__)

_SYSTEM = (
    "너는 SI 인력 배치 시스템의 설명가능AI(XAI) 브리핑 작성자다. 주어진 그래프 문맥"
    "(스킬·협업이력·리뷰근거)만 근거로, 인력 교체(스왑)에 대한 명분·리스크·대안을"
    "한국어로 작성하라. 문맥에 없는 사실을 지어내지 마라."
    ' JSON {"rationale": str, "risks": list[str], "alternatives": list[str]}만 출력.'
)
_SYSTEM_SOURCED = (
    "너는 SI 인력 배치 시스템의 설명가능AI(XAI) 브리핑 작성자다. 주어진 그래프 문맥"
    "(스킬·협업이력·리뷰근거)만 근거로, 인력 교체(스왑)에 대한 명분·리스크·대안을 한국어로 작성하라."
    " 문맥에 없는 사실을 지어내지 마라. 리뷰 근거는 kind가 quote인 근거만 쓴다."
    " 그 근거를 쓴 문장 끝에 [source_id]를 붙이고, [source_id]를 붙인 출처마다 citations에 그 근거 text를"
    " 글자 하나 바꾸지 말고 넣어라. kind가 quote가 아닌 근거에는 [source_id]를 붙이지 마라."
    " 본문에는 따옴표를 쓰지 마라(인용은 citations에만)."
    ' JSON {"rationale": str, "risks": list[str], "alternatives": list[str],'
    ' "citations": [{"source_id": str, "quote": str}]}만 출력.'
)
_SYSTEM_HIDDEN = (
    "너는 SI 인력 배치 시스템의 설명가능AI(XAI) 브리핑 작성자다. 주어진 그래프 문맥"
    "(스킬·협업이력·리뷰 항목 라벨)만 근거로, 인력 교체(스왑)에 대한 명분·리스크·대안을 한국어로 작성하라."
    " 리뷰 원문은 비공개다. 리뷰 문장을 지어내거나 따옴표로 인용하지 마라. 리뷰 근거를 쓸 때는 [source_id]만 붙여라."
    ' JSON {"rationale": str, "risks": list[str], "alternatives": list[str], "citations": []}만 출력.'
)
# 출처 표시는 괄호 단위가 아니라 본문 어디서든 찾는다([rv:a, rv:b]처럼 묶어 써도 하나씩 걸린다).
_MARKER = re.compile(r"rv:[^\s\],;)」』]+")
# 따옴표 짝: 큰따옴표류는 길이와 무관하게, 작은따옴표류(‘’)는 강조 표기와 겹쳐 5자 이상만 인용으로 본다.
# ASCII 작은따옴표(')는 영어 아포스트로피라 보지 않는다.
_DOUBLE = re.compile(r"[\"“”＂]([^\"“”＂]*)[\"“”＂]|「([^」]*)」|『([^』]*)』")
_SINGLE = re.compile(r"‘([^’]*)’|'([^']*[가-힣][^']*)'")     # ASCII '는 안에 한글이 있을 때만 인용
_QUOTE_CHARS = set('"“”＂「」『』')


class BriefingRejected(ValueError):
    """LLM 브리핑을 버린 이유. code는 로그로 전환 빈도를 사유별로 세기 위한 값이다(사용자 결정 1)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _context_sources(ctx: dict) -> dict[str, dict]:
    return {e["source_id"]: e for person in ctx.values() for e in person.get("evidence", [])
            if isinstance(e, dict) and "source_id" in e}


def _verified_citations(out: dict, ctx: dict, evidence: EvidenceIndex) -> list[dict]:
    """인용을 원문과 대조한다. 통과하면 검증된 근거 목록, 하나라도 어긋나면 ValueError."""
    allowed = _context_sources(ctx)
    citations = out.get("citations", [])
    if not isinstance(citations, list):
        raise BriefingRejected("bad_citation_shape", "citations가 목록이 아니다")
    if citations and not evidence.reveal_text:
        raise BriefingRejected("hidden_citation", "원문 비공개 모드인데 인용을 냈다")
    verified = []
    for c in citations:
        if not isinstance(c, dict) or not isinstance(c.get("source_id"), str) or not isinstance(c.get("quote"), str):
            raise BriefingRejected("bad_citation_shape", f"인용 형식이 틀렸다: {c!r}")
        sid, quote = c["source_id"], c["quote"].strip()
        if sid not in allowed:
            raise BriefingRejected("unknown_source", f"문맥에 없는 출처를 인용했다: {sid}")
        if not evidence.verify_quote(sid, quote):
            raise BriefingRejected("quote_not_verbatim", f"인용이 원문과 다르다: {sid}")
        verified.append({"source_id": sid, "reviewer_id": allowed[sid]["reviewer_id"],
                         "kind": "quote", "text": quote})
    cited = {v["source_id"] for v in verified}
    for text in [out["rationale"], *out["risks"], *out["alternatives"]]:
        for sid in _MARKER.findall(text):
            if sid not in allowed:
                raise BriefingRejected("unknown_marker", f"본문이 문맥에 없는 출처를 가리킨다: {sid}")
            # 공개 모드: 본문의 출처 표시는 검증된 인용이 있는 출처여야 한다(표시와 인용의 짝).
            # 숨김 모드는 인용이 없으므로 문맥의 출처(항목 라벨)를 가리키는 것만 허용한다.
            if evidence.reveal_text and sid not in cited:
                raise BriefingRejected("uncited_marker", f"본문의 출처 표시에 대응하는 검증된 인용이 없다: {sid}")
        _check_inline_quotes(text, allowed, evidence)
    return verified


def _check_inline_quotes(text: str, allowed: dict, evidence: EvidenceIndex) -> None:
    """본문 속 따옴표 인용. 숨김 모드에서는 LLM이 원문을 본 적이 없으므로 따옴표 자체를 거부한다."""
    if not evidence.reveal_text:
        if any(ch in _QUOTE_CHARS for ch in text) or _SINGLE.search(text):
            raise BriefingRejected("hidden_quote", "원문 비공개 모드인데 본문에 따옴표 인용이 있다")
        return
    spans = [next(g for g in m.groups() if g is not None) for m in _DOUBLE.finditer(text)]
    spans += [g for m in _SINGLE.finditer(text) for g in m.groups()
              if g is not None and len(g.strip()) >= MIN_QUOTE_CHARS]
    leftover = _SINGLE.sub("", _DOUBLE.sub("", text))
    if any(ch in _QUOTE_CHARS for ch in leftover):
        raise BriefingRejected("unbalanced_quote", "본문에 짝이 맞지 않는 따옴표가 있다")
    for inline in spans:
        if not any(evidence.verify_quote(sid, inline) for sid in allowed):
            raise BriefingRejected("inline_quote_not_verbatim", f"본문의 따옴표 인용이 원문에 없다: {inline[:40]!r}")


def generate_briefing(client, model: str, ctx: dict, out_id: str, in_id: str,
                      evidence: EvidenceIndex | None = None) -> dict:
    if evidence is None and _context_sources(ctx):
        # 출처가 붙은 근거(원문 인용)를 문맥에 넣고 검증기를 빠뜨리면 인용 검사가 통째로 꺼진다(K5 리뷰).
        raise BriefingRejected("missing_index", "근거 색인 없이 출처 근거 문맥이 들어왔다 -- generate_briefing에도 같은 색인을 넘길 것")
    if evidence is None:
        system = _SYSTEM
    else:
        system = _SYSTEM_SOURCED if evidence.reveal_text else _SYSTEM_HIDDEN
    payload = {"out_person": out_id, "in_person": in_id, "context": ctx}
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    try:
        out = json.loads(resp.choices[0].message.content)
        briefing = BriefingOut(rationale=out["rationale"], risks=out["risks"],
                               alternatives=out["alternatives"]).model_dump()
        briefing["evidence"] = [] if evidence is None else _verified_citations(out, ctx, evidence)
        return briefing
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        log.warning("LLM 브리핑을 버림 code=%s (%s -> %s): %s",
                    getattr(exc, "code", type(exc).__name__), out_id, in_id, exc)
        raise ValueError(f"브리핑 생성 실패 ({out_id} -> {in_id}): {exc}") from exc
