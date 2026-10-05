import time
from types import SimpleNamespace

import pulp
import pytest

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry, PlanAssignment
from core.optimize.alternatives import generate_plans, meets_quality_floor, _diversity_cut


# --- 브리핑 Step 1 테스트 (verbatim) ----------------------------------------
# 실측 확인: n=25/p=5/seed=3 시나리오는 Plan A objective가 양수(약 5.96, unfilled=0)
# 이므로 브리핑의 `0.95 * obj_a` 비교가 부호 반전 함정에 걸리지 않는다 -- 아래
# meets_quality_floor()와 결과가 정확히 동일하다. 따라서 이 테스트는 브리핑 그대로 둔다.

def test_alternatives_quality_and_diversity():
    ds = generate_dataset(25, 5, seed=3)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    plans = generate_plans(g, eng.skill_matrix({}), eng.synergy_matrix(),
                           MilpParams(time_limit=60), n_alternatives=2)
    assert plans[0].label == "A" and len(plans) >= 2
    obj_a = plans[0].objective
    for alt in plans[1:]:
        assert alt.objective >= 0.95 * obj_a - 1e-6
        prev = plans[0].pairs()
        overlap = len(alt.pairs() & prev) / max(1, len(prev))
        assert overlap <= 0.8 + 1e-6


# --- 컨트롤러 추가: 목적함수 부호 이슈 ---------------------------------------

def test_meets_quality_floor_symmetric_for_positive_objA():
    """objA >= 0일 때는 교과서적 공식(floor*objA)과 정확히 같아야 한다."""
    obj_a = 100.0
    assert meets_quality_floor(95.0, obj_a) is True           # == 0.95*objA
    assert meets_quality_floor(94.999999, obj_a) is True      # 1e-6 허용오차 이내
    assert meets_quality_floor(94.9, obj_a) is False          # 명확히 미달
    assert meets_quality_floor(120.0, obj_a) is True         # 더 좋은 해는 항상 통과


def test_meets_quality_floor_symmetric_for_negative_objA():
    """objA < 0(슬랙 페널티로 인한 대량 음수)일 때도 "objA 대비 5% 이내"를 뜻해야 한다.

    objA=-100이면 허용 낙폭은 |objA|*0.05=5 -> 임계값은 -105.
    - -103(3만큼 나쁨, 5% 이내) -> 통과
    - -105(정확히 경계) -> 통과
    - -108(8만큼 나쁨, 5% 초과) -> 탈락
    - -96/-99(Plan A보다 오히려 더 좋은 해, 즉 objA인 -100보다 큼) -> 당연히 통과
    """
    obj_a = -100.0
    assert meets_quality_floor(-103.0, obj_a) is True
    assert meets_quality_floor(-105.0, obj_a) is True
    assert meets_quality_floor(-108.0, obj_a) is False
    assert meets_quality_floor(-96.0, obj_a) is True
    assert meets_quality_floor(-99.0, obj_a) is True


def test_naive_brief_formula_would_reject_everything_when_objA_negative():
    """브리핑 원문 비교식(`alt.objective >= 0.95 * plan_a.objective`)을 objA<0에
    그대로 적용하면 무슨 일이 벌어지는지 수치로 증명한다: 임계값(0.95*objA)이 objA
    자신보다 "더 좋은" 값이 되어버려, alt가 (제약이 더 많은 하위 문제이므로 결코
    넘을 수 없는) 전역 최적해 objA보다도 좋아야만 통과한다 -- 즉 사실상 항상 거부된다.
    이것이 이 태스크가 브리핑의 비교식을 verbatim으로 채택하지 않은 이유다.
    """
    obj_a = -100.0
    naive_threshold = 0.95 * obj_a  # = -95.0, objA(-100)보다 큼(더 좋음)
    assert naive_threshold > obj_a, "임계값이 Plan A 자신보다 더 좋은 해를 요구하게 됨"

    # alt는 diversity cut이 추가된 "Plan A의 제약을 모두 포함하는" 하위문제의 최적해이므로
    # 이론상 alt.objective <= obj_a (전역 최적해를 절대 넘을 수 없음). 그 전제 하에서
    # naive 공식은 반드시 거부한다: alt <= obj_a < naive_threshold.
    for alt_obj in (-100.0, -100.5, -103.0, -108.0):
        assert alt_obj <= obj_a + 1e-9
        assert not (alt_obj >= naive_threshold - 1e-6), (
            "naive 공식이 obj_a 이하의 alt를 통과시켰다면 모순 -- 이 케이스는 항상 거부되어야 함")
    # 반면 수정된 게이트는 -103(5% 이내)은 통과시킨다 -- naive라면 놓쳤을 유효한 대안.
    assert meets_quality_floor(-103.0, obj_a) is True


