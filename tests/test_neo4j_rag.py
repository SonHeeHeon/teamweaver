import sqlite3
import pytest
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.sqlite_store import build_sqlite
from core.graph.neo4j_store import get_driver, load_neo4j
from core.rag import sqlite_rag, neo4j_rag
from core.rag.queries import RAG_QUERIES, RETURN_KEYS

pytestmark = pytest.mark.neo4j


@pytest.fixture(scope="module")
def both(tmp_path_factory):
    ds = generate_dataset(60, 12, seed=42)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path_factory.mktemp("ragn") / "rag.db"
    build_sqlite(ds, parsed, path)
    conn = sqlite3.connect(path)
    driver = get_driver()
    load_neo4j(driver, ds, parsed)
    yield conn, driver, ds
    conn.close()
    driver.close()


def test_all_five_implemented():
    assert set(neo4j_rag.ALL) == set(RAG_QUERIES)


def _norm(rows, name):
    """비교용 정규화: 키 순서·부동소수 오차·행 순서를 제거한다.

    swap_diff의 'value' 필드는 skill/cowork/evidence 세 종류를 하나의
    문자열 컬럼으로 묶으려고 양쪽 백엔드가 각각 SQLite `CAST(...AS TEXT)`,
    Cypher `toString()`을 쓴다. 두 변환은 반복소수(1/3, 2/3 등)에서
    유효자릿수가 달라 같은 IEEE754 double이라도 문자열이 어긋난다 —
    실측: 이 시드(42)의 60인 데이터셋 리뷰 166건 중 41건(약 25%)에서
    `CAST(REAL AS TEXT)`와 `repr()`(Neo4j `toString()`과 동일 자릿수)이
    불일치했다(예: 1/3 → SQLite `'0.33333333333333332'` vs Neo4j
    `'0.3333333333333333'`). 값 자체는 같으므로 이 필드만 숫자로 파싱해
    반올림 후 비교한다 — 다른 질의의 문자열 필드(person_id, skill명 등)는
    숫자로 보이더라도(예: person_id가 우연히 숫자 형태) 원문 그대로
    비교해야 하므로, 코어션은 실제로 필요한 이 한 필드로 한정한다.
    """
    out = []
    for r in rows:
        item = []
        for k in RETURN_KEYS[name]:
            v = r[k]
            if isinstance(v, float):
                v = round(v, 9)
            elif name == "swap_diff" and k == "value" and isinstance(v, str):
                try:
                    v = round(float(v), 9)
                except ValueError:
                    pass
            item.append(v)
        out.append(tuple(item))
    return sorted(out, key=lambda t: tuple(str(x) for x in t))


def test_parity_all_five_queries(both):
    """공정성의 전제: 양쪽이 같은 질문에 같은 답을 내야 비교가 성립한다.

    team_cohesion은 ids[:6](p000..p005)로는 팀 내부에 WORKED_WITH 엣지가
    전혀 없어 양쪽 다 빈 리스트를 반환한다 — [] == []는 아무것도 검증하지
    않는 공허한 통과다. team_cohesion은 5개 질의 중 SQL↔Cypher 대응이 가장
    까다롭다(널 평균 경로, 암묵적 그룹핑, a.id < b.id ↔ MIN/MAX 저장 규약,
    양방향 리뷰 OR) — 실측(ids[:30] → 35행, 그 중 1행은 REVIEWED 관계가 아예
    없어 polarity가 None)으로 populated-average/null-average 두 경로를 모두
    실제로 태운다.
    """
    conn, driver, ds = both
    ids = [p.id for p in ds.people]
    cases = {
        "swap_diff": (lambda m: m.swap_diff, (ids[0], ids[1])),
        "replacement_candidates": (lambda m: m.replacement_candidates,
                                   ("Java", 3, ids[:5])),
        "team_cohesion": (lambda m: m.team_cohesion, (ids[:30],)),
        "overfamiliar_pairs": (lambda m: m.overfamiliar_pairs, (6,)),
        "skill_within_hops_2hop": (lambda m: m.skill_within_hops,
                                    (ids[0], "Java", 2)),
        "skill_within_hops_3hop": (lambda m: m.skill_within_hops,
                                    (ids[0], "Java", 3)),
    }
    for name, (getter, args) in cases.items():
        return_key = "skill_within_hops" if name.startswith("skill_within_hops") \
            else name
        s = _norm(getter(sqlite_rag)(conn, *args), return_key)
        n = _norm(getter(neo4j_rag)(driver, *args), return_key)
        assert s == n, f"{name}: 백엔드 결과 불일치\nsqlite={s[:3]}\nneo4j={n[:3]}"
        assert s, (
            f"{name}: 양쪽 다 빈 결과다 — parity 비교가 공허하게 통과했다. "
            "[] == []는 아무것도 검증하지 않는다. 실제 행을 반환하는 인자로 "
            "바꿀 것."
        )


