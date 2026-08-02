import sqlite3
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.sqlite_store import build_sqlite, synergy_context_sql

def _db(tmp_path):
    ds = generate_dataset(30, 6, seed=3)
    parsed = parse_reviews_rule_based(ds.reviews)
    p = tmp_path / "tw.db"
    build_sqlite(ds, parsed, p)
    return ds, sqlite3.connect(p)

def test_counts_roundtrip(tmp_path):
    ds, conn = _db(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM person").fetchone()[0] == 30
    assert conn.execute("SELECT COUNT(*) FROM review").fetchone()[0] == len(ds.reviews)
    n_items = conn.execute("SELECT COUNT(*) FROM review_item").fetchone()[0]
    assert n_items == sum(len(r.positive.items) + len(r.negative.items) for r in ds.reviews)

def test_synergy_context_1hop_matches_collaborations(tmp_path):
    ds, conn = _db(tmp_path)
    pid = ds.coworks[0].a_id
    rows = synergy_context_sql(conn, [pid], hops=1)
    direct = {c.b_id if c.a_id == pid else c.a_id
              for c in ds.coworks if pid in (c.a_id, c.b_id)}
    assert {r[1] for r in rows} == direct