class _FakeGraph:
    def __init__(self, project_ids, pid_index):
        self.projects = [SimpleNamespace(id=jid) for jid in project_ids]
        self.pid_index = pid_index
        self.project_index = {jid: k for k, jid in enumerate(project_ids)}


def test_generate_plans_still_finds_alternatives_when_planA_objective_is_negative(monkeypatch):
    """generate_plans 자체(모듈 함수 전체)를 대상으로 한 회귀 테스트: solve_milp을
    monkeypatch해 Plan A objective가 -100(슬랙으로 인한 대량 음수)인 상황을 결정론적으로
    재현한다. -103(5% 이내, 통과되어야 함)과 -108(5% 초과, 탈락해야 함)을 순서대로
    반환하도록 구성해, 수정된 게이트가 실제로 -103짜리 대안 하나를 채택하고 -108에서
    멈추는지 확인한다. 브리핑 원문 공식이었다면 첫 번째 대안(-103)부터 즉시 탈락해
    유효 대안이 0개가 되었을 것이다(위 test_naive_brief_formula_... 참고).
    """
    graph = _FakeGraph(["j0"], {"p0": 0, "p1": 1, "p2": 2, "p3": 3})
    plan_a = PlanAssignment(
        entries=[AssignEntry(person_id="p0", project_id="j0", alloc=1.0),
                AssignEntry(person_id="p1", project_id="j0", alloc=1.0)],
        objective=-100.0, unfilled=["j0:고급:1명 미충원"], violations=[], label="A")
    alt_b = PlanAssignment(
        entries=[AssignEntry(person_id="p0", project_id="j0", alloc=1.0),
                AssignEntry(person_id="p2", project_id="j0", alloc=1.0)],
        objective=-103.0, unfilled=["j0:고급:1명 미충원"], violations=[])
    alt_c_rejected = PlanAssignment(
        entries=[AssignEntry(person_id="p0", project_id="j0", alloc=1.0),
                AssignEntry(person_id="p3", project_id="j0", alloc=1.0)],
        objective=-108.0, unfilled=["j0:고급:1명 미충원"], violations=[])
    canned = [plan_a, alt_b, alt_c_rejected]
    calls = []

    def fake_solve(g, S, C, params, extra_constraints=None):
        calls.append(extra_constraints)
        return canned[len(calls) - 1], "Optimal"

    monkeypatch.setattr("core.optimize.alternatives._solve", fake_solve)

    plans = generate_plans(graph, None, None, MilpParams(), n_alternatives=2)

    assert [p.objective for p in plans] == [-100.0, -103.0], (
        "-103(5% 이내)은 채택되고 -108(5% 초과)에서 멈춰야 함")
    assert [p.label for p in plans] == ["A", "B"]
    assert len(calls) == 3, "탈락한 -108 시도까지 포함해 총 3회 solve_milp 호출"


# --- 컨트롤러 추가: 다양성 cut이 "모든" 이전 해에 대해 걸리는가 --------------

