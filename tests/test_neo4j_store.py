import sqlite3

import pytest
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.neo4j_store import get_driver, load_neo4j, synergy_context_cypher
from core.graph.sqlite_store import build_sqlite, synergy_context_sql

pytestmark = pytest.mark.neo4j

@pytest.fixture(scope="module")
def loaded():
    ds = generate_dataset(30, 6, seed=3)
    driver = get_driver()
    load_neo4j(driver, ds, parse_reviews_rule_based(ds.reviews))
    yield ds, driver
    driver.close()

def _bfs_reach(coworks, start, hops):
    """Nodes reachable from start within <=hops steps of the undirected cowork graph."""
    adj = {}
    for c in coworks:
        adj.setdefault(c.a_id, set()).add(c.b_id)
        adj.setdefault(c.b_id, set()).add(c.a_id)
    visited = {start}
    frontier = {start}
    for _ in range(hops):
        nxt = set()
        for n in frontier:
            nxt |= adj.get(n, set())
        nxt -= visited
        visited |= nxt
        frontier = nxt
    visited.discard(start)
    return visited

def test_node_counts(loaded):
    ds, driver = loaded
    with driver.session() as s:
        n = s.run("MATCH (p:Person) RETURN count(p) AS n").single()["n"]
    assert n == 30

def test_synergy_context_1hop(loaded):
    ds, driver = loaded
    parsed = parse_reviews_rule_based(ds.reviews)
    pol_by_reviewee = {}
    for pr in parsed:
        pol_by_reviewee.setdefault(pr.reviewee_id, []).append(pr.text_polarity)

    pid = ds.coworks[0].a_id
    rows = synergy_context_cypher(driver, [pid], hops=1)
    direct = {c.b_id if c.a_id == pid else c.a_id
              for c in ds.coworks if pid in (c.a_id, c.b_id)}
    assert {r["other"] for r in rows} == direct
    assert all(r["src"] == pid for r in rows)
    for r in rows:
        polarities = pol_by_reviewee.get(r["other"])
        expected = sum(polarities) / len(polarities) if polarities else None
        if expected is None:
            assert r["avg_polarity"] is None
        else:
            assert r["avg_polarity"] == pytest.approx(expected)

def test_synergy_context_2hop(loaded):
    ds, driver = loaded
    pid = ds.coworks[0].a_id
    rows = synergy_context_cypher(driver, [pid], hops=2)
    expected = _bfs_reach(ds.coworks, pid, 2)
    assert {r["other"] for r in rows} == expected
    assert all(r["src"] == pid for r in rows)

def test_has_skill_edges(loaded):
    ds, driver = loaded
    total_skills = sum(len(p.skills) for p in ds.people)
    with driver.session() as s:
        n = s.run("MATCH (:Person)-[h:HAS_SKILL]->(:Skill) RETURN count(h) AS n").single()["n"]
    assert n == total_skills

    person = ds.people[0]
    with driver.session() as s:
        rows = s.run(
            "MATCH (p:Person {id: $pid})-[h:HAS_SKILL]->(k:Skill) "
            "RETURN k.name AS skill, h.level AS level", pid=person.id)
        levels = {r["skill"]: r["level"] for r in rows}
    assert levels == person.skills

def test_requires_edges(loaded):
    ds, driver = loaded
    total_reqs = sum(len(j.requirements) for j in ds.projects)
    with driver.session() as s:
        n = s.run("MATCH (:Project)-[q:REQUIRES]->(:Skill) RETURN count(q) AS n").single()["n"]
    assert n == total_reqs

    project = ds.projects[0]
    with driver.session() as s:
        rows = s.run(
            "MATCH (j:Project {id: $jid})-[q:REQUIRES]->(k:Skill) "
            "RETURN k.name AS skill, q.min_level AS min_level, q.headcount AS headcount",
            jid=project.id)
        got = {r["skill"]: (r["min_level"], r["headcount"]) for r in rows}
    expected = {r.skill: (r.min_level, r.headcount) for r in project.requirements}
    assert got == expected

def test_reviewed_edges(loaded):
    ds, driver = loaded
    parsed = parse_reviews_rule_based(ds.reviews)
    pol = {(pr.reviewer_id, pr.reviewee_id): pr for pr in parsed}

    with driver.session() as s:
        n = s.run("MATCH (:Person)-[v:REVIEWED]->(:Person) RETURN count(v) AS n").single()["n"]
    assert n == len(ds.reviews)

    review = ds.reviews[0]
    expected_parsed = pol[(review.reviewer_id, review.reviewee_id)]
    with driver.session() as s:
        row = s.run(
            "MATCH (a:Person {id: $rv})-[v:REVIEWED]->(b:Person {id: $re}) "
            "RETURN v.pos_items AS pos, v.neg_items AS neg, v.polarity AS pol",
            rv=review.reviewer_id, re=review.reviewee_id).single()
    assert row["pos"] == review.positive.items
    assert row["neg"] == review.negative.items
    assert row["pol"] == pytest.approx(expected_parsed.text_polarity)

def test_cross_backend_node_sets_match(loaded, tmp_path):
    """synergy_context_sql (SQLite) and synergy_context_cypher (Neo4j) must agree on the
    logical set of reachable people for the same (source, hops) -- the fairness precondition
    for Experiment 1's cross-backend comparison."""
    ds, driver = loaded
    parsed = parse_reviews_rule_based(ds.reviews)
    db_path = tmp_path / "cross.db"
    build_sqlite(ds, parsed, db_path)
    conn = sqlite3.connect(db_path)
    try:
        sample_ids = [p.id for p in ds.people[:5]]
        for hops in (1, 2, 3):
            for pid in sample_ids:
                sql_other = {row[1] for row in synergy_context_sql(conn, [pid], hops=hops)}
                cy_other = {row["other"] for row in synergy_context_cypher(driver, [pid], hops=hops)}
                assert cy_other == sql_other, (
                    f"node set mismatch pid={pid} hops={hops}: "
                    f"sql={sql_other} cypher={cy_other}")
    finally:
        conn.close()
