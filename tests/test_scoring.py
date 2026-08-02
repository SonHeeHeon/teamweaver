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