def test_diversity_cut_adds_one_constraint_per_prior_plan():
    """`_diversity_cut`이 Plan A뿐 아니라 이미 채택된 모든 이전 해 각각에 대해
    별도의 no-good cut을 추가하는지(합쳐서 하나로 뭉개지 않는지) 구조적으로 검증한다.
    실제 CBC 없이 pulp 제약 객체를 직접 조사한다.
    """
    pdx = {"p0": 0, "p1": 1, "p2": 2}
    jdx = {"j0": 0}
    plan_a_pairs = {("p0", "j0"), ("p1", "j0")}
    plan_b_pairs = {("p0", "j0"), ("p2", "j0"), ("p1", "j0")}
    prev_sets = [plan_a_pairs, plan_b_pairs]

    prob = pulp.LpProblem("t", pulp.LpMaximize)
    z = {i: {0: pulp.LpVariable(f"z_{i}_0", cat="Binary")} for i in range(3)}
    cut = _diversity_cut(prev_sets, pdx, jdx)
    cut(prob, z)

    constraints = list(prob.constraints.values())
    assert len(constraints) == 2, "이전 해가 2개면 cut도 2개 -- Plan A 하나만이 아님"

    # 각 cut의 RHS(=-constant)가 그 해당 이전 해의 floor(0.8*|prior|)와 정확히 일치하고
    # (뭉쳐진 합계가 아니라 개별 값), LHS가 오직 그 이전 해의 변수만 참조하는지 확인.
    for pairs_set, constraint in zip(prev_sets, constraints):
        rhs = -constraint.constant
        assert rhs == pytest.approx(float(__import__("math").floor(0.8 * len(pairs_set))))
        referenced = {v.name for v in constraint.keys()}
        expected_names = {f"z_{pdx[pid]}_{jdx[jid]}" for pid, jid in pairs_set}
        assert referenced == expected_names


@pytest.mark.parametrize("seed", [3])
def test_diversity_binds_against_every_prior_plan_end_to_end(seed):
    """실제 CBC 해로 A/B/C 3개를 만들어, C가 A뿐 아니라 B와도 80% 이하로 겹치는지
    (즉 cut이 B에 대해서도 실제로 걸렸는지) 종단으로 검증한다. 또한 연속한 해들이
    서로 완전히 동일하지 않음(no-good cut이 실제로 이전 해를 배제함)을 직접 확인한다.
    """
    ds = generate_dataset(25, 5, seed=seed)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    plans = generate_plans(g, S, C, MilpParams(time_limit=60), n_alternatives=3)

    assert plans[0].label == "A"
    assert [p.label for p in plans] == list("ABCDEFG"[: len(plans)]), \
        "라벨이 A, B, C, D 순으로 부여되어야 함"

    for i, plan in enumerate(plans):
        for prior in plans[:i]:
            prev_pairs = prior.pairs()
            assert plan.pairs() != prev_pairs, (
                f"{plan.label}이(가) {prior.label}과(와) 완전히 동일한 배치 -- "
                f"no-good cut이 이전 해를 실제로 배제하지 못함")
            overlap = len(plan.pairs() & prev_pairs) / max(1, len(prev_pairs))
            assert overlap <= 0.8 + 1e-6, (
                f"{plan.label}이(가) {prior.label}과(와) {overlap:.2%} 중복 -- "
                f"cut이 Plan A뿐 아니라 {prior.label}에도 걸려야 함")


# --- 3차 리뷰(Finding 2 정정): 원시 entry-count 95% 바닥은 잘못된 지표였다 ---------
# 2차 리뷰 fix 라운드에서 진단한 결과(아래, task-13-report.md에 상세): n=100/p=20/seed=42
# 에서 total_need(등급 정원 합)는 76인데 실측 entries는 108~114 -- 대부분이 등급 정원
# equality(Σz+slack==need)에 전혀 걸리지 않는 "보너스" 배치(주로 초급, 단가가 싸서 예산이
# 허용하는 한 skill-fit 이득만으로 자유롭게 추가/삭제됨)였다. 이 보너스 배치가 대안마다
# 자연스럽게 늘고 줄기 때문에, "entries >= Plan A의 95%"라는 raw 카운트 바닥은 무해한
# 현상(보너스 인원 변동)을 truncation으로 오인하는 잘못된 프록시였다 -- 실제로 필요한
# 것은 "새 배치가 실제로 있는가"(진짜 substitution)와 "필수 정원 커버리지가 Plan A보다
# 나빠지지 않았는가"(진짜 truncation 방지)뿐이다. 컨트롤러 3차 지시에 따라 raw entry-count
# 바닥과 "new>=0.5*dropped" 비율 체크를 모두 제거하고, 아래 두 불변식으로 교체한다.

