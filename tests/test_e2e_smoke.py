import pytest
from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine
from core.optimize.greedy import solve_greedy
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
    milp_ratio = optimization_ratio(g, S, plan_a, params)
    fulfillment = matching_fulfillment(g, plan_a, {})  # 보조 지표, 정보성 출력만

    # Greedy vs MILP를 동일 frozen fixture·동일 metric으로 나란히 비교 -- "메트릭을
    # 통과하도록 정의한 것 아니냐"는 질문에 대한 가장 직접적인 답. Greedy는 solver
    # 없이 탐욕적으로 배정하므로 MILP보다 낮은 optimization_ratio를 내야 하고, 이
    # 격차가 사라지면(metric이 더 이상 구별하지 못하면) 회귀로 잡아낸다.
    greedy_plan = solve_greedy(g, S)
    greedy_ratio = optimization_ratio(g, S, greedy_plan, params)

    print(f"\n[e2e] milp_ratio={milp_ratio:.4f} greedy_ratio={greedy_ratio:.4f} "
          f"matching_fulfillment={fulfillment:.4f} unfilled={len(plan_a.unfilled)}")

    assert milp_ratio >= 0.90, f"최적화율 {milp_ratio:.3f} < 0.90"
    assert milp_ratio <= 1.0 + 1e-6, f"최적화율 {milp_ratio:.3f}이 이론적 상한 1.0을 초과함(UB 산출 결함 의심)"
    assert len(plan_a.unfilled) == 0, f"필수 정원 미충원 슬롯 존재: {plan_a.unfilled}"
    assert milp_ratio > greedy_ratio, (
        f"MILP({milp_ratio:.4f})가 Greedy({greedy_ratio:.4f})를 능가하지 못함 -- "
        "optimization_ratio가 더 이상 두 방식을 구별하지 못하는 회귀")
