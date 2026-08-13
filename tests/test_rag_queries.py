"""RAG 질의 계약 테스트."""
from core.rag.queries import RAG_QUERIES, SPECS, RETURN_KEYS, QuerySpec


def test_five_queries_defined():
    assert len(RAG_QUERIES) == 5
    assert set(RAG_QUERIES) == set(SPECS) == set(RETURN_KEYS)


def test_each_spec_has_description_and_complexity():
    for name in RAG_QUERIES:
        s = SPECS[name]
        assert isinstance(s, QuerySpec)
        assert len(s.description) > 10
        assert s.sql_complexity in ("simple", "recursive_cte", "multi_subquery")


def test_return_keys_are_nonempty_tuples():
    for name in RAG_QUERIES:
        keys = RETURN_KEYS[name]
        assert isinstance(keys, tuple) and len(keys) >= 2
        assert all(isinstance(k, str) for k in keys)


def test_specs_are_frozen():
    import dataclasses
    import pytest
    s = SPECS[RAG_QUERIES[0]]
    assert dataclasses.is_dataclass(s)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.name = "changed"


def test_sql_complexity_labels_match_actual_implementation():
    """sql_complexity는 Task 7의 decision.evaluate가 recursive_cte + multi_subquery
    개수를 세어 의사결정 규칙 3(표현력 근거 존치, 5종 중 3종 이상 기준)을 판정하는 데
    쓰인다. Task 1은 SQL이 존재하기 전에 이 값을 채웠고, Task 2/3에서 실제
    core/rag/sqlite_rag.py·core/rag/neo4j_rag.py의 SQL·Cypher를 읽고 재판정한 결과
    swap_diff·replacement_candidates는 multi_subquery로 과대, team_cohesion은 simple로
    과소 표기돼 있었다(각 함수 위 주석에 구조적 근거를 남겼다). 이 테스트는 그 재판정
    결과를 잠가 향후 누군가 queries.py만 조용히 되돌리는 회귀를 막는다 — 값이 바뀌면
    반드시 실제 SQL/Cypher를 다시 읽고 의도적으로 바꿨는지 확인해야 한다."""
    assert {name: SPECS[name].sql_complexity for name in RAG_QUERIES} == {
        "swap_diff": "simple",
        "replacement_candidates": "simple",
        "team_cohesion": "multi_subquery",
        "overfamiliar_pairs": "simple",
        "skill_within_hops": "recursive_cte",
    }
