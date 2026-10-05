"""C6 측정 장치의 건전성: 월별판은 고정판의 해를 포함하므로 최적값이 고정판보다 낮을 수 없다."""
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine
from core.optimize.milp import solve_milp_diagnostic
from experiments.c6.monthly_alloc import solve_model


def test_monthly_relaxation_is_never_worse_than_fixed_allocation():
    ds = generate_dataset(12, 3, seed=5)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = MilpParams(min_alloc=0.3, gap=0.0, time_limit=60)
    fixed = solve_model(g, S, C, params, monthly=False)
    monthly = solve_model(g, S, C, params, monthly=True)
    assert monthly["status"] == "Optimal" and not monthly["time_limited"]
    assert monthly["objective"] >= fixed["objective"] - 1e-6
    # 고정판은 서비스 MILP와 같은 식이다 -- 같은 최적값.
    assert abs(fixed["objective"] - solve_milp_diagnostic(g, S, C, params).objective) < 1e-5
