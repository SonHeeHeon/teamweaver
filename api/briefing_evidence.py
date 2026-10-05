"""브리핑 근거(K5)를 응답 계약에 맞추고, PDF로 돌아온 근거를 서버 색인으로 다시 확인한다.

- clamp_briefing: 근거 색인·LLM 인용은 응답 스키마(EvidenceOut)의 길이 상한을 모른다. 실데이터의
  긴 리뷰 항목 하나가 /api/whatif 전체를 500으로 만들지 않게(K5 연결 자체 리뷰 M1), 응답에 싣기
  전에 서버가 잘라낸다. 앞부분만 남기므로 원문 인용은 잘려도 여전히 원문의 글자 그대로다.
- verify_report_evidence: /api/report는 화면이 보낸 브리핑을 그대로 받는다. 검사가 없으면 아무
  문장이나 "직접 인용" 배지로 PDF에 찍힐 수 있다(자체 리뷰 S1). 출처가 이 데이터셋에 있고,
  인용은 원문과 글자 그대로이며, 요약·라벨은 색인이 만든 문장과 같아야 받는다."""
from fastapi import HTTPException

from api.rag.evidence import EvidenceIndex
from api.schemas import MAX_EVIDENCE, MAX_EVIDENCE_ID_CHARS, MAX_EVIDENCE_TEXT_CHARS, BriefingOut


def _clamp_text(text: str) -> str:
    return text[:MAX_EVIDENCE_TEXT_CHARS]


def clamp_briefing(briefing: dict) -> dict:
    kept = [{**e, "text": _clamp_text(e["text"])} for e in briefing.get("evidence", [])
            if len(e["source_id"]) <= MAX_EVIDENCE_ID_CHARS
            and len(e["reviewer_id"]) <= MAX_EVIDENCE_ID_CHARS]
    return {**briefing, "evidence": kept[:MAX_EVIDENCE]}


def verify_report_evidence(briefing: BriefingOut | None, index: EvidenceIndex | None) -> None:
    if briefing is None or not briefing.evidence:
        return
    if index is None:
        raise HTTPException(status_code=422, detail="이 데이터셋에는 근거 색인이 없어 브리핑 근거를 확인할 수 없다")
    for e in briefing.evidence:
        src = index.source(e.source_id)
        if src is None or src.reviewer_id != e.reviewer_id:
            raise HTTPException(status_code=422, detail=f"이 데이터셋에 없는 근거 출처다: {e.source_id}")
        if e.kind == "quote":
            ok = index.reveal_text and index.verify_quote(e.source_id, e.text)
        else:
            ok = any(ev.source_id == e.source_id and ev.kind == e.kind and _clamp_text(ev.text) == e.text
                     for ev in index.for_person(src.reviewee_id, limit=None))
        if not ok:
            raise HTTPException(status_code=422,
                                detail=f"근거 문장이 서버 색인과 다르다({e.kind}): {e.source_id}")
