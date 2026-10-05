"""C2 [A-P1]: 대안은 실질적으로 다른, 쓸 만한 후보만 낸다. 없으면 억지로 채우지 않는다.

감사 재현: 한 사람만 가능한 팀에 22명 정원을 요구하면 대안 B·C·D가 모두 빈 팀으로 반복됐다
(같은 점수, 22명 미충원). 다양성 컷은 직전 플랜이 비어 있으면 아무것도 막지 못하고, 품질
하한(Plan A의 95%)은 A가 크게 음수일 때 미충원 1명 더(-100점)를 허용했다."""
import numpy as np
import pytest

from core.domain.models import Grade, Project, ProjectPhase, Sector, SkillRequirement
from core.optimize.alternatives import generate_plans, rejection_reason
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry, PlanAssignment
from tests.phase0.factories import _graph, _person


def _plan(pairs, objective=10.0, unfilled=(), label="B"):
    return PlanAssignment(entries=[AssignEntry(person_id=p, project_id=j, alloc=0.5) for p, j in pairs],
                          objective=objective, unfilled=list(unfilled), violations=[], label=label)


A = _plan([("p0", "j0"), ("p1", "j0")], objective=10.0, label="A")


@pytest.mark.parametrize("alt, reason", [
    (_plan([]), "empty"),                                            # 빈 팀
    (_plan([("p1", "j0"), ("p0", "j0")]), "duplicate"),              # 같은 구성(순서만 다름)
    (_plan([("p2", "j0")], objective=9.0), "quality"),               # A의 95% 미만
    (_plan([("p2", "j0")], objective=9.8, unfilled=["j0:중급:1명 미충원"]), "unfilled"),
    (_plan([("p2", "j0")], objective=9.8), None),                    # 통과
])
def test_rejection_reason(alt, reason):
    assert rejection_reason(alt, [A], A) == reason


def test_unfilled_limit_counts_people_not_lines():
    a = _plan([("p0", "j0")], objective=-100.0, unfilled=["j0:중급:1명 미충원"], label="A")
    same = _plan([("p1", "j0")], objective=-100.0, unfilled=["j0:중급:1명 미충원"])
    worse = _plan([("p1", "j1")], objective=-100.0, unfilled=["j0:중급:2명 미충원"])
    assert rejection_reason(same, [a], a) is None
    assert rejection_reason(worse, [a], a) == "unfilled"


def _one_feasible_person_fixture():
    """정원 22명(고급)인데 고급은 한 사람뿐이고 예산상 한 사람만 넣을 수 있다(감사 재현과 같은 모양).
    나머지는 정원에 없는 등급이라 넣어도 미충원이 줄지 않는다."""
    people = [_person("p0", Grade.SENIOR)] + [_person(f"p{i}", Grade.MID) for i in range(1, 4)]
    projects = [Project(id="j0", name="정원 과다", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                        start_month=0, end_month=0, grade_headcount={Grade.SENIOR: 22},
                        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                        monthly_budget=300)]
    return _graph(people, projects), np.array([[0.9], [0.8], [0.7], [0.6]]), np.zeros((4, 4))


def test_empty_repeated_alternatives_are_not_returned():
    graph, S, C = _one_feasible_person_fixture()
    plans = generate_plans(graph, S, C, MilpParams(pair_keep_ratio=0.0), n_alternatives=3)
    assert plans[0].entries, "Plan A는 한 사람을 넣는다"
    assert all(p.entries for p in plans), "빈 팀은 대안이 아니다"
    sigs = [frozenset(p.pairs()) for p in plans]
    assert len(sigs) == len(set(sigs)), "같은 구성은 한 번만"
    base = sum(int(u.split(":")[2].split("명")[0]) for u in plans[0].unfilled)
    assert all(sum(int(u.split(":")[2].split("명")[0]) for u in p.unfilled) <= base for p in plans)


def test_unfilled_cap_is_a_model_constraint_not_only_a_post_check():
    """미충원 상한을 모델 제약으로 넣는다(리뷰 SHOULD-1). 미충원 1명은 -100점이라 최적해가 일부러
    더 비우지는 않지만, gap 허용(5%)으로 멈춘 해는 그럴 수 있다 -- 그때 사후 거절만 하면 대안이
    끊긴다. 정원에 적힌 등급의 배치 합이 '총 정원 − A의 미충원' 이상이어야 한다."""
    import pulp
    from core.optimize.alternatives import _unfilled_cap
    graph, _, _ = _one_feasible_person_fixture()
    prob = pulp.LpProblem("t", pulp.LpMaximize)
    z = [[pulp.LpVariable(f"z_{i}_0", cat="Binary")] for i in range(4)]
    _unfilled_cap(graph, 21)(prob, z)
    (row,) = prob.constraints.values()
    assert {v.name for v in row} == {"z_0_0"}            # 정원 등급(고급) p0만 -- 정원 밖 중급은 빠진다
    assert row.sense == pulp.LpConstraintGE and -row.constant == 22 - 21


