"""월별 투입률(사용자 결정 2026-10-05): 프로젝트 진행 달마다 투입률이 달라질 수 있다.

설명에 쓴 예와 같은 모양: 한 사람(가용률 100%)에게 Y(1~3월)와 X(1~6월)가 있고 둘 다 맞는 사람이다.
고정 투입률이면 1~3월에 Y와 X를 나눠 쓴 비율을 4~6월에도 그대로 써야 해 Y가 끝난 뒤 시간이 논다.
월별이면 1~3월은 Y 위주, 4~6월은 X 100%가 된다."""
from dataclasses import replace

import numpy as np
import pytest

from core.domain.models import Grade, Project, ProjectPhase, Sector, SkillRequirement
from core.optimize.milp import MilpParams, solve_milp_diagnostic
from core.optimize.validation import validate_raw_solution
from experiments.phase0.oracle import solve_tiny_oracle
from tests.phase0.factories import _graph, _person


def _two_projects(budget=10_000):
    people = [_person("p0", Grade.MID)]
    mk = lambda pid, start, end: Project(
        id=pid, name=pid, sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=start, end_month=end,
        grade_headcount={Grade.MID: 1}, requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
        monthly_budget=budget)
    return _graph(people, [mk("Y", 0, 2), mk("X", 0, 5)]), np.array([[0.9, 0.9]]), np.zeros((1, 1))


P = dict(pair_keep_ratio=0.0, min_alloc=0.2, gap=0.0)


def test_monthly_uses_the_freed_months_and_scores_higher():
    g, S, C = _two_projects()
    fixed = solve_milp_diagnostic(g, S, C, MilpParams(**P))
    monthly = solve_milp_diagnostic(g, S, C, MilpParams(**P, allocation_mode="monthly"))
    assert monthly.objective > fixed.objective + 0.3                   # 0.9 → 1.26
    x = next(e for e in monthly.plan.entries if e.project_id == "X")
    assert x.monthly_alloc is not None
    assert [x.monthly_alloc[m] for m in range(3, 6)] == [1.0, 1.0, 1.0]     # Y가 끝난 4~6월은 X에 전부
    assert all(x.monthly_alloc[m] <= 0.8 for m in range(3))
    assert x.alloc == pytest.approx(sum(x.monthly_alloc.values()) / 6, abs=1e-6)
    assert validate_raw_solution(g, S, C, MilpParams(**P, allocation_mode="monthly"), monthly).valid


def test_entries_without_monthly_variation_stay_plain():
    """달마다 같은 값이면 고정 항목과 똑같이 낸다(monthly_alloc 없음) -- 기존 화면·저장과 호환."""
    g, S, C = _two_projects()
    monthly = solve_milp_diagnostic(g, S, C, MilpParams(**P, allocation_mode="monthly"))
    y = next(e for e in monthly.plan.entries if e.project_id == "Y")
    assert y.monthly_alloc is None and y.alloc == pytest.approx(0.8)


def test_monthly_matches_the_independent_oracle():
    g, S, C = _two_projects(budget=700)                                 # 예산도 달마다 걸리게(700 < 단가 1000)
    params = MilpParams(**P, allocation_mode="monthly")
    oracle = solve_tiny_oracle(g, S, C, params)
    milp = solve_milp_diagnostic(g, S, C, params)
    assert milp.objective == pytest.approx(oracle.objective, abs=1e-6)


def test_validator_checks_each_month_and_the_mean():
    g, S, C = _two_projects()
    params = MilpParams(**P, allocation_mode="monthly")
    raw = solve_milp_diagnostic(g, S, C, params)
    # 4월(달 3)에 X를 0.5 더 올리면 그달 가용률을 넘는다 -- 평균만 보면 못 잡는 위반.
    over = dict(raw.a_month)
    y_idx, x_idx = 0, 1
    over[(0, y_idx, 0)] = 1.0
    bad = replace(raw, a_month=over)
    codes = {i.code for i in validate_raw_solution(g, S, C, params, bad).issues}
    assert "availability" in codes and "monthly_mean" in codes


def test_fixed_mode_is_unchanged():
    """기본(fixed)은 이전 모델과 같은 해·같은 항목 -- monthly_alloc이 생기지 않는다."""
    g, S, C = _two_projects()
    raw = solve_milp_diagnostic(g, S, C, MilpParams(**P))
    assert raw.a_month is None and all(e.monthly_alloc is None for e in raw.plan.entries)


def test_c1_refinement_repairs_a_tiny_budget_residue_in_one_month():
    """C1 보정(예산 극미세 초과만 고정팀 LP로 고침)이 달별 변수에서도 그달만 고친다."""
    from core.optimize.candidate import rebuild_plan
    from core.optimize.numerics import NumericalPolicy, assess_candidate
    g, S, C = _two_projects(budget=700)
    params = MilpParams(**P, allocation_mode="monthly")
    raw = solve_milp_diagnostic(g, S, C, params)
    key = max((k for k in raw.a_month if raw.a_month[k] > 0.5), key=lambda k: raw.a_month[k])
    tampered = dict(raw.a_month)
    tampered[key] += 2.5e-9                        # 단가 1000 × 2.5e-9 = 2.5e-6 초과(그 한 달만)
    noisy = rebuild_plan(g, params, raw, allocations=tampered)
    assert {i.code for i in validate_raw_solution(g, S, C, params, noisy).issues} == {"budget"}
    res = assess_candidate(g, S, C, params, noisy, native_capture=noisy, policy=NumericalPolicy(enabled=True))
    assert res.refinement.reason == "REFINED", res.refinement.reason
    assert res.accepted.a_month[key] <= raw.a_month[key] + 1e-12
    assert validate_raw_solution(g, S, C, params, res.accepted).valid


