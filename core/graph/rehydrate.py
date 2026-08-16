"""저장소 → MemoryGraph 재구축.

인메모리 계층은 영속성이 없다 — 프로세스가 재시작되면 저장소에서 전량 다시
읽어야 한다. 그래서 이 함수의 소요 시간이 곧 API 서버의 콜드 스타트 비용이고,
"저장소가 왜 필요한가"에 대한 가장 직접적인 답이다.

한계(의도된 것): availability와 projects는 저장소에서 복원하지 않고 상수로
채운다. 이 모듈의 목적은 재수화 **비용** 비교이지 완전한 상태 복원이 아니다.
중요한 것은 그 생략이 두 백엔드에서 대칭이라는 점이다 — 한쪽만 더 읽으면
그쪽 재수화 시간이 부당하게 비싸진다. evidence도 같은 이유로 양쪽 다 버린다
(SQLite review 테이블에는 evidence 컬럼 자체가 없다).
"""
from core.domain.models import (CoworkRecord, Dataset, Grade, ParsedReview,
                                PeerReview, Person, ReviewSection)
from core.graph.memory_graph import MemoryGraph

# 빈 리뷰 섹션 대체값. ReviewSection.items는 min_length=1이라 빈 리스트로는
# 모델 생성 자체가 실패한다 — 크래시를 막으려고 두는 값이다. 발동하면
# _item_score = (n_pos - n_neg)/(n_pos + n_neg)의 분자·분모가 바뀌어
# pair_review_score가 조용히 달라진다. 그래서 tests/test_rehydrate.py의
# test_rehydration_assumptions_hold_at_experiment_scales와
# test_*_store_has_no_empty_review_section이 실험이 쓰는 규모 전부에서
# "이 값은 발동하지 않는다"를 잠근다. 검사를 함수 안에 넣지 않는 이유는
# 재수화가 태스크 6의 피측정 대상이라 측정 경로에 추가 질의를 넣을 수 없기
# 때문이다.
#
# 스킬 쪽에는 이 대체값을 쓰지 않는다. min_length=1은 ReviewSection에만 있는
# 제약이고 Person.skills: dict[str, int]에는 길이 제약이 없어 {}가 합법이다
# (core/domain/models.py:18, :21-26) — 스킬 없는 사람을 "미상 스킬 보유자"로
# 위장시키지 않고 {}로 정직하게 복원한다. skills.get(pid, {}) / skills=sk를 쓴다.
_UNKNOWN = "미상"


def _assemble(people: list[Person], coworks: list[CoworkRecord],
              reviews: list[PeerReview], parsed: list[ParsedReview]) -> MemoryGraph:
    """두 백엔드가 공유하는 조립 단계.

    모듈 수준 함수로 두는 이유: 이 안의 MemoryGraph.build는 저장소와 무관한
    순수 파이썬 비용이다. 재수화 시간에서 이 몫이 크면 rehydrate_ms는 저장소
    성능에 둔감해진다 — 태스크 6이 이 부분을 따로 재서 공개할 수 있어야 한다.
    """
    ds = Dataset(people=people, projects=[], coworks=coworks, reviews=reviews)
    return MemoryGraph.build(ds, parsed)


def from_sqlite(conn) -> MemoryGraph:
    skills: dict[str, dict[str, int]] = {}
    for pid, skill, lv in conn.execute("SELECT person_id, skill, level FROM person_skill"):
        skills.setdefault(pid, {})[skill] = lv
    people = []
    for pid, name, grade, rate in conn.execute(
            "SELECT id, name, grade, monthly_rate FROM person ORDER BY id"):
        people.append(Person(id=pid, name=name, grade=Grade(grade), monthly_rate=rate,
                             skills=skills.get(pid, {}),
                             availability=[1.0] * 6))
    coworks = [CoworkRecord(a_id=a, b_id=b, co_months=m, project_count=c)
               for a, b, m, c in conn.execute(
                   "SELECT a_id, b_id, co_months, project_count FROM collaboration")]
    items: dict[tuple[str, str], dict[bool, list[str]]] = {}
    for rv, re_, it, pos in conn.execute(
            "SELECT reviewer_id, reviewee_id, item, is_positive FROM review_item"):
        items.setdefault((rv, re_), {True: [], False: []})[bool(pos)].append(it)
    reviews, parsed = [], []
    for rv, re_, pol in conn.execute(
            "SELECT reviewer_id, reviewee_id, text_polarity FROM review"):
        sel = items.get((rv, re_), {True: [_UNKNOWN], False: [_UNKNOWN]})
        reviews.append(PeerReview(
            reviewer_id=rv, reviewee_id=re_,
            positive=ReviewSection(items=sel[True] or [_UNKNOWN], text=""),
            negative=ReviewSection(items=sel[False] or [_UNKNOWN], text="")))
        parsed.append(ParsedReview(reviewer_id=rv, reviewee_id=re_,
                                   text_polarity=pol, evidence=[]))
    return _assemble(people, coworks, reviews, parsed)
