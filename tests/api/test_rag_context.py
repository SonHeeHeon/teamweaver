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


@pytest.fixture
def data_and_conn(tmp_path):
    ds = generate_dataset(40, 8, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path / "rag.db"
    build_sqlite(ds, parsed, path)
    c = sqlite3.connect(path)
    yield ds, parsed, c
    c.close()


def _reviewed_pair(ds):
    reviewed = {r.reviewee_id for r in ds.reviews}
    ids = [p.id for p in ds.people if p.id in reviewed]
    return ids[0], ids[1]


def test_without_an_index_the_context_is_unchanged(data_and_conn):
    """K5 회귀: 색인을 넘기지 않으면 예전 문맥(sqlite_rag 행)과 같다."""
    from api.rag.context import swap_context
    ds, _, conn = data_and_conn
    out_id, in_id = _reviewed_pair(ds)
    ctx = swap_context(conn, out_id, in_id)
    assert all(set(e) == {"key", "value"} for pid in (out_id, in_id) for e in ctx[pid]["evidence"])
    assert swap_context(conn, out_id, in_id, evidence=None) == ctx


def test_with_an_index_evidence_is_sourced_sentences_not_numbers(data_and_conn):
    """K5: 근거 자리에 극성 숫자 대신 출처 ID가 붙은 원문 문장이 들어간다."""
    from api.rag.context import swap_context
    from api.rag.evidence import build_evidence_index
    ds, parsed, conn = data_and_conn
    idx = build_evidence_index(ds, parsed, reveal_text=True)
    out_id, in_id = _reviewed_pair(ds)
    plain = swap_context(conn, out_id, in_id)
    ctx = swap_context(conn, out_id, in_id, evidence=idx)
    for pid in (out_id, in_id):
        assert ctx[pid]["skills"] == plain[pid]["skills"] and ctx[pid]["coworks"] == plain[pid]["coworks"]
        assert ctx[pid]["evidence"], pid
        for e in ctx[pid]["evidence"]:
            assert set(e) == {"source_id", "reviewer_id", "kind", "text", "polarity"}
            assert e["kind"] == "quote" and idx.verify_quote(e["source_id"], e["text"])
        assert len(ctx[pid]["evidence"]) <= 6


def test_hidden_index_puts_no_review_text_in_the_context(data_and_conn):
    from api.rag.context import swap_context
    from api.rag.evidence import build_evidence_index
    ds, parsed, conn = data_and_conn
    out_id, in_id = _reviewed_pair(ds)
    ctx = swap_context(conn, out_id, in_id, evidence=build_evidence_index(ds, parsed, reveal_text=False))
    texts = [t for r in ds.reviews for t in (r.positive.text, r.negative.text)]
    blob = repr(ctx)
    assert all(e["kind"] == "label" for pid in (out_id, in_id) for e in ctx[pid]["evidence"])
    assert not any(t in blob for t in texts)
