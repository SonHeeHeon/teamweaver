import pytest
from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine
from core.optimize.milp import MilpParams
from core.optimize.alternatives import generate_plans
from core.optimize.metrics import matching_fulfillment, optimization_ratio

pytestmark = pytest.mark.slow

def test_frozen_fixture_end_to_end():
    assert (FIXTURES_DIR / "people.json").exists(), "fixture 동결이 선행되어야 함"
    ds, parsed = load_fixtures(FIXTURES_DIR)
    assert len(ds.people) == 100 and len(ds.projects) == 20
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = MilpParams(time_limit=180)
    plans = generate_plans(g, S, C, params, n_alternatives=3)
    assert len(plans) >= 4, "Plan A + 유효 대안 3개 (스펙 목표)"

    plan_a = plans[0]
    ratio = optimization_ratio(g, S, plan_a, params)
    fulfillment = matching_fulfillment(g, plan_a, {})  # 보조 지표, 정보성 출력만
    print(f"\n[e2e] optimization_ratio={ratio:.4f} matching_fulfillment={fulfillment:.4f} "
          f"unfilled={len(plan_a.unfilled)}")

    assert ratio >= 0.90, f"최적화율 {ratio:.3f} < 0.90"
    assert ratio <= 1.0 + 1e-6, f"최적화율 {ratio:.3f}이 이론적 상한 1.0을 초과함(UB 산출 결함 의심)"
    assert len(plan_a.unfilled) == 0, f"필수 정원 미충원 슬롯 존재: {plan_a.unfilled}"
