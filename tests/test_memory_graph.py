import numpy as np
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph

def _graph(n=30, j=6, seed=3):
    ds = generate_dataset(n, j, seed=seed)
    return ds, MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))

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
