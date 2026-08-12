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