def test_alternatives_are_genuine_substitutions_not_truncations():
    """각 대안에 대해 다음 두 가지를 하드 어서션한다:
    1. `alt.pairs() - planA.pairs()`가 비어있지 않다 -- Plan A에 없던 새 배치를 실제로
       도입한다(순수 삭제라면 이 집합이 공집합이라 실패).
    2. `len(alt.unfilled) <= len(planA.unfilled)` -- 대안이 Plan A보다 "더 많은" 필수
       등급 슬롯을 미충원 상태로 남겨서 다양성을 "값싸게" 얻지 않는다. 보너스(비정원)
       배치는 늘거나 줄어도 무방하지만, 필수 정원 커버리지는 절대 Plan A보다 나빠지면
       안 된다는 것이 실제로 필요한 anti-truncation 개런티다.

    브리핑 스케일(n=25/p=5/seed=3)에서 실측 확인 결과 모든 대안이 unfilled=0(Plan A도
    0)이라 조건 2는 자명하게 성립하고, 조건 1도 3개 대안 모두 통과(새 배치 5~7개).
    """
    ds = generate_dataset(25, 5, seed=3)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    plans = generate_plans(g, S, C, MilpParams(time_limit=60), n_alternatives=3)
    plan_a = plans[0]
    assert len(plans) >= 2, "대안이 하나도 안 나오면 이 테스트 자체가 무의미"

    for alt in plans[1:]:
        new = alt.pairs() - plan_a.pairs()
        assert len(new) > 0, f"{alt.label} adds no new assignment vs Plan A -- pure truncation"
        assert len(alt.unfilled) <= len(plan_a.unfilled), (
            f"{alt.label}: unfilled={alt.unfilled} (count={len(alt.unfilled)}) leaves MORE "
            f"required quota slots unfilled than Plan A(count={len(plan_a.unfilled)}) -- "
            f"genuine truncation of required headcount coverage")


# --- 컨트롤러 추가: 데모 규모 실측 (fixtures/*.json 미동결 상태이므로 Task 14와
# 동일 규모(n=100/p=20)의 대체 데이터셋으로 실측) ---------------------------

