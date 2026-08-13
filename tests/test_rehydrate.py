"""저장소 → MemoryGraph 재수화와 증분 갱신 검증.

이 파일은 플랜 3 규칙 2(영속성 4지표)의 **계측기 검증**이다. 태스크 6은
여기서 만든 from_sqlite / from_neo4j / append_* 를 그대로 재서 판정한다.
두 재수화가 같은 일을 하지 않거나 append_*의 내구성이 한쪽만 보장되면,
태스크 6이 재는 것은 저장소의 성질이 아니라 이 구현의 성질이 된다.
그래서 여기서는 "돌아간다"가 아니라 "양쪽이 같은 답을 낸다"를 잠근다.
"""
import sqlite3

import numpy as np
import pytest

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import (CoworkRecord, ParsedReview, PeerReview,
                                ReviewSection)
from core.graph import rehydrate
from core.graph.memory_graph import MemoryGraph
from core.graph.neo4j_store import append_cowork as neo4j_append_cowork
from core.graph.neo4j_store import append_review as neo4j_append_review
from core.graph.neo4j_store import get_driver, load_neo4j
from core.graph.sqlite_store import append_cowork, append_review, build_sqlite

# 재수화 테스트용 데이터셋. 40인/8프로젝트는 사람·협업·리뷰가 모두 여러 건씩
# 나오면서도 테스트마다 Neo4j를 새로 적재할 만큼 작다.
DATASET = (40, 8, 5)

# 실험이 실제로 쓰는 규모. 재수화가 의존하는 가정을 이 전부에서 잠근다.
EXPERIMENT_SCALES = [(40, 8, 5), (100, 20, 42), (300, 60, 42),
                     (500, 100, 42), (1000, 200, 42)]

# 데이터셋에 아직 없는 (a, b) 쌍. append 테스트는 반드시 새 레코드를 써야
# 한다 — 기존 쌍에 쓰면 SQLite는 collaboration에 행이 하나 더 쌓여
# csr_matrix가 두 값을 더해버리고(재수화 결과가 9가 아니게 된다), Neo4j는
# MERGE라 갱신만 되어 양쪽이 애초에 다른 일을 하게 된다.
NEW_PAIR = ("p000", "p039")

# append_review가 쓸 새 리뷰. 좋은점 2 / 나쁜점 1이라 item_score가 (2-1)/3로
# 0도 1도 아닌 값이 나온다 — 부호나 분모가 틀려도 걸린다.
NEW_REVIEW_POS = ["문서화", "일정준수"]
NEW_REVIEW_NEG = ["의사소통"]
NEW_REVIEW_POLARITY = 0.25
# MemoryGraph.build: 0.5 * item_score + 0.5 * text_polarity
NEW_REVIEW_PAIR_SCORE = 0.5 * (2 - 1) / (2 + 1) + 0.5 * NEW_REVIEW_POLARITY


@pytest.fixture
def built(tmp_path):
    """SQLite에 적재된 데이터셋 하나.

    append 테스트가 DB를 바꾸므로 함수 스코프다. path도 함께 넘긴다 —
    내구성 검증은 반드시 새 연결로 읽어야 하기 때문이다.
    """
    ds = generate_dataset(*DATASET)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path / "r.db"
    build_sqlite(ds, parsed, path)
    conn = sqlite3.connect(path)
    yield ds, parsed, conn, path
    conn.close()


@pytest.fixture
def both_stores(built):
    """같은 데이터셋을 SQLite와 Neo4j 양쪽에 적재한다.

    Neo4j는 공유 인스턴스이고 load_neo4j가 매번 전체를 지우고 다시 넣는다.
    모듈 스코프로 공유하면 append 테스트의 쓰기가 뒤따르는 동등성 테스트에
    그대로 보여 "두 저장소가 다르다"는 가짜 실패를 만든다 — 함수 스코프로
    격리한다.
    """
    ds, parsed, conn, path = built
    driver = get_driver()
    load_neo4j(driver, ds, parsed)
    yield ds, parsed, conn, path, driver
    driver.close()


