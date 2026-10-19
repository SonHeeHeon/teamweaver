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



def test_heuristic_keeps_a_current_team_that_is_exempt_from_the_familiar_pair_penalty():
    """Codex 리뷰(2026-10-19): 휴리스틱이 지금 같은 사업 팀원 예외를 몰라 기존 팀을 흩고 실제 점수를 낮췄다(3명·사업 1개 반례).
    p0·p1은 오래 함께 일했고 지금 j0에 함께 있다 -- 예외로 감점이 없으니 기술이 조금 낮은 p2로 바꾸면 손해다."""
    from dataclasses import replace

    import numpy as np
    from scipy.sparse import csr_matrix

    from core.domain.models import Grade, Project, ProjectPhase, Sector, SkillRequirement
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.milp import MilpParams
    from tests.phase0.factories import _graph, _person
    people = [_person(f"p{k}", Grade.MID) for k in range(3)]
    project = Project(id="j0", name="사업", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=0, end_month=2,
                      grade_headcount={Grade.MID: 2}, requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                      monthly_budget=100_000)
    cw = np.zeros((3, 3))
    cw[0, 1] = cw[1, 0] = 24
    graph = replace(_graph(people, [project]), cowork_months=csr_matrix(cw), _within_cache={},
                    current_teams=frozenset({(0, 1, 0)}))
    S, C = np.array([[1.0], [0.9], [0.85]]), np.zeros((3, 3))
    params = MilpParams(pair_keep_ratio=0.0, allocation_mode="full", mu=0.5)
    entries, _ = heuristic(graph, S, C, params, seconds=0.3, seed=1)
    assert {e.person_id for e in entries} == {"p0", "p1"}
    assert abs(evaluate_plan(graph, S, C, params, entries).objective.total - 1.9) < 1e-9
