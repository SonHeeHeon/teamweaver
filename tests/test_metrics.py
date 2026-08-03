from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine
from core.optimize.milp import MilpParams, solve_milp
from core.optimize.types import AssignEntry, PlanAssignment
from core.optimize.metrics import matching_fulfillment, optimization_ratio


def _graph():
    ds = generate_dataset(20, 3, seed=9)
    return ds, MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))


def test_empty_plan_zero():
    _, g = _graph()
    plan = PlanAssignment(entries=[], objective=0.0, unfilled=[], violations=[])
    assert matching_fulfillment(g, plan, {}) == 0.0


def test_perfect_single_slot():
    ds, g = _graph()
    proj = ds.projects[0]
    rq = proj.requirements[0]
    qualified = [p for p in ds.people if p.skills.get(rq.skill, 0) >= rq.min_level]
    entries = [AssignEntry(person_id=p.id, project_id=proj.id, alloc=1.0)
               for p in qualified[: rq.headcount]]
    plan = PlanAssignment(entries=entries, objective=0.0, unfilled=[], violations=[])
    # 이 슬롯만 보면 충족도 1.0, 전체는 다른 슬롯이 0이므로 (0,1) 사이
    v = matching_fulfillment(g, plan, {})
    assert 0.0 < v < 1.0


def test_bounds():
    _, g = _graph()
    plan = PlanAssignment(entries=[], objective=0.0, unfilled=[], violations=[])
    assert 0.0 <= matching_fulfillment(g, plan, {"Java": 5}) <= 1.0


def test_hand_computed_two_people_two_requirements():
    """Hand-computed test with fully controlled input.

    Scenario:
    - 2 people: p1 (Java=3), p2 (Java=4)
    - 1 project with 2 requirements:
      - Req1: Java, min_level=3, headcount=1
      - Req2: Java, min_level=4, headcount=1
    - Assign only p1 to project with alloc=1.0

    Expected calculation:
    - Req1: p1 qualifies (3>=3), got=1.0, h=1, f_1 = min(1, 1.0/1) = 1.0
    - Req2: p1 not qualified (3<4), got=0.0, h=1, f_2 = min(1, 0.0/1) = 0.0
    - w_1 = 3.0 (default), w_2 = 3.0 (default)
    - num = 3.0*1.0 + 3.0*0.0 = 3.0
    - den = 3.0 + 3.0 = 6.0
    - result = 3.0 / 6.0 = 0.5
    """
    from core.domain.models import (
        Dataset, Person, Project, SkillRequirement, Grade, Sector, ProjectPhase
    )

    # Create 2 people
    people = [
        Person(
            id="p1", name="Person1", grade=Grade.MID,
            monthly_rate=5000,
            skills={"Java": 3},
            availability=[1.0] * 6
        ),
        Person(
            id="p2", name="Person2", grade=Grade.MID,
            monthly_rate=5000,
            skills={"Java": 4},
            availability=[1.0] * 6
        ),
    ]

    # Create 1 project with 2 requirements
    projects = [
        Project(
            id="proj1", name="Project1", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
            start_month=0, end_month=2,
            grade_headcount={Grade.MID: 2},
            requirements=[
                SkillRequirement(skill="Java", min_level=3, headcount=1),
                SkillRequirement(skill="Java", min_level=4, headcount=1),
            ],
            monthly_budget=100000
        )
    ]

    # Build dataset and graph
    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    g = MemoryGraph.build(ds, [])

    # Assign only p1 with alloc=1.0
    entries = [AssignEntry(person_id="p1", project_id="proj1", alloc=1.0)]
    plan = PlanAssignment(entries=entries, objective=0.0, unfilled=[], violations=[])

    # Calculate and verify
    result = matching_fulfillment(g, plan, {})
    assert result == 0.5, f"Expected 0.5, got {result}"