def _new_review() -> tuple[PeerReview, ParsedReview]:
    rv, re_ = NEW_PAIR
    review = PeerReview(
        reviewer_id=rv, reviewee_id=re_,
        positive=ReviewSection(items=NEW_REVIEW_POS, text="문서화가 좋았습니다."),
        negative=ReviewSection(items=NEW_REVIEW_NEG, text="의사소통이 아쉬웠습니다."))
    parsed = ParsedReview(reviewer_id=rv, reviewee_id=re_,
                          text_polarity=NEW_REVIEW_POLARITY,
                          evidence=["문서화가 좋았습니다."])
    return review, parsed


def _assert_pair_is_new(ds) -> None:
    """NEW_PAIR가 데이터셋에 없음을 확인한다.

    없어야 SQLite INSERT와 Neo4j MERGE가 둘 다 "새 레코드 1건 쓰기"가 된다.
    데이터셋이 바뀌어 이 쌍이 생기면, 값이 어긋난 원인을 재수화 버그로
    오인하지 않도록 여기서 먼저 분명하게 실패시킨다.
    """
    a, b = NEW_PAIR
    assert not any({c.a_id, c.b_id} == {a, b} for c in ds.coworks), (
        f"{NEW_PAIR}가 이미 협업 이력에 있다 — append가 새 레코드가 아니게 된다. "
        "NEW_PAIR를 데이터셋에 없는 쌍으로 바꿀 것.")
    assert not any((r.reviewer_id, r.reviewee_id) == (a, b) for r in ds.reviews), (
        f"{NEW_PAIR} 방향 리뷰가 이미 있다 — append가 새 레코드가 아니게 된다. "
        "NEW_PAIR를 데이터셋에 없는 쌍으로 바꿀 것.")


def _person_facts(g: MemoryGraph) -> list[tuple]:
    """저장소에서 실제로 복원되는 Person 필드만 뽑는다.

    availability는 뺀다 — 두 from_* 모두 상수 [1.0]*6으로 채우므로 저장소에서
    온 값이 아니다(그 상수성 자체는 _assert_rehydration_constants가 잠근다).
    skills는 백엔드마다 dict 삽입 순서가 다르므로 정렬해 비교한다.
    """
    return [(p.id, p.name, p.grade, p.monthly_rate, sorted(p.skills.items()))
            for p in g.people]


def _assert_same_graph(a: MemoryGraph, b: MemoryGraph, label: str) -> None:
    """MemoryGraph에서 저장소 유래 필드를 **전부** 비교한다.

    비교 대상: people(availability 제외), pid_index, skill_index, skill_levels,
    cowork_months, pair_review_score, node_polarity.

    제외한 필드와 이유:
      - people[].availability, projects, project_index, pair_evidence:
        저장소에서 복원하지 않고 상수([1.0]*6 / [] / {} / {})로 채우는 값이다
        (브리프가 명시한 한계). 여기서 [] == []를 비교하면 아무것도 검증하지
        못하므로, 두 그래프가 같은 상수를 쓰는지는
        _assert_rehydration_constants가 따로 단언한다.

    pair_review_score / node_polarity만 1e-9 허용오차로 본다: Neo4j의 MATCH는
    행 순서를 보장하지 않아 np.mean의 덧셈 순서가 SQLite와 달라질 수 있다.
    달라지는 것은 값이 아니라 부동소수 결합 순서뿐이므로 키 집합은 정확히
    같아야 하고, 값은 마지막 자리 오차 안이어야 한다.
    """
    assert a.pid_index == b.pid_index, f"{label}: pid_index 불일치"
    assert _person_facts(a) == _person_facts(b), f"{label}: people 필드 불일치"
    assert a.skill_index == b.skill_index, f"{label}: skill_index 불일치"

    assert a.skill_levels.shape == b.skill_levels.shape, (
        f"{label}: skill_levels 모양 불일치 "
        f"{a.skill_levels.shape} vs {b.skill_levels.shape} — 스킬 어휘가 다르다")
    assert np.allclose(a.skill_levels, b.skill_levels), f"{label}: skill_levels 값 불일치"

    assert a.cowork_months.shape == b.cowork_months.shape, f"{label}: cowork_months 모양 불일치"
    assert np.allclose(a.cowork_months.toarray(), b.cowork_months.toarray()), (
        f"{label}: cowork_months 값 불일치")

    assert a.pair_review_score.keys() == b.pair_review_score.keys(), (
        f"{label}: pair_review_score 키 불일치")
    for k in a.pair_review_score:
        assert abs(a.pair_review_score[k] - b.pair_review_score[k]) < 1e-9, (
            f"{label}: pair_review_score{k} "
            f"{a.pair_review_score[k]} vs {b.pair_review_score[k]}")

    assert a.node_polarity.keys() == b.node_polarity.keys(), f"{label}: node_polarity 키 불일치"
    for k in a.node_polarity:
        assert abs(a.node_polarity[k] - b.node_polarity[k]) < 1e-9, (
            f"{label}: node_polarity[{k}] {a.node_polarity[k]} vs {b.node_polarity[k]}")


