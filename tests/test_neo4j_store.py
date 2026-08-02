import pytest
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.neo4j_store import get_driver, load_neo4j, synergy_context_cypher

pytestmark = pytest.mark.neo4j

@pytest.fixture(scope="module")
def loaded():
    ds = generate_dataset(30, 6, seed=3)
    driver = get_driver()
    load_neo4j(driver, ds, parse_reviews_rule_based(ds.reviews))
    yield ds, driver
    driver.close()

def test_node_counts(loaded):
    ds, driver = loaded
    with driver.session() as s:
        n = s.run("MATCH (p:Person) RETURN count(p) AS n").single()["n"]
    assert n == 30

def test_synergy_context_1hop(loaded):
    ds, driver = loaded
    pid = ds.coworks[0].a_id
    rows = synergy_context_cypher(driver, [pid], hops=1)
    direct = {c.b_id if c.a_id == pid else c.a_id
              for c in ds.coworks if pid in (c.a_id, c.b_id)}
    assert {r["other"] for r in rows} == direct
