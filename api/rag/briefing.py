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

from api.rag.evidence import MIN_QUOTE_CHARS, EvidenceIndex, normalize
from core.config import load_pricing
from api.schemas import BriefingOut

log = logging.getLogger(__name__)

# 공통 지시(2026-10-05 튜닝): 실측에서 gpt-6-luna가 위험 4~5개·대안 3~4개의 긴 설명을 내고, 문맥에
# 프로젝트 요구 기술·점수 변화가 없어 매번 "정보가 부족해 단정하기 어렵다"로 끝났다. 길이 상한과
# 결론 우선, 그리고 project·score_change를 받으면 그것으로 판단하라는 지시를 둔다.
_BASE = (
    "너는 SI 인력 배치 담당자를 돕는 설명가능AI(XAI) 브리핑 작성자다. 한국어로 쓴다."
    " 입력: out_person(빠질 사람), in_person(들어올 사람), context(사람별 skills=기술:레벨 1~5,"
    " coworks=협업 상대:함께한 개월, evidence=리뷰 근거). 있으면 context.project(교체 대상 프로젝트의"
    " 요구 기술·최소 레벨·인원)와 score_change(교체 전후 배치 점수 변화, total이 양수면 개선)도 온다."
    " 문맥에 있는 사실만 쓰고 지어내지 마라."
    " project가 있으면 두 사람을 그 요구 기술 기준으로 비교해 결론을 내라(정보 부족이라고 얼버무리지 마라)."
    " score_change가 있으면 그 방향과 어긋나는 결론을 쓰지 말고 total 값을 한 번 언급하라."
    " score_change.new_violations가 있으면(이 교체로 가용률·예산·정원 등 제약 위반이 새로 생김) total과 상관없이"
    " 교체를 권고하지 말고 결론을 '보류'로 하며, 그 위반을 risks의 첫 항목으로 써라."
    " feasible이 false인데 new_violations가 없으면 교체 전부터 있던 위반이니, 그 사실을 risks에 적고 권고 여부는 점수로 판단하라."
    " 형식: rationale은 결론(교체 권고/조건부/보류)을 첫 문장에 두고 2~3문장, risks는 최대 3개,"
    " alternatives는 최대 2개, 각 항목은 한 문장."
)
_SYSTEM = (
    _BASE +
    ' JSON {"rationale": str, "risks": list[str], "alternatives": list[str]}만 출력.'
)
_SYSTEM_SOURCED = (
    _BASE +
    " 리뷰 근거는 kind가 quote인 근거만 쓴다."
    " 그 근거를 쓴 문장 끝에 [source_id]를 붙이고, [source_id]를 붙인 출처마다 citations에 그 근거 text를"
    " 글자 하나 바꾸지 말고 넣어라. kind가 quote가 아닌 근거에는 [source_id]를 붙이지 마라."
    " 본문에는 따옴표를 쓰지 마라(인용은 citations에만)."
    ' JSON {"rationale": str, "risks": list[str], "alternatives": list[str],'
    ' "citations": [{"source_id": str, "quote": str}]}만 출력.'
)
_SYSTEM_HIDDEN = (
    _BASE +
    " 리뷰 원문은 비공개이고 evidence에는 리뷰 항목 라벨(예: 좋은 점: 소통·협업)만 있다."
    " 리뷰 문장을 지어내지 마라. 두 사람의 라벨 중 교체 판단과 관련 있는 것(이 프로젝트에 필요한 역량과 닿는"
    " 좋은 점·아쉬운 점)이 있으면 1~2개를 근거나 위험으로 들고 그 문장 끝에 [source_id]만 붙여라"
    " -- 실데이터 시연에서 근거가 비지 않게(2026-10-06 실측: 지시가 선택이라 3회 모두 라벨을 안 썼다)."
    " citations는 항상 빈 배열 []로 둔다. 본문에 따옴표를 쓰지 마라."
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
    if not evidence.reveal_text:
        # 숨김 모드: 인용은 받지 않는다. 다만 그 출처의 항목 라벨을 옮겨 적은 것은 원문 유출이 아니므로
        # (라벨은 공개 허용, 사용자 결정 2026-10-05) 무시한다. 실측에서 가끔 이렇게 채워 설명이 버려졌다.
        for c in citations:
            if not (isinstance(c, dict) and isinstance(c.get("quote"), str)
                    and _is_label_text(c["quote"], allowed.get(c.get("source_id")))):
                raise BriefingRejected("hidden_citation", "원문 비공개 모드인데 인용을 냈다")
        citations = []
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
            # 숨김 모드: 본문이 가리킨 출처의 항목 라벨을 근거로 싣는다 -- 화면·PDF가 "원문 비공개 + 라벨"을 보여 주게
            # (K5 설계, 규칙 기반 설명과 같은 모양). 2026-10-06 실데이터 리허설: 본문에 [rv:…]가 있는데 근거가 0건이었다.
            if not evidence.reveal_text and sid not in cited and allowed[sid].get("kind") == "label":
                row = allowed[sid]
                verified.append({"source_id": sid, "reviewer_id": row["reviewer_id"], "kind": "label",
                                 "text": row["text"]})
                cited.add(sid)
        _check_inline_quotes(text, allowed, evidence)
    return verified


def _label_items(row: dict | None) -> set[str]:
    """label 근거 text("좋은 점: 소통·협업")의 항목들."""
    if not row or row.get("kind") != "label" or ": " not in row.get("text", ""):
        return set()
    return {normalize(x) for x in row["text"].split(": ", 1)[1].split("·") if x.strip()}


def _is_label_text(quote: str, row: dict | None) -> bool:
    q = normalize(quote)
    return bool(row) and row.get("kind") == "label" and bool(q) and (
        q == normalize(row["text"]) or q in _label_items(row))


def _check_inline_quotes(text: str, allowed: dict, evidence: EvidenceIndex) -> None:
    """본문 속 따옴표 인용. 숨김 모드에서는 LLM이 원문을 본 적이 없으므로, 문맥의 항목 라벨을 감싼
    따옴표(공개 허용)만 두고 나머지 따옴표는 거부한다."""
    if not evidence.reveal_text:
        # 항목 하나("소통")든 라벨 전체("좋은 점: 소통·협업")든 공개 허용된 라벨 문자열이면 따옴표를 허용한다.
        labels = set().union(*(_label_items(r) for r in allowed.values())) if allowed else set()
        labels |= {normalize(r["text"]) for r in allowed.values() if r.get("kind") == "label"}
        spans = [next(g for g in m.groups() if g is not None) for m in _DOUBLE.finditer(text)]
        spans += [g for m in _SINGLE.finditer(text) for g in m.groups() if g is not None]
        leftover = _SINGLE.sub("", _DOUBLE.sub("", text))
        if any(ch in _QUOTE_CHARS for ch in leftover) or any(normalize(sp) not in labels for sp in spans):
            raise BriefingRejected("hidden_quote", "원문 비공개 모드인데 본문에 라벨이 아닌 따옴표 인용이 있다")
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


def _check_infeasible_conclusion(out: dict, score_change: dict | None) -> None:
    """A swap that breaks a rule must not be recommended, whatever the score says (claude-b request,
    2026-10-05). The prompt asks for it; this guard makes it hold even when the model ignores the prompt."""
    # only violations this swap creates force a hold; violations that were already there before the swap
    # (feasible False with no new ones) must not block a swap that may even reduce them (review SHOULD)
    if not score_change or not score_change.get("new_violations"):
        return
    first = re.split(r"(?<=[.!?。])\s", out["rationale"].strip(), maxsplit=1)[0]
    if "보류" not in first:
        raise BriefingRejected("recommends_infeasible",
                               "교체 후 제약 위반이 있는데 첫 문장 결론이 '보류'가 아니다")


def generate_briefing(client, model: str, ctx: dict, out_id: str, in_id: str,
                      evidence: EvidenceIndex | None = None, score_change: dict | None = None,
                      reasoning_effort: str | None = None) -> dict:
    """score_change: 교체 전후 배치 점수 변화(예: {"total": .., "skill": .., ...}). reasoning_effort를
    주지 않으면 fixtures/pricing.json의 models[model].reasoning_effort를 쓴다(실측: low가 기본보다 ~5초 빠름)."""
    if evidence is None and _context_sources(ctx):
        # 출처가 붙은 근거(원문 인용)를 문맥에 넣고 검증기를 빠뜨리면 인용 검사가 통째로 꺼진다(K5 리뷰).
        raise BriefingRejected("missing_index", "근거 색인 없이 출처 근거 문맥이 들어왔다 -- generate_briefing에도 같은 색인을 넘길 것")
    if evidence is None:
        system = _SYSTEM
    else:
        system = _SYSTEM_SOURCED if evidence.reveal_text else _SYSTEM_HIDDEN
    payload = {"out_person": out_id, "in_person": in_id, "context": ctx}
    if score_change is not None:
        payload["score_change"] = score_change
    if reasoning_effort is None:
        # 모델별 설정(pricing.json models[모델].reasoning_effort). 모델을 바꿔도 지원 안 하는 값이 남지 않게.
        reasoning_effort = load_pricing().get("models", {}).get(model, {}).get("reasoning_effort")
    effort = reasoning_effort
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        **({"reasoning_effort": effort} if effort else {}),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    try:
        out = json.loads(resp.choices[0].message.content)
        briefing = BriefingOut(rationale=out["rationale"], risks=out["risks"],
                               alternatives=out["alternatives"]).model_dump()
        _check_infeasible_conclusion(briefing, score_change)
        briefing["evidence"] = [] if evidence is None else _verified_citations(out, ctx, evidence)
        return briefing
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        log.warning("LLM 브리핑을 버림 code=%s (%s -> %s): %s",
                    getattr(exc, "code", type(exc).__name__), out_id, in_id, exc)
        raise ValueError(f"브리핑 생성 실패 ({out_id} -> {in_id}): {exc}") from exc
