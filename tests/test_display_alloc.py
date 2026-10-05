"""C3 [A-P2]: 표시(반환)용 투입률이 해 값·가용률 위로 올라가지 않는다.

예전 규칙 `round(max(min_alloc, floor2(v)), 2)`은 min_alloc이 소수 둘째 자리보다 정밀하면
(예 0.205) 내림한 값이 최소값 아래로 떨어져 끌어올린 뒤 다시 반올림해, 해 값 0.206을 0.21로
돌려줬다 -- 가용률 0.206인 사람에게 더 많은 일을 맡긴 것처럼 보인다. greedy 기준안은 남은
가용률을 그대로 반올림해 같은 일이 났다."""
from collections import defaultdict

import pytest

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.optimize.greedy import solve_greedy
from core.optimize.milp import MilpParams, display_alloc, solve_milp
from core.optimize.validation import _display_alloc as validator_display_alloc
from core.scoring.engine import ScoringEngine

AVAIL = 0.206


def _graph_with_availability(value: float, n=25, j=5, seed=3):
    ds = generate_dataset(n, j, seed=seed)
    ds = ds.model_copy(update={"people": [p.model_copy(update={"availability": [value] * len(p.availability)})
                                          for p in ds.people]})
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    return ds, g, eng.skill_matrix({}), eng.synergy_matrix()


@pytest.mark.parametrize("display", [display_alloc, validator_display_alloc],
                         ids=["service", "independent-validator"])
@pytest.mark.parametrize("value, min_alloc, expected", [
    (0.239, 0.2, 0.23),            # 2자리 내림이 최소값 이상이면 그대로(기존 동작)
    (0.5, 0.3, 0.5),
    (0.206, 0.205, 0.206),         # 2자리 내림(0.20)이 최소값 아래 -> 해 값을 정밀하게 둔다
    (0.205, 0.205, 0.205),
    (0.1999995, 0.2, 0.2),         # 솔버가 최소값 바로 아래(허용오차 안)에 둔 값은 최소값으로
])
def test_display_alloc_stays_between_min_alloc_and_the_solved_value(display, value, min_alloc, expected):
    shown = display(value, min_alloc)
    assert shown == pytest.approx(expected, abs=1e-9)
    assert shown <= value + 1e-6                    # 해 값 위로 올리지 않는다(검증 허용오차 1e-6 안)
    assert shown >= min_alloc - 1e-9


def _load(ds, entries):
    months = {p.id: p.months for p in ds.projects}
    load = defaultdict(float)
    for e in entries:
        for m in months[e.project_id]:
            load[(e.person_id, m)] += e.alloc
    return load


def test_greedy_never_rounds_allocation_above_availability():
    ds, g, S, _ = _graph_with_availability(AVAIL)
    plan = solve_greedy(g, S)
    assert plan.entries, "가용률 0.206이면 greedy도 사람을 넣는다(최소 0.2 이상)"
    assert all(v <= AVAIL + 1e-9 for v in _load(ds, plan.entries).values())


def test_milp_plan_allocation_never_exceeds_availability_with_fine_min_alloc():
    ds, g, S, C = _graph_with_availability(AVAIL)
    plan = solve_milp(g, S, C, MilpParams(min_alloc=0.205, time_limit=30))
    assert plan.entries
    assert all(e.alloc >= 0.205 - 1e-9 for e in plan.entries)
    assert all(v <= AVAIL + 1e-6 for v in _load(ds, plan.entries).values())