@pytest.mark.slow
def test_demo_scale_alternatives():
    """데모급 규모(n=100/p=20, seed=42 -- Task 14 계획의 동결 fixture와 동일 규모)에서
    (1) 실제로 몇 개의 유효 대안을 얻을 수 있는지, (2) 그 대안들이 진짜 substitution인지
    (Finding 2) 실측하고 벽시계 시간을 기록한다. 두 관심사를 하나의 테스트로 묶은 이유는
    `generate_plans(..., n_alternatives=3)` 자체가 이 규모에서 ~25~30초짜리 CBC 4회
    호출이라, 별도 테스트로 쪼개면 데모 규모 solve를 두 번 지불하게 되기 때문이다.

    컨트롤러 지시(개수): "3개 미만이면 임계값을 완화해 개수를 인위적으로 늘리지 말고
    숫자 그대로 보고하라"는 지침에 따라, 개수 자체(`len(plans) >= 4`)를 하드
    어서션하지 않는다 -- 항상 성립해야 하는 구조적 불변식(A 존재, 라벨 순서, 채택된
    대안들이 품질/다양성 게이트를 만족함)만 검증한다.

    컨트롤러 지시(Finding 2, 3차 정정): 원래 썼던 원시 "entries >= Plan A의 95%" 바닥은
    잘못된 지표였다 -- entries 대부분이 등급 정원에 안 걸리는 "보너스" 배치(실측:
    total_need=76 vs entries=108~114)라서, 그 변동은 무해한 현상이지 truncation이 아니다.
    실제로 필요한 두 불변식만 하드 어서션한다: (a) 새 배치가 실제로 존재(new>0, 순수
    삭제 방지) (b) `len(alt.unfilled) <= len(planA.unfilled)`(필수 등급 정원 커버리지가
    Plan A보다 나빠지지 않음 -- 이게 진짜 anti-truncation 개런티). `xfail`은 더 이상
    필요 없다 -- 이 규모/시드에서 둘 다 항상 성립함을 실측으로 확인했다(모든 plan이
    unfilled=0).
    """
    ds = generate_dataset(100, 20, seed=42)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()

    t0 = time.time()
    plans = generate_plans(g, S, C, MilpParams(time_limit=180), n_alternatives=3)
    dt = time.time() - t0

    print(f"\n[demo-scale n=100/p=20/seed=42] obtained {len(plans)} plan(s) "
          f"(Plan A + {len(plans) - 1} valid alternative(s)) in {dt:.2f}s wall-clock")
    for p in plans:
        print(f"  {p.label}: objective={p.objective:.3f} unfilled={len(p.unfilled)} "
              f"entries={len(p.entries)}")

    assert plans[0].label == "A"
    assert [p.label for p in plans] == list("ABCDEFG"[: len(plans)])
    obj_a = plans[0].objective
    for i, alt in enumerate(plans[1:], start=1):
        assert meets_quality_floor(alt.objective, obj_a)
        for prior in plans[:i]:
            prev = prior.pairs()
            overlap = len(alt.pairs() & prev) / max(1, len(prev))
            assert overlap <= 0.8 + 1e-6

    # --- Finding 2 (3차 정정): genuine substitution vs. truncation -------------------
    plan_a = plans[0]
    print(f"[substitution check] Plan A entries={len(plan_a.entries)} "
          f"unfilled={len(plan_a.unfilled)}")
    for alt in plans[1:]:
        dropped = plan_a.pairs() - alt.pairs()
        new = alt.pairs() - plan_a.pairs()
        print(f"  {alt.label}: entries={len(alt.entries)} "
              f"({len(alt.entries) / len(plan_a.entries):.4f} of A) dropped={len(dropped)} "
              f"new={len(new)} unfilled={len(alt.unfilled)} (planA={len(plan_a.unfilled)})")
        assert len(new) > 0, f"{alt.label} adds no new assignment vs Plan A -- pure truncation"
        assert len(alt.unfilled) <= len(plan_a.unfilled), (
            f"{alt.label}: unfilled={alt.unfilled} (count={len(alt.unfilled)}) leaves MORE "
            f"required quota slots unfilled than Plan A(count={len(plan_a.unfilled)}) -- "
            f"genuine truncation of required headcount coverage")


# --- Task 4 Step 1 (브리핑 verbatim): 스트리밍 제너레이터 -----------------------

def test_generate_plans_streaming_yields_plan_a_first_then_alternatives():
    ds = generate_dataset(25, 5, seed=3)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    from core.optimize.alternatives import generate_plans_streaming
    gen = generate_plans_streaming(g, eng.skill_matrix({}), eng.synergy_matrix(),
                                   MilpParams(time_limit=60), n_alternatives=2)
    first = next(gen)
    assert first.label == "A"
    rest = list(gen)
    assert len(rest) >= 1
    assert [p.label for p in rest] == list("BCDEFG"[:len(rest)])


def test_generate_plans_still_returns_a_list_backward_compatible():
    """generate_plans는 generate_plans_streaming의 리스트화일 뿐이어야 한다 --
    exp3_algorithm.py와 test_e2e_smoke.py가 이 함수를 list로 계속 쓴다."""
    ds = generate_dataset(25, 5, seed=3)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    plans = generate_plans(g, eng.skill_matrix({}), eng.synergy_matrix(),
                           MilpParams(time_limit=60), n_alternatives=2)
    assert isinstance(plans, list) and plans[0].label == "A"


@pytest.mark.parametrize("reason,flag", [("time_limit_incumbent", True), ("optimal", False)])
def test_solve_marks_plans_stopped_at_the_time_limit(monkeypatch, reason, flag):
    # 화면 배지 "시간 한도 도달"의 근거: 솔버 종료 사유가 시간 한도면 플랜에 표시한다(리뷰 S3).
    from core.optimize import alternatives
    plan = PlanAssignment(entries=[], objective=1.0, unfilled=[], violations=[])
    raw = SimpleNamespace(plan=plan, evidence=SimpleNamespace(termination_reason=reason))
    monkeypatch.setattr(alternatives, "solve_milp_diagnostic", lambda *a, **k: raw)
    out, got = alternatives._solve(None, None, None, None)
    assert got == reason and out.time_limited is flag