def _assert_rehydration_constants(g: MemoryGraph, label: str) -> None:
    """저장소에서 복원하지 않는 필드가 정해진 상수인지 잠근다.

    브리프가 명시한 한계다 — availability와 projects는 재수화 **비용** 비교가
    목적이라 상수로 채운다. 중요한 것은 그 상수성이 두 백엔드에서 대칭이라는
    점이다: 한쪽만 실제 값을 복원하면 그쪽 rehydrate_ms가 부당하게 비싸진다.
    """
    assert g.projects == [], f"{label}: projects는 상수 []여야 한다"
    assert g.project_index == {}, f"{label}: project_index는 상수 {{}}여야 한다"
    assert g.pair_evidence == {}, (
        f"{label}: evidence는 양쪽 다 버린다 — SQLite review 테이블에는 evidence "
        "컬럼 자체가 없으므로, Neo4j에서만 읽으면 재수화 비용 비교가 불공정해진다")
    assert all(p.availability == [1.0] * 6 for p in g.people), (
        f"{label}: availability는 상수 [1.0]*6이어야 한다")


# --------------------------------------------------------------------------
# 재수화 동등성
# --------------------------------------------------------------------------

def test_rehydrate_from_sqlite_matches_direct_build(built):
    """재수화 결과가 원본에서 직접 만든 MemoryGraph와 같아야 한다 —
    다르면 재시작 후 시스템이 다른 답을 내게 된다."""
    ds, parsed, conn, path = built
    direct = MemoryGraph.build(ds, parsed)
    restored = rehydrate.from_sqlite(conn)
    _assert_same_graph(restored, direct, "sqlite 재수화 vs 직접 빌드")
    _assert_rehydration_constants(restored, "sqlite 재수화")


@pytest.mark.neo4j
def test_rehydrate_from_neo4j_matches_direct_build(both_stores):
    """Neo4j 재수화도 **직접 빌드**를 기준으로 검증한다.

    두 재수화가 서로만 같으면 둘 다 같은 방식으로 틀렸을 때 통과한다
    (예: 양쪽이 똑같이 "미상" 대체값을 끼워 넣으면 서로는 같고 진실과는
    다르다). 그래서 SQLite 쪽과 마찬가지로 원본 기준 앵커를 따로 둔다.
    """
    ds, parsed, conn, path, driver = both_stores
    direct = MemoryGraph.build(ds, parsed)
    restored = rehydrate.from_neo4j(driver)
    _assert_same_graph(restored, direct, "neo4j 재수화 vs 직접 빌드")
    _assert_rehydration_constants(restored, "neo4j 재수화")


@pytest.mark.neo4j
def test_rehydrate_from_neo4j_matches_sqlite(both_stores):
    """두 저장소에서 재수화한 그래프가 같아야 한다 — 다르면 저장소 교체가
    시스템의 답을 바꾼다는 뜻이다.

    특히 pair_review_score를 반드시 본다. 두 from_*에서 가장 갈라지기 쉬운
    경로가 리뷰 복원이기 때문이다: 항목 리스트, 극성, _item_score 계산,
    정렬된 쌍 키 만들기가 전부 여기 모여 있다. pid_index/skill_levels만
    비교하면 이 경로는 검증되지 않은 채 통과한다.
    """
    ds, parsed, conn, path, driver = both_stores
    a = rehydrate.from_sqlite(conn)
    b = rehydrate.from_neo4j(driver)
    _assert_same_graph(a, b, "sqlite 재수화 vs neo4j 재수화")
    _assert_rehydration_constants(a, "sqlite 재수화")
    _assert_rehydration_constants(b, "neo4j 재수화")
    # 비교가 공허하지 않았음을 증명한다 — 빈 dict끼리 비교해 통과하는 사고를 막는다.
    assert a.pair_review_score, "pair_review_score가 비어 있다 — 비교가 공허하다"
    assert a.node_polarity, "node_polarity가 비어 있다 — 비교가 공허하다"
    assert a.cowork_months.nnz > 0, "cowork_months가 비어 있다 — 비교가 공허하다"


