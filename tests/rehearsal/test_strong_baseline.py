"""더 강한 비교 상대(rehearsal/strong_baseline): 제약을 지키는 배치를 내고, 국소 탐색은 점수를 낮추지 않는다."""
from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine
from rehearsal.strong_baseline import heuristic


def _setup():
    ds, parsed = load_fixtures(FIXTURES_DIR)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    return g, eng.skill_matrix({}), eng.synergy_matrix(), MilpParams(min_alloc=0.3, max_pairs=200)


def test_heuristic_respects_every_constraint():
    g, S, C, params = _setup()
    entries, stats = heuristic(g, S, C, params, seconds=0.5, seed=1)
    ev = evaluate_plan(g, S, C, params, entries)
    assert not ev.violations
    assert entries and stats["tries"] > 0


def test_local_search_does_not_lower_the_score():
    g, S, C, params = _setup()
    before, _ = heuristic(g, S, C, params, seconds=0.0, seed=1)
    after, _ = heuristic(g, S, C, params, seconds=0.5, seed=1)
    score = lambda e: evaluate_plan(g, S, C, params, e).objective.total   # noqa: E731
    assert score(after) >= score(before) - 1e-6