def test_unfilled_text_in_an_unknown_format_fails_loudly():
    bad = _plan([("p2", "j0")], objective=9.8, unfilled=["j0 중급 2명"])
    with pytest.raises(ValueError):
        rejection_reason(bad, [A], A)


def test_unfilled_cap_is_applied_only_when_quality_floor_allows_an_extra_unfilled(monkeypatch):
    """A가 크게 음수(감점 허용폭 ≥ 100점)일 때만 상한을 넣는다 -- 기본 데이터처럼 다 채운 경우
    품질 하한이 이미 막으므로 계산만 느려진다."""
    import core.optimize.alternatives as alt
    applied = []
    monkeypatch.setattr(alt, "_unfilled_cap", lambda graph, k: (applied.append(k) or (lambda prob, z: None)))
    graph, S, C = _one_feasible_person_fixture()             # A ≈ -2100 → 5%는 105점 ≥ 100
    alt.generate_plans(graph, S, C, MilpParams(pair_keep_ratio=0.0), n_alternatives=1)
    assert applied == [21]
    applied.clear()
    from tests.phase0.factories import one_project_fixture   # 다 채운다 → A가 양수
    alt.generate_plans(*one_project_fixture(), MilpParams(pair_keep_ratio=0.0), n_alternatives=1)
    assert applied == []


@pytest.mark.parametrize("message, reason, cache_ok", [
    ("MILP failed: Infeasible", "no_feasible_alternative", True),
    ("MILP found no incumbent solution within time_limit=5s (status=Not Solved) — cannot extract a plan",
     "time_limit", False),
    ("MILP returned an invalid incumbent candidate (status=Optimal; independent_validation_failed:budget; x)",
     "validation_rejected", False),
    ("MILP failed: Unbounded", "solver_failed", False),
])
def test_failure_reason_and_cacheability(message, reason, cache_ok):
    from core.optimize.alternatives import _failure_reason, cacheable
    assert _failure_reason(RuntimeError(message)) == reason
    assert cacheable({"stop_reason": reason}) is cache_ok


def test_no_alternative_under_the_cap_is_a_deterministic_stop():
    """상한·다양성 컷 아래 해가 없는 것은 '조건을 만족하는 대안 없음'이다 -- 시간 초과처럼 다루면
    캐시하지 않고 화면도 "다시 실행하면 나올 수 있다"고 잘못 안내했다(C2 2차 리뷰 SHOULD-1)."""
    from core.optimize.alternatives import cacheable
    graph, S, C = _one_feasible_person_fixture()
    outcome = {}
    plans = generate_plans(graph, S, C, MilpParams(pair_keep_ratio=0.0), n_alternatives=3, outcome=outcome)
    assert [p.label for p in plans] == ["A"]
    assert outcome.get("stop_reason") in {"no_feasible_alternative", "empty", "unfilled", "quality"}
    assert cacheable(outcome)


@pytest.mark.slow
def test_time_limited_incumbent_is_marked_not_cacheable():
    """PuLP는 시간 한도에 걸린 CBC 해도 status "Optimal"로 준다(sol_status만 IntegerFeasible) --
    종료 사유로 구분해 캐시하지 않는다(C2 2차 리뷰 MUST). 기본 fixture에서 gap 0·6초는 한도에 걸린다."""
    from core.config import FIXTURES_DIR
    from core.datagen.fixtures_io import load_fixtures
    from core.graph.memory_graph import MemoryGraph
    from core.optimize.alternatives import cacheable
    from core.scoring.engine import ScoringEngine
    ds, parsed = load_fixtures(FIXTURES_DIR)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    outcome = {}
    try:
        generate_plans(g, eng.skill_matrix({}), eng.synergy_matrix(),
                       MilpParams(time_limit=6, gap=0.0), n_alternatives=1, outcome=outcome)
    except RuntimeError:
        pytest.skip("Plan A가 6초 안에 해를 못 찾음(부하) -- 이 경우는 time_limited와 무관")
    assert outcome.get("time_limited") is True
    assert not cacheable(outcome)
