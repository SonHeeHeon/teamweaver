"""리뷰 근거 색인(K5): 브리핑에 쓰는 리뷰 근거마다 출처 ID를 붙이고, "직접 인용"인지 검사한다.

예전 문맥은 근거 자리에 리뷰 극성 숫자만 있었다(SQLite review 표에 문장이 없다). LLM은 문장을
본 적 없이 "근거"를 써야 했다. 이 색인은 데이터셋을 활성화할 때 한 번 만들어 메모리에 둔다 --
공유 SQLite 스키마(Codex 실험의 측정 대상)를 바꾸지 않기 위해서다.

출처 ID: ``rv:<평가자>><피평가자>#<회차>:<pos|neg>``. 회차는 같은 방향 리뷰가 ds.reviews에 나오는
순서(1부터)다. core.ingest는 (날짜, review_id) 순으로 넘기므로 #1이 가장 오래된 회차다.
"최신 우선"은 ds.reviews 목록의 역순이라는 뜻이다. 날짜가 없는 datagen·fixture에서는 날짜순이 아니다.

근거 종류:
- quote   파싱 근거(또는 원문 첫 문장)가 원문에 글자 그대로 들어 있다(NFC·공백만 정규화).
- summary 파싱 근거가 원문과 다르다(LLM 파서가 바꿔 쓴 경우) -- 인용이라고 부르지 않는다.
- label   원문 숨김 모드(실데이터). 문장 없이 리뷰 항목 라벨만 담고, 원문은 색인에도 남기지 않는다.
"""
import difflib
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from core.domain.models import Dataset, ParsedReview, PeerReview

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 6
MIN_QUOTE_CHARS = 5                 # "." 같은 조각이 원문 포함 검사를 통과하지 않게
_SECTIONS = (("positive", "pos", "좋은 점"), ("negative", "neg", "아쉬운 점"))
_SENTENCE = re.compile(r"[^.!?。]+[.!?。]?")


def normalize(text: str) -> str:
    """NFC + 연속 공백 하나로 + 앞뒤 공백 제거. 글자·조사·문장부호는 바꾸지 않는다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _first_sentence(text: str) -> str:
    m = _SENTENCE.search(text.strip())
    return m.group(0).strip() if m else ""


@dataclass(frozen=True)
class EvidenceSource:
    source_id: str
    reviewer_id: str
    reviewee_id: str
    round_no: int
    section: Literal["positive", "negative"]
    text: str                       # 숨김 모드면 ""


@dataclass(frozen=True)
class Evidence:
    source_id: str
    reviewer_id: str
    kind: Literal["quote", "summary", "label"]
    text: str
    polarity: float | None          # 그 리뷰의 text_polarity. 파싱 결과와 짝이 안 맞으면 None


class EvidenceIndex:
    def __init__(self, sources: dict[str, EvidenceSource],
                 by_person: dict[str, list[Evidence]], reveal_text: bool):
        self._sources = sources
        self._by_person = by_person       # 최신 회차가 앞
        self.reveal_text = reveal_text

    def source(self, source_id: str) -> EvidenceSource | None:
        return self._sources.get(source_id)

    def for_person(self, person_id: str, limit: int | None = DEFAULT_LIMIT) -> list[Evidence]:
        evs = self._by_person.get(person_id, [])
        return list(evs if limit is None else evs[:limit])

    def verify_quote(self, source_id: str, quote: str) -> bool:
        """quote가 그 출처 원문에 글자 그대로 있는가. 숨김 모드·없는 출처·너무 짧은 조각은 False."""
        src = self._sources.get(source_id)
        q = normalize(quote)
        if src is None or not src.text or len(q) < MIN_QUOTE_CHARS:
            return False
        return q in normalize(src.text)


def _aligned(ds: Dataset, parsed: list[ParsedReview]) -> bool:
    return len(parsed) == len(ds.reviews) and all(
        (p.reviewer_id, p.reviewee_id) == (r.reviewer_id, r.reviewee_id)
        for p, r in zip(parsed, ds.reviews))


def _classify(review: PeerReview, sid: dict[str, str], text: str) -> tuple[str, str]:
    """파싱 근거 하나의 (출처 ID, 종류). 원문에 들어 있는 쪽이 출처, 아니면 더 비슷한 쪽."""
    q = normalize(text)
    for field, _, _ in _SECTIONS:
        if len(q) >= MIN_QUOTE_CHARS and q in normalize(getattr(review, field).text):
            return sid[field], "quote"
    ratio = {field: difflib.SequenceMatcher(None, q, normalize(getattr(review, field).text)).ratio()
             for field, _, _ in _SECTIONS}
    return sid[max(ratio, key=ratio.get)], "summary"


def build_evidence_index(ds: Dataset, parsed: list[ParsedReview], *, reveal_text: bool) -> EvidenceIndex:
    aligned = _aligned(ds, parsed)
    if not aligned and ds.reviews:
        log.warning("parsed reviews do not line up with ds.reviews (%d vs %d); evidence uses the first "
                    "sentence of each review section", len(parsed), len(ds.reviews))
    sources: dict[str, EvidenceSource] = {}
    per_review: dict[str, list[list[Evidence]]] = defaultdict(list)
    rounds: dict[tuple[str, str], int] = defaultdict(int)
    for i, r in enumerate(ds.reviews):
        rounds[(r.reviewer_id, r.reviewee_id)] += 1
        n = rounds[(r.reviewer_id, r.reviewee_id)]
        sid = {field: f"rv:{r.reviewer_id}>{r.reviewee_id}#{n}:{short}" for field, short, _ in _SECTIONS}
        for field, _, _ in _SECTIONS:
            sources[sid[field]] = EvidenceSource(sid[field], r.reviewer_id, r.reviewee_id, n, field,
                                                 getattr(r, field).text if reveal_text else "")
        polarity = parsed[i].text_polarity if aligned else None
        evs: list[Evidence] = []
        if not reveal_text:
            for field, _, label in _SECTIONS:
                evs.append(Evidence(sid[field], r.reviewer_id, "label",
                                    f"{label}: {'·'.join(getattr(r, field).items)}", polarity))
        elif aligned and parsed[i].evidence:
            for text in parsed[i].evidence:
                source_id, kind = _classify(r, sid, text)
                evs.append(Evidence(source_id, r.reviewer_id, kind, text.strip(), polarity))
        else:
            for field, _, _ in _SECTIONS:
                sentence = _first_sentence(getattr(r, field).text)
                if len(normalize(sentence)) >= MIN_QUOTE_CHARS:
                    evs.append(Evidence(sid[field], r.reviewer_id, "quote", sentence, polarity))
        per_review[r.reviewee_id].append(evs)
    by_person = {pid: [e for evs in reversed(groups) for e in evs] for pid, groups in per_review.items()}
    return EvidenceIndex(sources, by_person, reveal_text)
