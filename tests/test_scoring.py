import numpy as np
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine

def _eng(n=30, j=6, seed=3):
    ds = generate_dataset(n, j, seed=seed)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    return ds, g, ScoringEngine(g)

def test_skill_matrix_hand_case():
    ds, g, eng = _eng()
    S = eng.skill_matrix({})            # 전 스킬 가중치 3
    i, j = 0, 0
    person, proj = ds.people[i], ds.projects[j]
    num = den = 0.0
    for rq in proj.requirements:
        w = 3.0
        lv = person.skills.get(rq.skill, 0)
        num += w * min(lv / rq.min_level, 1.0); den += w
    assert abs(S[i, j] - num / den) < 1e-9

def test_skill_matrix_bounds_and_weight_effect():
    ds, g, eng = _eng()
    S3 = eng.skill_matrix({})
    assert S3.min() >= 0.0 and S3.max() <= 1.0
    target = ds.projects[0].requirements[0].skill
    S5 = eng.skill_matrix({target: 5})
    assert not np.allclose(S3[:, 0], S5[:, 0])   # 가중치 변경이 점수를 바꾼다

def test_synergy_symmetric_bounded():
    _, g, eng = _eng()
    C = eng.synergy_matrix()
    assert np.allclose(C, C.T) and np.all(np.diag(C) == 0)
    assert C.min() >= -1.0 and C.max() <= 1.0

def test_synergy_matrix_hand_computed_pair():
    ds, g, eng = _eng()
    # Find a pair with nonzero cowork_months
    (i, j), review_score = next(iter(g.pair_review_score.items()))
    co_months = g.cowork_months[i, j]
    # Hand-compute expected value
    expected = 0.4 * min(co_months / 12.0, 1.0) + 0.6 * review_score
    C = eng.synergy_matrix()
    assert abs(C[i, j] - expected) < 1e-9
    assert abs(C[j, i] - expected) < 1e-9

def test_synergy_matrix_respects_alpha_beta():
    ds, g, _ = _eng()
    from core.scoring.engine import ScoringEngine
    (i, j), review_score = next(iter(g.pair_review_score.items()))
    co = min(g.cowork_months[i, j] / 12.0, 1.0)
    swapped = ScoringEngine(g, alpha=0.6, beta=0.4).synergy_matrix()
    assert abs(swapped[i, j] - (0.6 * co + 0.4 * review_score)) < 1e-9