# --------------------------------------------------------------------------
# 증분 갱신 (SQLite)
# --------------------------------------------------------------------------

def test_append_cowork_is_visible_after_rehydrate(built):
    ds, parsed, conn, path = built
    _assert_pair_is_new(ds)
    rec = CoworkRecord(a_id=NEW_PAIR[0], b_id=NEW_PAIR[1], co_months=9, project_count=2)
    append_cowork(conn, rec)
    g = rehydrate.from_sqlite(conn)
    i, j = g.pid_index[NEW_PAIR[0]], g.pid_index[NEW_PAIR[1]]
    assert g.cowork_months[i, j] == 9 and g.cowork_months[j, i] == 9


def test_append_review_is_visible_after_rehydrate(built):
    """새 리뷰가 pair_review_score까지 도달해야 한다.

    행이 들어갔는지가 아니라 **점수가 맞는지**를 본다 — review는 극성,
    review_item은 항목을 따로 저장하므로 한쪽만 써도 행 개수 검사는 통과한다.
    """
    ds, parsed, conn, path = built
    _assert_pair_is_new(ds)
    review, pr = _new_review()
    append_review(conn, review, pr)
    g = rehydrate.from_sqlite(conn)
    key = tuple(sorted((g.pid_index[NEW_PAIR[0]], g.pid_index[NEW_PAIR[1]])))
    assert key in g.pair_review_score, "새 리뷰가 pair_review_score에 없다"
    assert g.pair_review_score[key] == pytest.approx(NEW_REVIEW_PAIR_SCORE)


def test_appends_are_durable_to_a_fresh_connection(built):
    """append_*가 반환한 시점에 이미 디스크에 커밋돼 있어야 한다.

    태스크 6은 append_review/append_cowork를 규칙 2의 지표로 잰다. 한쪽은
    커밋하고 한쪽은 버퍼링한다면 그 측정은 저장소 비교가 아니라 커밋 여부
    비교가 된다. 쓰기에 쓴 conn으로 읽으면 커밋되지 않은 쓰기도 보이므로,
    반드시 **새 연결**로 읽어 확인한다.
    """
    ds, parsed, conn, path = built
    _assert_pair_is_new(ds)
    rec = CoworkRecord(a_id=NEW_PAIR[0], b_id=NEW_PAIR[1], co_months=9, project_count=2)
    review, pr = _new_review()
    append_cowork(conn, rec)
    append_review(conn, review, pr)

    fresh = sqlite3.connect(path)
    try:
        assert fresh.execute(
            "SELECT co_months, project_count FROM collaboration WHERE a_id=? AND b_id=?",
            NEW_PAIR).fetchall() == [(9, 2)]
        assert fresh.execute(
            "SELECT text_polarity FROM review WHERE reviewer_id=? AND reviewee_id=?",
            NEW_PAIR).fetchall() == [(NEW_REVIEW_POLARITY,)]
        items = fresh.execute(
            "SELECT item, is_positive FROM review_item WHERE reviewer_id=? AND reviewee_id=?",
            NEW_PAIR).fetchall()
        assert sorted(items) == sorted([(it, 1) for it in NEW_REVIEW_POS] +
                                       [(it, 0) for it in NEW_REVIEW_NEG])
    finally:
        fresh.close()


# --------------------------------------------------------------------------
# 증분 갱신 (Neo4j)
# --------------------------------------------------------------------------

@pytest.mark.neo4j
def test_neo4j_append_cowork_is_visible_after_rehydrate(both_stores):
    ds, parsed, conn, path, driver = both_stores
    _assert_pair_is_new(ds)
    rec = CoworkRecord(a_id=NEW_PAIR[0], b_id=NEW_PAIR[1], co_months=9, project_count=2)
    neo4j_append_cowork(driver, rec)
    g = rehydrate.from_neo4j(driver)
    i, j = g.pid_index[NEW_PAIR[0]], g.pid_index[NEW_PAIR[1]]
    assert g.cowork_months[i, j] == 9 and g.cowork_months[j, i] == 9