def test_swap_diff_parity_survives_repeating_decimal_polarity(both):
    """swap_diff('value')의 문자열 변환은 SQLite CAST/Neo4j toString의 유효자릿수
    차이로 반복소수(1/3, 2/3 등)에서 원문 문자열이 어긋날 수 있다. ids[0]/ids[1]
    (p000/p001)은 우연히 이 값들을 피해가므로 test_parity_all_five_queries만으로는
    이 회귀를 못 잡는다. ids[2]/ids[3](p002/p003)는 seed=42 데이터셋에서 실제로
    1/3, 2/3 계열의 polarity를 갖는 리뷰가 걸리는 쌍임을 확인했다 — 이 쌍으로
    명시적으로 잠근다."""
    conn, driver, ds = both
    ids = [p.id for p in ds.people]
    s = _norm(sqlite_rag.swap_diff(conn, ids[2], ids[3]), "swap_diff")
    n = _norm(neo4j_rag.swap_diff(driver, ids[2], ids[3]), "swap_diff")
    assert s == n


def test_no_label_scan_in_any_query(both):
    """Plan 2에서 Neo4j가 라벨 스캔으로 불이익을 봤던 전례 재발 방지.

    5개 질의 전부를 검사한다. swap_diff/skill_within_hops는 노드 프로퍼티로
    시작 지점을 잡아 스캔 위험이 낮지만, 나머지 3종이 오히려 위험하다 —
    overfamiliar_pairs는 노드 프로퍼티 앵커가 아예 없고, team_cohesion의
    OPTIONAL MATCH는 (이 수정 전에는) 런타임 프로퍼티 비교로 Person을
    재매치했다. AllNodesScan도 NodeByLabelScan과 마찬가지로(오히려 더 나쁜)
    스캔이므로 함께 금지한다. profile이 비어 있으면 str(None) == "None"이
    되어 두 문자열 다 통과해버리므로 None이 아님을 먼저 확인한다.
    """
    conn, driver, ds = both
    ids = [p.id for p in ds.people]
    profiles = {
        "swap_diff": neo4j_rag.PROFILE_QUERIES["swap_diff"](ids[0], ids[1]),
        "replacement_candidates": neo4j_rag.PROFILE_QUERIES["replacement_candidates"](
            "Java", 3, ids[:5]),
        "team_cohesion": neo4j_rag.PROFILE_QUERIES["team_cohesion"](ids[:30]),
        "overfamiliar_pairs": neo4j_rag.PROFILE_QUERIES["overfamiliar_pairs"](6),
        "skill_within_hops": neo4j_rag.PROFILE_QUERIES["skill_within_hops"](
            ids[0], "Java", 2),
    }
    with driver.session() as s:
        for name, (cypher, params) in profiles.items():
            plan = s.run("PROFILE " + cypher, **params).consume().profile
            assert plan is not None, f"{name}: PROFILE 결과가 비어 있다"
            text = str(plan)
            assert "NodeByLabelScan" not in text, f"{name}: 라벨 스캔 발생 — 인덱스 미사용"
            assert "AllNodesScan" not in text, f"{name}: 전체 노드 스캔 발생 — 인덱스 미사용"
