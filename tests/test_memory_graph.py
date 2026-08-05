from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph, _item_score
from core.domain.models import PeerReview, ReviewSection

def _graph(n=30, j=6, seed=3):
    ds = generate_dataset(n, j, seed=seed)
    return ds, MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))

def _expected_item_score(review):
    """Local helper for testing: computes item score without importing production function."""
    p = len(review.positive.items)
    n = len(review.negative.items)
    return (p - n) / (p + n)

def _classify_review_pairs(reviews):
    """Extract bidirectional and unidirectional pairs from review list."""
    review_pairs = {}
    for r in reviews:
        pair = tuple(sorted([r.reviewer_id, r.reviewee_id]))
        if pair not in review_pairs:
            review_pairs[pair] = []
        review_pairs[pair].append((r.reviewer_id, r.reviewee_id))
    return review_pairs

def test_skill_levels_shape_and_values():
    ds, g = _graph()
    assert g.skill_levels.shape == (30, len(g.skill_index))
    p0 = ds.people[0]
    for s, lv in p0.skills.items():
        assert g.skill_levels[g.pid_index[p0.id], g.skill_index[s]] == lv

def test_cowork_symmetric():
    ds, g = _graph()
    c = ds.coworks[0]
    i, j = g.pid_index[c.a_id], g.pid_index[c.b_id]
    assert g.cowork_months[i, j] == c.co_months == g.cowork_months[j, i]

def test_pair_review_score_bounds_and_key_order():
    _, g = _graph()
    assert g.pair_review_score
    for (i, j), v in g.pair_review_score.items():
        assert i < j and -1.0 <= v <= 1.0

def test_pair_evidence_attribution_with_bidirectional_reviews():
    """Verify that pair_evidence tracks reviewer_id with each sentence."""
    ds = generate_dataset(30, 6, seed=1)  # seed=1 has bidirectional pairs
    parsed = parse_reviews_rule_based(ds.reviews)
    g = MemoryGraph.build(ds, parsed)

    # Find a bidirectional pair
    review_pairs = _classify_review_pairs(ds.reviews)

    bi_pair = None
    for pair, dirs in review_pairs.items():
        if len(set(dirs)) == 2:
            bi_pair = pair
            break

    assert bi_pair is not None, "No bidirectional pair found in seed=1"

    # Map to indices
    i, j = g.pid_index[bi_pair[0]], g.pid_index[bi_pair[1]]
    key = tuple(sorted([i, j]))

    # Check that both reviewers' evidence is present and attributed
    assert key in g.pair_evidence, f"No evidence for key {key}"
    evidence_list = g.pair_evidence[key]
    assert len(evidence_list) > 0, f"Empty evidence list for key {key}"

    # All evidence items should be tuples of (reviewer_id, sentence)
    reviewer_ids = set()
    for reviewer_id, sentence in evidence_list:
        assert isinstance(reviewer_id, str), f"reviewer_id should be str, got {type(reviewer_id)}"
        assert isinstance(sentence, str), f"sentence should be str, got {type(sentence)}"
        reviewer_ids.add(reviewer_id)

    # Should have evidence from both directions
    assert len(reviewer_ids) == 2, f"Expected 2 reviewers, got {len(reviewer_ids)}: {reviewer_ids}"
    assert bi_pair[0] in reviewer_ids and bi_pair[1] in reviewer_ids, \
        f"Both participants should be reviewers. Got {reviewer_ids}, expected {set(bi_pair)}"

def test_pair_review_score_formula_bidirectional():
    """Verify that pair_review_score correctly averages bidirectional reviews."""
    ds = generate_dataset(30, 6, seed=1)  # seed=1 has bidirectional pairs
    parsed = parse_reviews_rule_based(ds.reviews)
    g = MemoryGraph.build(ds, parsed)

    # Find a bidirectional pair
    review_pairs = _classify_review_pairs(ds.reviews)

    bi_pair = None
    for pair, dirs in review_pairs.items():
        if len(set(dirs)) == 2:
            bi_pair = pair
            break

    assert bi_pair is not None

    # Get the two reviews (both directions)
    reviews_dict = {(r.reviewer_id, r.reviewee_id): r for r in ds.reviews}
    rev_a = reviews_dict.get((bi_pair[0], bi_pair[1]))
    rev_b = reviews_dict.get((bi_pair[1], bi_pair[0]))
    assert rev_a is not None and rev_b is not None

    # Get the polarity values
    polarity_dict = {(p.reviewer_id, p.reviewee_id): p.text_polarity for p in parsed}
    pol_a = polarity_dict.get((bi_pair[0], bi_pair[1]), 0.0)
    pol_b = polarity_dict.get((bi_pair[1], bi_pair[0]), 0.0)

    # Compute expected scores using local helper (not production function)
    score_a = 0.5 * _expected_item_score(rev_a) + 0.5 * pol_a
    score_b = 0.5 * _expected_item_score(rev_b) + 0.5 * pol_b
    expected_score = (score_a + score_b) / 2.0

    # Get actual score
    i, j = g.pid_index[bi_pair[0]], g.pid_index[bi_pair[1]]
    key = tuple(sorted([i, j]))
    actual_score = g.pair_review_score[key]

    # Assert equality with tolerance
    assert abs(actual_score - expected_score) < 1e-9, \
        f"Score mismatch: actual={actual_score}, expected={expected_score}"