@pytest.mark.neo4j
def test_neo4j_append_review_is_visible_after_rehydrate(both_stores):
    ds, parsed, conn, path, driver = both_stores
    _assert_pair_is_new(ds)
    review, pr = _new_review()
    neo4j_append_review(driver, review, pr)
    g = rehydrate.from_neo4j(driver)
    key = tuple(sorted((g.pid_index[NEW_PAIR[0]], g.pid_index[NEW_PAIR[1]])))
    assert key in g.pair_review_score, "새 리뷰가 pair_review_score에 없다"
    assert g.pair_review_score[key] == pytest.approx(NEW_REVIEW_PAIR_SCORE)


@pytest.mark.neo4j
def test_neo4j_appends_are_durable_to_a_fresh_driver(both_stores):
    """Neo4j 쪽도 반환 시점에 커밋돼 있어야 한다 — SQLite와 대칭 검증.

    s.run은 자동 커밋 트랜잭션이라 세션이 닫힐 때 커밋된다. 같은 driver의
    새 세션이면 커넥션 풀을 공유하므로, 아예 **새 driver**를 열어 읽는다.

    evidence까지 확인하는 이유: Neo4j의 REVIEWED에는 evidence가 실제로
    저장되지만 SQLite review 테이블에는 그 컬럼이 아예 없다. 이 비대칭은
    태스크 6의 disk_bytes 지표에 그대로 반영되므로 코드에서 눈에 보이게 둔다.
    """
    ds, parsed, conn, path, driver = both_stores
    _assert_pair_is_new(ds)
    rec = CoworkRecord(a_id=NEW_PAIR[0], b_id=NEW_PAIR[1], co_months=9, project_count=2)
    review, pr = _new_review()
    neo4j_append_cowork(driver, rec)
    neo4j_append_review(driver, review, pr)

    fresh = get_driver()
    try:
        with fresh.session() as s:
            row = s.run("MATCH (a:Person {id:$a})-[w:WORKED_WITH]->(b:Person {id:$b}) "
                        "RETURN w.co_months AS m, w.project_count AS c",
                        a=NEW_PAIR[0], b=NEW_PAIR[1]).single()
            assert row is not None, "새 driver에서 WORKED_WITH가 보이지 않는다 — 커밋되지 않았다"
            assert (row["m"], row["c"]) == (9, 2)

            row = s.run("MATCH (a:Person {id:$rv})-[v:REVIEWED]->(b:Person {id:$re}) "
                        "RETURN v.pos_items AS pos, v.neg_items AS neg, "
                        "v.polarity AS pol, v.evidence AS ev",
                        rv=NEW_PAIR[0], re=NEW_PAIR[1]).single()
            assert row is not None, "새 driver에서 REVIEWED가 보이지 않는다 — 커밋되지 않았다"
            assert row["pos"] == NEW_REVIEW_POS
            assert row["neg"] == NEW_REVIEW_NEG
            assert row["pol"] == pytest.approx(NEW_REVIEW_POLARITY)
            assert row["ev"] == pr.evidence
    finally:
        fresh.close()