def test_hand_computed_weights_change_result():
    """Verify that different weights for skills change the matching fulfillment.

    Scenario:
    - 1 person: p1 (Java=3, Python=2)
    - 1 project with 2 requirements (different skills):
      - Req1: Java, min_level=3, headcount=1
      - Req2: Python, min_level=3, headcount=1
    - Assign p1 with alloc=1.0

    Expected calculations:
    With default weights (3.0 for both):
    - Req1 (Java): p1 qualifies (3>=3), got=1.0, f_1 = 1.0, w_1 = 3.0
    - Req2 (Python): p1 not qualified (2<3), got=0.0, f_2 = 0.0, w_2 = 3.0
    - num = 3.0*1.0 + 3.0*0.0 = 3.0
    - den = 3.0 + 3.0 = 6.0
    - result_default = 3.0 / 6.0 = 0.5

    With custom weights (Java=1, Python=9):
    - Req1 (Java): w_1 = 1.0
    - Req2 (Python): w_2 = 9.0
    - num = 1.0*1.0 + 9.0*0.0 = 1.0
    - den = 1.0 + 9.0 = 10.0
    - result_custom = 1.0 / 10.0 = 0.1
    """
    from core.domain.models import (
        Dataset, Person, Project, SkillRequirement, Grade, Sector, ProjectPhase
    )

    people = [
        Person(
            id="p1", name="Person1", grade=Grade.MID,
            monthly_rate=5000,
            skills={"Java": 3, "Python": 2},
            availability=[1.0] * 6
        ),
    ]

    projects = [
        Project(
            id="proj1", name="Project1", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
            start_month=0, end_month=2,
            grade_headcount={Grade.MID: 1},
            requirements=[
                SkillRequirement(skill="Java", min_level=3, headcount=1),
                SkillRequirement(skill="Python", min_level=3, headcount=1),
            ],
            monthly_budget=100000
        )
    ]

    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    g = MemoryGraph.build(ds, [])

    entries = [AssignEntry(person_id="p1", project_id="proj1", alloc=1.0)]
    plan = PlanAssignment(entries=entries, objective=0.0, unfilled=[], violations=[])

    # With default weights: 3.0 for both
    result_default = matching_fulfillment(g, plan, {})
    assert result_default == 0.5, f"Expected 0.5 (default weights), got {result_default}"

    # With custom weights: Java=1, Python=9
    # This should give (1.0*1.0 + 9.0*0.0) / 10.0 = 0.1
    result_custom = matching_fulfillment(g, plan, {"Java": 1, "Python": 9})
    assert result_custom == 0.1, f"Expected 0.1 (custom weights), got {result_custom}"

    # Verify they are different
    assert result_default != result_custom, "Weights should change the result"


def test_edge_case_empty_graph():
    """Test that matching_fulfillment handles empty graph.projects without ZeroDivisionError."""
    from core.domain.models import Dataset

    ds = Dataset(people=[], projects=[], coworks=[], reviews=[])
    g = MemoryGraph.build(ds, [])

    plan = PlanAssignment(entries=[], objective=0.0, unfilled=[], violations=[])
    result = matching_fulfillment(g, plan, {})
    assert result == 0.0, "Empty graph should return 0.0, not crash"


def test_edge_case_project_no_requirements():
    """Test that matching_fulfillment handles project with zero requirements correctly."""
    from core.domain.models import (
        Dataset, Person, Project, Grade, Sector, ProjectPhase
    )

    people = [
        Person(
            id="p1", name="Person1", grade=Grade.MID,
            monthly_rate=5000,
            skills={"Java": 3},
            availability=[1.0] * 6
        ),
    ]

    # Project with empty requirements
    projects = [
        Project(
            id="proj1", name="Project1", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
            start_month=0, end_month=2,
            grade_headcount={Grade.MID: 1},
            requirements=[],  # No requirements
            monthly_budget=100000
        )
    ]

    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    g = MemoryGraph.build(ds, [])

    entries = [AssignEntry(person_id="p1", project_id="proj1", alloc=1.0)]
    plan = PlanAssignment(entries=entries, objective=0.0, unfilled=[], violations=[])

    result = matching_fulfillment(g, plan, {})
    assert result == 0.0, "Project with no requirements should return 0.0"