def test_pair_review_score_formula_unidirectional():
    """Verify that unidirectional reviews are scored correctly (not halved)."""
    ds = generate_dataset(30, 6, seed=1)
    parsed = parse_reviews_rule_based(ds.reviews)
    g = MemoryGraph.build(ds, parsed)

    # Find a unidirectional pair (only one direction exists)
    review_pairs = _classify_review_pairs(ds.reviews)

    # Get the one-directional review
    reviews_dict = {(r.reviewer_id, r.reviewee_id): r for r in ds.reviews}
    polarity_dict = {(p.reviewer_id, p.reviewee_id): p.text_polarity for p in parsed}

    # Try to find a truly unidirectional pair by checking both directions
    found_unidirectional = False
    for pair in review_pairs:
        rev_forward = reviews_dict.get((pair[0], pair[1]))
        rev_backward = reviews_dict.get((pair[1], pair[0]))

        if rev_forward and not rev_backward:
            # Found unidirectional pair: forward exists, backward doesn't
            score_forward = 0.5 * _expected_item_score(rev_forward) + \
                           0.5 * polarity_dict.get((pair[0], pair[1]), 0.0)
            expected = score_forward  # Should NOT be halved or averaged

            i, j = g.pid_index[pair[0]], g.pid_index[pair[1]]
            key = tuple(sorted([i, j]))
            actual = g.pair_review_score[key]

            assert abs(actual - expected) < 1e-9, \
                f"Unidirectional score mismatch: actual={actual}, expected={expected}"
            found_unidirectional = True
            break

        if rev_backward and not rev_forward:
            # Found unidirectional pair: backward exists, forward doesn't
            score_backward = 0.5 * _expected_item_score(rev_backward) + \
                            0.5 * polarity_dict.get((pair[1], pair[0]), 0.0)
            expected = score_backward

            i, j = g.pid_index[pair[0]], g.pid_index[pair[1]]
            key = tuple(sorted([i, j]))
            actual = g.pair_review_score[key]

            assert abs(actual - expected) < 1e-9, \
                f"Unidirectional score mismatch: actual={actual}, expected={expected}"
            found_unidirectional = True
            break

    assert found_unidirectional, "No unidirectional pair found in this seed"

def test_item_score_arithmetic_hand_computed():
    """Direct unit test of _item_score with literal hand-computed values."""
    # Create a review with 3 positive items and 1 negative item
    r = PeerReview(reviewer_id="a", reviewee_id="b",
                   positive=ReviewSection(items=["책임감", "소통", "전문성"], text="좋았습니다."),
                   negative=ReviewSection(items=["꼼꼼함"], text="아쉬웠습니다."))
    # Expected: (3 - 1) / (3 + 1) = 2 / 4 = 0.5
    expected = 0.5
    actual = _item_score(r)
    assert abs(actual - expected) < 1e-9, \
        f"Item score mismatch: actual={actual}, expected={expected}"

def _chain_graph():
    """A–B–C 체인 + 고립 노드 D. 인덱스: A=0,B=1,C=2,D=3"""
    from core.domain.models import CoworkRecord, Dataset, Grade, Person, HORIZON_MONTHS
    people = [Person(id=f"p{i:03d}", name=f"사람{i}", grade=Grade.MID, monthly_rate=1000,
                     skills={"Java": 3}, availability=[1.0] * HORIZON_MONTHS) for i in range(4)]
    coworks = [CoworkRecord(a_id="p000", b_id="p001", co_months=3, project_count=1),
               CoworkRecord(a_id="p001", b_id="p002", co_months=3, project_count=1)]
    ds = Dataset(people=people, projects=[], coworks=coworks, reviews=[])
    return ds, MemoryGraph.build(ds, [])

def test_memory_traversal_1hop():
    _, g = _chain_graph()
    rows = g.synergy_context_memory(["p000"], hops=1)
    assert {r[1] for r in rows} == {"p001"}
    assert all(r[0] == "p000" for r in rows)

def test_memory_traversal_2hop_reaches_transitively():
    _, g = _chain_graph()
    assert {r[1] for r in g.synergy_context_memory(["p000"], hops=2)} == {"p001", "p002"}

def test_memory_traversal_excludes_source_and_isolated():
    _, g = _chain_graph()
    nodes = {r[1] for r in g.synergy_context_memory(["p000"], hops=3)}
    assert "p000" not in nodes and "p003" not in nodes

def test_memory_traversal_multisource_keeps_src_separate():
    _, g = _chain_graph()
    rows = g.synergy_context_memory(["p000", "p002"], hops=1)
    by_src = {}
    for src, node, _ in rows:
        by_src.setdefault(src, set()).add(node)
    assert by_src == {"p000": {"p001"}, "p002": {"p001"}}

def test_memory_traversal_matches_sqlite_backend(tmp_path):
    """세 백엔드 공정성: 인메모리 결과가 SQLite와 동일한 도달 집합이어야 한다."""
    from core.datagen.generator import generate_dataset
    from core.datagen.parse_reviews import parse_reviews_rule_based
    from core.graph.sqlite_store import build_sqlite, synergy_context_sql
    import sqlite3
    ds = generate_dataset(60, 12, seed=11)
    parsed = parse_reviews_rule_based(ds.reviews)
    g = MemoryGraph.build(ds, parsed)
    db = tmp_path / "cmp.db"
    build_sqlite(ds, parsed, db)
    conn = sqlite3.connect(db)
    seeds = [p.id for p in ds.people[:5]]
    for hops in (1, 2, 3):
        mem = {(r[0], r[1]) for r in g.synergy_context_memory(seeds, hops)}
        sql = {(r[0], r[1]) for r in synergy_context_sql(conn, seeds, hops)}
        assert mem == sql, f"hops={hops} 도달 집합 불일치"
    conn.close()