# --------------------------------------------------------------------------
# 재수화가 기대는 가정들 (깨지면 조용히 틀리는 것들)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("n_people,n_projects,seed", EXPERIMENT_SCALES)
def test_rehydration_assumptions_hold_at_experiment_scales(n_people, n_projects, seed):
    """재수화가 조용히 틀릴 수 있는 가정 다섯 개를 실험 규모 전부에서 잠근다.

    (1) 빈 리뷰 섹션: from_sqlite·from_neo4j는 이때 "미상" 항목을 끼워 넣는다.
        ReviewSection(min_length=1) 때문에 크래시를 막으려 둔 장치지만, 실제로
        발동하면 _item_score = (n_pos - n_neg)/(n_pos + n_neg)의 분자·분모가
        바뀌어 pair_review_score가 조용히 달라진다.
    (2) 스킬 없는 사람: 같은 이유로 {"미상": 1}이 들어가 skill_index 어휘가
        넓어지고 skill_levels의 모양이 달라진다.
    (3) 프로젝트 요구 스킬: _assemble은 Dataset(projects=[])로 그래프를 만든다.
        MemoryGraph.build의 스킬 어휘는 (사람 스킬 ∪ 요구 스킬)이므로, 아무도
        갖지 않은 요구 스킬이 하나라도 있으면 재수화 그래프의 skill_index가
        직접 빌드보다 좁아진다.
    (4) 사람 id 정렬: 두 from_*는 ORDER BY id로 사람 순서를 정한다. 이 순서가
        Dataset 순서와 다르면 pid_index가 어긋나고, 인덱스 쌍으로 키를 만드는
        pair_review_score가 통째로 달라진다.
    (5) (리뷰어, 피리뷰어) 중복: MemoryGraph.build는 방향별 dict로 1건만 남기지만
        저장소는 두 행을 그대로 갖는다 — 중복이 있으면 재수화가 직접 빌드와
        달라진다.
    """
    ds = generate_dataset(n_people, n_projects, seed)

    empty = [(r.reviewer_id, r.reviewee_id) for r in ds.reviews
             if not r.positive.items or not r.negative.items]
    assert not empty, f'빈 리뷰 섹션이 있다 {empty[:3]} — 재수화의 "미상" 대체값이 발동한다'

    skill_less = [p.id for p in ds.people if not p.skills]
    assert not skill_less, f'스킬 없는 사람이 있다 {skill_less[:3]} — {{"미상": 1}} 대체값이 발동한다'

    required = {r.skill for j in ds.projects for r in j.requirements}
    held = {s for p in ds.people for s in p.skills}
    assert required <= held, (
        f"아무도 갖지 않은 요구 스킬이 있다 {sorted(required - held)} — "
        "_assemble의 Dataset(projects=[])이 skill_index 어휘를 좁힌다. "
        "고친다면 SQLite requirement 테이블과 Neo4j REQUIRES 양쪽에서 대칭으로 "
        "읽어야 하고, 늘어난 재수화 비용을 공개해야 한다.")

    ids = [p.id for p in ds.people]
    assert ids == sorted(ids), "사람 id가 오름차순이 아니다 — ORDER BY id가 Dataset 순서와 어긋난다"

    dirs = [(r.reviewer_id, r.reviewee_id) for r in ds.reviews]
    assert len(dirs) == len(set(dirs)), "같은 방향 리뷰가 중복이다 — 재수화가 직접 빌드와 달라진다"


def test_sqlite_store_has_no_empty_review_section(built):
    """저장소를 거친 뒤에도 빈 섹션이 없는지 본다.

    데이터셋 단계 검사(위)와 별개로 필요하다 — from_sqlite는 review 행에
    대응하는 review_item이 아예 없을 때도 "미상"을 끼워 넣으므로, 적재
    과정에서 항목이 유실되는 경로까지 여기서 막힌다.
    """
    ds, parsed, conn, path = built
    for flag, label in ((1, "좋은점"), (0, "나쁜점")):
        n = conn.execute(
            "SELECT COUNT(*) FROM review r WHERE NOT EXISTS ("
            " SELECT 1 FROM review_item i WHERE i.reviewer_id = r.reviewer_id"
            " AND i.reviewee_id = r.reviewee_id AND i.is_positive = ?)", (flag,)).fetchone()[0]
        assert n == 0, f'{label} 항목이 없는 리뷰가 {n}건 — "미상" 대체값이 발동한다'
    n = conn.execute(
        "SELECT COUNT(*) FROM person p WHERE NOT EXISTS ("
        " SELECT 1 FROM person_skill s WHERE s.person_id = p.id)").fetchone()[0]
    assert n == 0, f'스킬 행이 없는 사람이 {n}명 — {{"미상": 1}} 대체값이 발동한다'


@pytest.mark.neo4j
def test_neo4j_store_has_no_empty_review_section(both_stores):
    """Neo4j 쪽 같은 검사 — SQLite에서만 확인하면 대칭이 깨진다."""
    ds, parsed, conn, path, driver = both_stores
    with driver.session() as s:
        n = s.run("MATCH (:Person)-[v:REVIEWED]->(:Person) "
                  "WHERE v.pos_items IS NULL OR size(v.pos_items) = 0 "
                  "OR v.neg_items IS NULL OR size(v.neg_items) = 0 "
                  "RETURN count(v) AS n").single()["n"]
        assert n == 0, f'빈 항목 리스트를 가진 REVIEWED가 {n}건 — "미상" 대체값이 발동한다'
        n = s.run("MATCH (p:Person) WHERE NOT (p)-[:HAS_SKILL]->() "
                  "RETURN count(p) AS n").single()["n"]
        assert n == 0, f'HAS_SKILL이 없는 사람이 {n}명 — {{"미상": 1}} 대체값이 발동한다'
