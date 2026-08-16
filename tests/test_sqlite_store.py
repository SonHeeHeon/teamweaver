import sqlite3
import pytest
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.sqlite_store import build_sqlite, synergy_context_sql, append_cowork, append_review
from core.domain.models import Dataset, Person, Grade, Project, Sector, ProjectPhase, CoworkRecord, ParsedReview, PeerReview, ReviewSection

def _db(tmp_path):
    ds = generate_dataset(30, 6, seed=3)
    parsed = parse_reviews_rule_based(ds.reviews)
    p = tmp_path / "tw.db"
    build_sqlite(ds, parsed, p)
    return ds, sqlite3.connect(p)


@pytest.fixture
def built(tmp_path):
    """SQLite에 적재된 데이터셋 하나.

    append 테스트가 DB를 바꾸므로 함수 스코프다.
    """
    ds = generate_dataset(40, 8, seed=5)
    parsed = parse_reviews_rule_based(ds.reviews)
    path = tmp_path / "r.db"
    build_sqlite(ds, parsed, path)
    conn = sqlite3.connect(path)
    yield conn
    conn.close()

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

def _make_minimal_dataset(people_ids: list[str], coworks: list[tuple]) -> Dataset:
    """Construct a minimal Dataset with hand-crafted collaborations for topology testing.

    Args:
        people_ids: list of person IDs (e.g., ['A', 'B', 'C'])
        coworks: list of (a_id, b_id) collaboration pairs
    """
    people = [Person(id=pid, name=pid, grade=Grade.SENIOR, monthly_rate=5000,
                     skills={}, availability=[1, 1, 1, 1, 1, 1])
              for pid in people_ids]
    projects = [Project(id="P1", name="P1", sector=Sector.FINANCE, phase=ProjectPhase.EXECUTION,
                        start_month=1, end_month=3, grade_headcount={},
                        requirements=[], monthly_budget=50000)]
    cowork_records = [CoworkRecord(a_id=a, b_id=b, co_months=1, project_count=1)
                      for a, b in coworks]
    reviews = []
    return Dataset(people=people, projects=projects, coworks=cowork_records, reviews=reviews)

def test_synergy_context_2hop_chain(tmp_path):
    """Test 2-hop: A–B–C. hops=1 should return {B}, hops=2 should return {B, C}."""
    ds = _make_minimal_dataset(['A', 'B', 'C'], [('A', 'B'), ('B', 'C')])
    parsed = parse_reviews_rule_based([])
    p = tmp_path / "chain.db"
    build_sqlite(ds, parsed, p)
    conn = sqlite3.connect(p)

    # 1-hop from A
    rows_1 = synergy_context_sql(conn, ['A'], hops=1)
    nodes_1 = {r[1] for r in rows_1}
    assert nodes_1 == {'B'}, f"Expected {{B}}, got {nodes_1}"

    # 2-hops from A
    rows_2 = synergy_context_sql(conn, ['A'], hops=2)
    nodes_2 = {r[1] for r in rows_2}
    assert nodes_2 == {'B', 'C'}, f"Expected {{B, C}}, got {nodes_2}"

    conn.close()

def test_synergy_context_cycle(tmp_path):
    """Test cycle: A–B–C–A. hops=3 should return {B, C} without duplicates (no growth)."""
    ds = _make_minimal_dataset(['A', 'B', 'C'], [('A', 'B'), ('B', 'C'), ('C', 'A')])
    parsed = parse_reviews_rule_based([])
    p = tmp_path / "cycle.db"
    build_sqlite(ds, parsed, p)
    conn = sqlite3.connect(p)

    rows = synergy_context_sql(conn, ['A'], hops=3)
    nodes = {r[1] for r in rows}
    assert nodes == {'B', 'C'}, f"Expected {{B, C}}, got {nodes}"
    # Verify no duplicates in result (each (src, node) pair appears once)
    assert len(rows) == len(set((r[0], r[1]) for r in rows)), "Duplicate (src, node) pairs detected"

    conn.close()

def test_synergy_context_multisource(tmp_path):
    """Test multi-source: [A, C]. Each source should get its own node set."""
    ds = _make_minimal_dataset(['A', 'B', 'C'], [('A', 'B'), ('B', 'C')])
    parsed = parse_reviews_rule_based([])
    p = tmp_path / "multi.db"
    build_sqlite(ds, parsed, p)
    conn = sqlite3.connect(p)

    rows = synergy_context_sql(conn, ['A', 'C'], hops=1)
    by_src = {}
    for src, node, polarity in rows:
        if src not in by_src:
            by_src[src] = set()
        by_src[src].add(node)

    assert by_src.get('A', set()) == {'B'}, f"From A, expected {{B}}, got {by_src.get('A')}"
    assert by_src.get('C', set()) == {'B'}, f"From C, expected {{B}}, got {by_src.get('C')}"

    conn.close()


# --------------------------------------------------------------------------
# 참조 무결성 (Task 2)
# --------------------------------------------------------------------------

def test_append_cowork_rejects_unknown_person_id(built):
    conn = built
    rec = CoworkRecord(a_id="p000", b_id="ghost", co_months=1, project_count=1)
    with pytest.raises(ValueError, match="ghost"):
        append_cowork(conn, rec)


def test_append_review_rejects_unknown_reviewer(built):
    conn = built
    review = PeerReview(reviewer_id="ghost", reviewee_id="p000",
                        positive=ReviewSection(items=["미상"], text=""),
                        negative=ReviewSection(items=["미상"], text=""))
    parsed = ParsedReview(reviewer_id="ghost", reviewee_id="p000", text_polarity=0.0)
    with pytest.raises(ValueError, match="ghost"):
        append_review(conn, review, parsed)