def test_multiskill_person_capacity_is_split_not_duplicated():
    """Test revised spec §4.4: capacity-consuming allocation splits across qualified slots.

    Scenario:
    - 1 person: p1 (Java=3, Python=3) — qualifies for BOTH skills
    - 1 project with 2 requirements (different skills):
      - Req1: Java>=3, h=1
      - Req2: Python>=3, h=1
    - Assign p1 to project with alloc=1.0

    Revised formula (capacity-consuming):
    - Q_p1,proj1 = {Req1, Req2} (size 2)
    - Each slot receives p1's alloc split: 1.0/2 = 0.5
    - Req1 (Java): got = 0.5, f_1 = min(1, 0.5/1) = 0.5, w = 3.0
    - Req2 (Python): got = 0.5, f_2 = min(1, 0.5/1) = 0.5, w = 3.0
    - num = 3.0*0.5 + 3.0*0.5 = 3.0
    - den = 3.0 + 3.0 = 6.0
    - result = 3.0 / 6.0 = 0.5

    Old (gameable) formula would wrongly give:
    - Req1: got = 1.0, f_1 = 1.0
    - Req2: got = 1.0, f_2 = 1.0
    - result = 1.0 (both fully satisfied with one person's alloc)
    """
    from core.domain.models import (
        Dataset, Person, Project, SkillRequirement, Grade, Sector, ProjectPhase
    )

    people = [
        Person(
            id="p1", name="Person1", grade=Grade.MID,
            monthly_rate=5000,
            skills={"Java": 3, "Python": 3},
            availability=[1.0] * 6
        ),
    ]

    projects = [
        Project(
            id="proj1", name="Project1", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
            start_month=0, end_month=2,
            grade_headcount={Grade.MID: 1},
            requirements=[
                SkillRequirement(skill="Java", min_level=3, headcount=1),
                SkillRequirement(skill="Python", min_level=3, headcount=1),
            ],
            monthly_budget=100000
        )
    ]

    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    g = MemoryGraph.build(ds, [])

    entries = [AssignEntry(person_id="p1", project_id="proj1", alloc=1.0)]
    plan = PlanAssignment(entries=entries, objective=0.0, unfilled=[], violations=[])

    result = matching_fulfillment(g, plan, {})
    assert abs(result - 0.5) < 1e-9, f"Expected 0.5 (capacity split), got {result}"


def test_plan_assignment_pairs():
    """Test PlanAssignment.pairs() returns (person_id, project_id) set."""
    entries = [
        AssignEntry(person_id="p1", project_id="proj1", alloc=0.5),
        AssignEntry(person_id="p2", project_id="proj1", alloc=0.5),
        AssignEntry(person_id="p3", project_id="proj2", alloc=1.0),
    ]
    plan = PlanAssignment(entries=entries, objective=0.8, unfilled=[], violations=[])
    pairs = plan.pairs()
    expected = {("p1", "proj1"), ("p2", "proj1"), ("p3", "proj2")}
    assert pairs == expected, f"Expected {expected}, got {pairs}"


def test_unknown_person_entry_is_skipped():
    """Test that entries referencing unknown persons are skipped without KeyError.

    Scenario:
    - 1 person in graph: p1
    - 1 project with requirement: Java>=3, h=1
    - 1 entry in plan for unknown person: p_unknown
    - Expected: p_unknown skipped, no KeyError, result = 0.0 (no qualified person)
    """
    from core.domain.models import (
        Dataset, Person, Project, SkillRequirement, Grade, Sector, ProjectPhase
    )

    people = [
        Person(
            id="p1", name="Person1", grade=Grade.MID,
            monthly_rate=5000,
            skills={"Java": 3},
            availability=[1.0] * 6
        ),
    ]

    projects = [
        Project(
            id="proj1", name="Project1", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
            start_month=0, end_month=2,
            grade_headcount={Grade.MID: 1},
            requirements=[
                SkillRequirement(skill="Java", min_level=3, headcount=1),
            ],
            monthly_budget=100000
        )
    ]

    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    g = MemoryGraph.build(ds, [])

    # Entry with unknown person
    entries = [AssignEntry(person_id="p_unknown", project_id="proj1", alloc=1.0)]
    plan = PlanAssignment(entries=entries, objective=0.0, unfilled=[], violations=[])

    # Should not raise KeyError; unknown person is skipped
    result = matching_fulfillment(g, plan, {})
    assert result == 0.0, f"Unknown person should be skipped; expected 0.0, got {result}"


# --- optimization_ratio (spec §4.4, 2026-08-04 확정) -----------------------
# 최적화율 = plan의 스킬적합 목적값(Σ S_ij·a_ij) / LP 완화 상한(UB, 동일 제약에서
# z를 연속으로 완화한 LP의 스킬 목적값). 매칭 충족률(용량 소모형)과 달리 "가용
# 자원 대비 얼마나 최적에 가까운가"를 측정한다 -- 헤드라인 목표 0.90.

