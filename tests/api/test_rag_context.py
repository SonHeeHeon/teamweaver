import sqlite3

import pytest

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.sqlite_store import build_sqlite


@pytest.fixture
def conn(tmp_path):
    ds = generate_dataset(40, 8, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path / "rag.db"
    build_sqlite(ds, parsed, path)
    c = sqlite3.connect(path)
    yield c
    c.close()


def test_swap_context_groups_by_kind_and_person(conn):
    from api.rag.context import swap_context
    ctx = swap_context(conn, "p000", "p001")
    assert set(ctx.keys()) == {"p000", "p001"}
    for pid in ("p000", "p001"):
        assert set(ctx[pid].keys()) <= {"skills", "coworks", "evidence"}


def test_swap_context_evidence_carries_real_review_sentences(conn):
    """리뷰 근거 인용이 실제로 담기는지 -- PoC 설계 §6이 요구하는
    '리뷰 근거 문장(evidence) 인용'을 직접 확인한다."""
    from api.rag.context import swap_context
    ctx = swap_context(conn, "p000", "p001")
    has_evidence = any(ctx[pid].get("evidence") for pid in ("p000", "p001"))
    # 데이터셋이 우연히 p000/p001에 리뷰가 없을 수도 있으니, 없으면 다른 쌍으로 재확인.
    if not has_evidence:
        ctx = swap_context(conn, "p010", "p011")
        has_evidence = any(ctx[pid].get("evidence") for pid in ("p010", "p011"))
    assert has_evidence