def test_optimization_ratio_stays_within_one_for_monthly_plans():
    """분모(LP 완화 상한)도 월별 정식으로 계산한다 -- 고정판 상한을 쓰면 140%가 나왔다(리뷰 M1)."""
    from core.optimize.metrics import _skill_relaxation_upper_bound
    g, S, C = _two_projects()
    params = MilpParams(**P, allocation_mode="monthly")
    raw = solve_milp_diagnostic(g, S, C, params)
    skill = sum(S[g.pid_index[e.person_id], g.project_index[e.project_id]] * e.alloc for e in raw.plan.entries)
    ub = _skill_relaxation_upper_bound(g, S, params)
    assert skill <= ub + 1e-6 and skill / ub == pytest.approx(1.0, abs=1e-6)


def test_monthly_plan_has_no_false_violations_in_report_metrics():
    """정상 월별 플랜이 '가용률 위반'으로 보이지 않는다(평가기가 달별로 검사, 리뷰 M2)."""
    from api.routes.plans import roster_metrics
    g, S, C = _two_projects()
    params = MilpParams(**P, allocation_mode="monthly")
    raw = solve_milp_diagnostic(g, S, C, params)
    m = roster_metrics(g, S, C, params, {}, raw.plan.entries)
    assert m["violations"] == [] and m["optimization_ratio"] == pytest.approx(1.0, abs=1e-6)


def test_monthly_recheck_catches_overloads_that_the_mean_hides():
    """평균으로는 가려지는 위반을 달별 검사가 잡는다(리뷰 2차 S3): 가용률은 X 평균 0.2지만 1월 0.5,
    예산은 X 평균 0.6이지만 1월 0.9(예산 700)."""
    from api.routes.plans import roster_metrics
    from core.optimize.types import AssignEntry
    g, S, C = _two_projects()
    params = MilpParams(**P, allocation_mode="monthly")
    entries = [AssignEntry(person_id="p0", project_id="Y", alloc=0.8),
               AssignEntry(person_id="p0", project_id="X", alloc=0.2,
                           monthly_alloc={0: 0.5, 1: 0.2, 2: 0.2, 3: 0.1, 4: 0.1, 5: 0.1})]
    m = roster_metrics(g, S, C, params, {}, entries)
    assert any("1번째 달" in v and "가용률" in v for v in m["violations"])     # 평균(0.8+0.2=1.0)으로는 못 잡음
    g2, S2, C2 = _two_projects(budget=700)
    entries2 = [AssignEntry(person_id="p0", project_id="X", alloc=0.6,
                            monthly_alloc={0: 0.9, 1: 0.3, 2: 0.3, 3: 0.9, 4: 0.6, 5: 0.6})]
    m2 = roster_metrics(g2, S2, C2, params, {}, entries2)
    assert any("1번째 달 비용" in v for v in m2["violations"])                  # 평균 600 < 700이라 평균으론 못 잡음


def test_ratio_uses_the_monthly_bound_when_a_fixed_plan_has_monthly_entries():
    """고정 방식 플랜에 사람별 달별 조정이 들어와도(설정은 fixed) 분모를 월별 상한으로 써서 1을 넘지 않는다."""
    from api.routes.plans import roster_metrics
    g, S, C = _two_projects()
    raw = solve_milp_diagnostic(g, S, C, MilpParams(**P, allocation_mode="monthly"))
    m = roster_metrics(g, S, C, MilpParams(**P), {}, raw.plan.entries)          # 월별 명단 + fixed 기준
    assert m["optimization_ratio"] == pytest.approx(1.0, abs=1e-6)


def test_monthly_entries_must_cover_the_project_months_and_match_the_mean():
    """잘못된 월별 입력(진행 달과 다름·평균 불일치)은 평가에서 거절된다 -- API는 이를 422로 낸다."""
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import AssignEntry
    g, S, C = _two_projects()
    params = MilpParams(**P, allocation_mode="monthly")
    with pytest.raises(ValueError, match="months"):
        evaluate_plan(g, S, C, params, [AssignEntry(person_id="p0", project_id="Y", alloc=0.5,
                                                    monthly_alloc={0: 0.5, 5: 0.5})])
    with pytest.raises(ValueError, match="mean"):
        evaluate_plan(g, S, C, params, [AssignEntry(person_id="p0", project_id="Y", alloc=0.9,
                                                    monthly_alloc={0: 0.5, 1: 0.5, 2: 0.5})])


def test_monthly_alternatives_are_generated_and_validated():
    """다양성 컷(추가 행)이 (i, j, m) 투입률 변수로 투영돼도 대안이 만들어지고 검증을 통과한다."""
    from core.datagen.generator import generate_dataset
    from core.datagen.parse_reviews import parse_reviews_rule_based
    from core.graph.memory_graph import MemoryGraph
    from core.optimize.alternatives import generate_plans
    from core.scoring.engine import ScoringEngine
    ds = generate_dataset(15, 3, seed=1)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    plans = generate_plans(g, eng.skill_matrix({}), eng.synergy_matrix(),
                           MilpParams(allocation_mode="monthly", time_limit=20), 2)
    assert len(plans) >= 2