def _single_slot_setup():
    """1명/1프로젝트/1요구사항 -- S_p1,proj1 = 1.0(만점)이 되도록 구성해 UB를
    손으로 예측 가능하게 만든 시나리오. p1의 Java=5, 요구 min_level=5, headcount=1,
    grade_headcount={MID:1}(정확히 1명), 예산·가동률은 비제약적(널널)."""
    from core.domain.models import (
        Dataset, Person, Project, SkillRequirement, Grade, Sector, ProjectPhase
    )
    people = [
        Person(id="p1", name="P1", grade=Grade.MID, monthly_rate=1000,
              skills={"Java": 5}, availability=[1.0] * 6),
    ]
    projects = [
        Project(id="proj1", name="Proj1", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
               start_month=0, end_month=0, grade_headcount={Grade.MID: 1},
               requirements=[SkillRequirement(skill="Java", min_level=5, headcount=1)],
               monthly_budget=1_000_000)
    ]
    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    g = MemoryGraph.build(ds, [])
    eng = ScoringEngine(g)
    S = eng.skill_matrix({})
    assert S[0, 0] == 1.0, "setup 전제 붕괴: S_p1,proj1이 1.0이어야 손계산이 성립"
    return g, S


def test_optimization_ratio_perfect_plan_is_one():
    """단일 슬롯을 정확히 최적으로 채운 실제 CBC 해는 UB와 정확히 같아야 한다
    (numerator == UB == 1.0*1.0 == 1.0 -> ratio == 1.0). 손계산: S=1.0인 유일한
    (사람,프로젝트) 쌍에서 제약(가동률 1.0, 예산 널널, 정원 1명)이 전혀 걸리지
    않으므로 a=z=1이 정수해에서도 최적 -> LP 완화 UB와 일치해야 한다."""
    g, S = _single_slot_setup()
    plan = solve_milp(g, S, __import__("numpy").zeros((1, 1)), MilpParams(time_limit=30))
    assert plan.entries == [AssignEntry(person_id="p1", project_id="proj1", alloc=1.0)]
    ratio = optimization_ratio(g, S, plan, MilpParams())
    assert abs(ratio - 1.0) < 1e-6, f"완벽 배치인데 ratio={ratio} (기대 1.0)"


def test_optimization_ratio_hand_computed_suboptimal_plan():
    """같은 셋업에서 '진짜 최적'이 아닌 plan을 손으로 만들어 ratio < 1을 확인한다.

    UB(손계산): S_p1,proj1=1.0, 제약 무해 -> LP 완화 최적은 a=z=1 -> UB=1.0.
    Plan(손으로 구성, CBC 미사용): a_11=0.5 (0.2<=a<=z=1 만족하는 valid 값이지만
    스킬적합 목적값 관점에서 진짜 최적(a=1)에는 못 미침) -> numerator = 1.0*0.5=0.5.
    ratio = 0.5/1.0 = 0.5 (정확히 손계산과 일치해야 함).
    """
    g, S = _single_slot_setup()
    plan = PlanAssignment(
        entries=[AssignEntry(person_id="p1", project_id="proj1", alloc=0.5)],
        objective=0.0, unfilled=[], violations=[])
    ratio = optimization_ratio(g, S, plan, MilpParams())
    assert abs(ratio - 0.5) < 1e-6, f"손계산 기대값 0.5, 실제 {ratio}"


def test_optimization_ratio_empty_plan_zero():
    g, S = _single_slot_setup()
    plan = PlanAssignment(entries=[], objective=0.0, unfilled=[], violations=[])
    assert optimization_ratio(g, S, plan, MilpParams()) == 0.0


def test_optimization_ratio_never_exceeds_one_on_real_solved_plan():
    """실제 CBC로 (synergy 포함) 전체 목적함수로 푼 plan에 대해서도 ratio가
    이론적 상한 1.0을 넘지 않는지(LP relaxation duality) 구조적으로 검증한다.
    synergy 항이 목적함수에 있으므로 실제 plan의 skill-only term은 순수
    skill-maximizing LP의 UB보다 낮을 수 있다(=ratio<1이 자연스러움) -- 이 테스트는
    '항상 1.0'이 아니라 '항상 <=1.0'만 확인한다."""
    ds = generate_dataset(25, 5, seed=3)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = MilpParams(time_limit=60)
    plan = solve_milp(g, S, C, params)
    ratio = optimization_ratio(g, S, plan, params)
    assert 0.0 <= ratio <= 1.0 + 1e-6, f"ratio={ratio}가 [0,1] 범위를 벗어남"


def test_optimization_ratio_unknown_person_entry_is_skipped():
    g, S = _single_slot_setup()
    plan = PlanAssignment(
        entries=[AssignEntry(person_id="p_unknown", project_id="proj1", alloc=1.0)],
        objective=0.0, unfilled=[], violations=[])
    assert optimization_ratio(g, S, plan, MilpParams()) == 0.0
