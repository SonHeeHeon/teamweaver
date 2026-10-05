import numpy as np
import pulp
import pytest
from collections import defaultdict
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import (Grade, Person, Project, ProjectPhase, Sector,
    SkillRequirement)
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine
from core.optimize.milp import MilpParams, solve_milp, pruned_pairs, _overfamiliar_pairs


def _setup(n=25, j=5, seed=3):
    ds = generate_dataset(n, j, seed=seed)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    return ds, g, eng.skill_matrix({}), eng.synergy_matrix()


def test_pruned_pairs_ratio():
    C = np.random.default_rng(0).normal(size=(20, 20)); C = (C + C.T) / 2
    pairs = pruned_pairs(C, 0.15)
    assert len(pairs) == int(0.15 * (20 * 19 / 2))
    assert all(i < j for i, j in pairs)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_milp_solution_satisfies_all_constraints(seed):
    ds, g, S, C = _setup(seed=seed)
    plan = solve_milp(g, S, C, MilpParams(time_limit=60))
    by_pid = {p.id: p for p in ds.people}
    by_jid = {j.id: j for j in ds.projects}
    # 1) 월별 가동률
    load = defaultdict(float)
    for e in plan.entries:
        for m in by_jid[e.project_id].months:
            load[(e.person_id, m)] += e.alloc
    for (pid, m), v in load.items():
        assert v <= by_pid[pid].availability[m] + 1e-6
    # 2) 최소 투입률
    assert all(e.alloc >= 0.2 - 1e-6 for e in plan.entries)
    # 3) 등급 정원 (slack 반영: 배치 + 미충원 = 정원)
    cnt = defaultdict(int)
    for e in plan.entries:
        cnt[(e.project_id, by_pid[e.person_id].grade)] += 1
    short = defaultdict(int)
    for u in plan.unfilled:                      # 형식 "j00:고급:1명 미충원"
        jid, gname, rest = u.split(":")
        short[(jid, gname)] += int(rest.replace("명 미충원", ""))
    for jj in ds.projects:
        for grade, need in jj.grade_headcount.items():
            assert cnt[(jj.id, grade)] + short[(jj.id, grade.value)] == need
    # 4) 월 예산
    for jj in ds.projects:
        cost = sum(by_pid[e.person_id].monthly_rate * e.alloc
                   for e in plan.entries if e.project_id == jj.id)
        assert cost <= jj.monthly_budget + 1e-6
    # 5) MILP는 violations 없음
    assert plan.violations == []


# --- 컨트롤러 추가 테스트 ---------------------------------------------------

class _MockGraph:
    """solve_milp이 실제로 쓰는 속성(people/projects/cowork_months)만 채운 최소 목."""
    def __init__(self, people, projects, n):
        self.people = people
        self.projects = projects
        self.cowork_months = np.zeros((n, n))


def _budget_tight_project():
    """등급 정원을 최소 투입률(0.2)로 채우는 비용조차 예산을 넘는 프로젝트.

    2명(특급, 단가 1000)이 필요하지만 예산 350은 min_alloc(0.2) 기준 최소 비용
    2*1000*0.2=400보다 작다 -> 두 자리 모두 채우는 것은 예산상 불가능하므로
    MILP는 slack(미충원)을 반드시 사용해야 하며, 그래도 예산은 위반하지 않아야 한다.
    """
    people = [
        Person(id="p0", name="A", grade=Grade.SENIOR, monthly_rate=1000,
               skills={"Java": 3}, availability=[1.0] * 6),
        Person(id="p1", name="B", grade=Grade.SENIOR, monthly_rate=1000,
               skills={"Java": 3}, availability=[1.0] * 6),
    ]
    projects = [
        Project(id="jTight", name="예산빠듯", sector=Sector.INTERNAL,
                phase=ProjectPhase.EXECUTION, start_month=0, end_month=1,
                grade_headcount={Grade.SENIOR: 2}, monthly_budget=350,
                requirements=[SkillRequirement(skill="Java", min_level=1, headcount=2)]),
    ]
    S = np.array([[0.9], [0.7]])
    C = np.zeros((2, 2))
    return people, projects, S, C


def test_milp_budget_tight_underfills_instead_of_violating():
    people, projects, S, C = _budget_tight_project()
    g = _MockGraph(people, projects, n=2)
    plan = solve_milp(g, S, C, MilpParams(time_limit=30))

    # 예산 위반 없음
    cost = sum(p.monthly_rate * e.alloc for e in plan.entries
               for p in people if p.id == e.person_id and e.project_id == "jTight")
    assert cost <= 350 + 1e-6

    # 두 자리를 다 채울 수 없으므로 미충원이 발생해야 하고, violations는 비어야 함
    assert plan.violations == []
    assert plan.unfilled == ["jTight:고급:1명 미충원"]
    # 1명만 배치됨 (정원 2 - 미충원 1 = 배치 1)
    tight_entries = [e for e in plan.entries if e.project_id == "jTight"]
    assert len(tight_entries) == 1
    assert tight_entries[0].alloc >= 0.2 - 1e-6


def test_milp_no_violations_by_construction_on_budget_tight_scenario():
    """`solve_milp`은 `violations=[]`를 항상 하드코드로 반환하므로, `plan.violations`
    필드를 읽는 것만으로는 아무 것도 증명하지 못한다(소스 코드 리터럴을 되읽는 tautology).
    이 테스트는 그 필드를 전혀 참조하지 않고, `entries`/`unfilled`로부터 예산·정원
    불변식을 직접 재계산해 검증한다 — naive(정원을 최소 투입률로라도 무조건 다 채우는)
    접근이면 반드시 예산을 초과하는 시나리오를 골라서 확인한다.
    """
    people, projects, S, C = _budget_tight_project()
    g = _MockGraph(people, projects, n=2)
    plan = solve_milp(g, S, C, MilpParams(time_limit=30))
    tight = projects[0]

    # 시나리오 자체 검증: naive하게 정원을 min_alloc으로라도 전원 채우면 예산 초과여야
    # 이 테스트가 "naive 솔버라면 위반했을 상황"이라는 전제가 성립한다.
    naive_full_fill_cost = sum(p.monthly_rate * 0.2 for p in people)
    assert naive_full_fill_cost > tight.monthly_budget, (
        "시나리오 전제 실패: 전원을 최소 투입률로 채워도 예산 이내라면 이 테스트가 "
        "'예산 위반 방지'를 검증하지 못한다")

    # plan.violations를 참조하지 않고 entries만으로 예산 재계산
    by_pid = {p.id: p for p in people}
    cost = sum(by_pid[e.person_id].monthly_rate * e.alloc
               for e in plan.entries if e.project_id == tight.id)
    assert cost <= tight.monthly_budget + 1e-6

    # plan.violations를 참조하지 않고 entries+unfilled로 정원 불변식 재계산
    placed = sum(1 for e in plan.entries
                 if e.project_id == tight.id and by_pid[e.person_id].grade == Grade.SENIOR)
    short = 0
    for u in plan.unfilled:
        jid, gname, rest = u.split(":")
        if jid == tight.id and gname == Grade.SENIOR.value:
            short += int(rest.replace("명 미충원", ""))
    assert placed + short == tight.grade_headcount[Grade.SENIOR]


def test_overfamiliar_pairs_independent_of_pruning():
    """Finding 1 회귀 테스트(단위): |C|가 작아 pair_keep_ratio에 의해 pruning되는 쌍이라도
    cowork_months가 임계값 이상이면 `_overfamiliar_pairs`는 이를 포함해야 한다 — 클리크
    페널티 대상 집합은 시너지 pruning과 무관하게 전수 계산되어야 한다."""
    n = 4
    C = np.zeros((n, n))
    C[0, 1] = C[1, 0] = 0.01     # |C| 최소 -> pruning으로 제외될 쌍
    C[2, 3] = C[3, 2] = 0.9      # |C| 최대 -> pruning으로 유지될 쌍
    C[0, 2] = C[2, 0] = 0.02
    C[0, 3] = C[3, 0] = 0.03
    C[1, 2] = C[2, 1] = 0.04
    C[1, 3] = C[3, 1] = 0.05

    ratio = 0.17  # 총 6쌍 중 top-1만 유지 -> (2,3)만 pruned set에 남음
    pruned = set(pruned_pairs(C, ratio))
    assert pruned == {(2, 3)}
    assert (0, 1) not in pruned  # (0,1)은 |C|가 작아 pruning으로 제외됨

    class _CoworkOnlyGraph:
        def __init__(self):
            self.cowork_months = np.zeros((n, n))
            self.cowork_months[0, 1] = self.cowork_months[1, 0] = 10  # >= threshold(6)

    overfam = _overfamiliar_pairs(_CoworkOnlyGraph(), threshold=6)
    assert (0, 1) in overfam, "pruning으로 제외된 쌍도 cowork 임계값을 넘으면 overfam에 포함되어야 함"

    # 옛(버그) 방식: overfam을 pruned 집합과 교집합으로 구했다면 (0,1)은 사라졌을 것
    old_buggy_overfam = {(p, q) for (p, q) in pruned if (p, q) == (0, 1)}
    assert old_buggy_overfam == set(), "구버그 재현: pruned 교집합 방식이면 (0,1)이 소실됨을 확인"


def test_milp_clique_penalty_applies_even_when_pair_pruned_from_reward():
    """Finding 1 회귀 테스트(종단): |C|가 작아 시너지 보상 항(pruned 집합)에서는 제외되지만
    cowork_months가 임계값을 넘는 쌍이, 실제로 목적함수의 -mu 페널티를 받는지 검증한다.

    설계: p0/p1이 스킬 적합도가 압도적으로 높아(0.95/0.9 vs 0.1/0.1) mu 값과 무관하게
    항상 함께 배치되지만, C[0,1]은 매우 작아 pruning으로 시너지 보상 대상에서는 제외됨.
    cowork_months[0,1]=10(임계값 6 이상)이므로 mu>0이면 반드시 -mu 페널티가 적용되어야
    한다 — mu=0.2와 mu=0 두 번 풀어 objective 차이가 정확히 0.2인지로 검증한다(부동소수
    오차만 허용). 이 차이가 0에 가깝다면(구버그처럼 pruning과 교집합해 overfam을 구했다면)
    페널티가 조용히 무력화된 것이므로 테스트가 실패한다.
    """
    people = [Person(id=f"p{i}", name=f"P{i}", grade=Grade.MID, monthly_rate=1000,
                     skills={"Java": 3}, availability=[1.0] * 6) for i in range(4)]
    projects = [Project(id="proj", name="proj", sector=Sector.INTERNAL,
                        phase=ProjectPhase.EXECUTION, start_month=0, end_month=1,
                        grade_headcount={Grade.MID: 2}, monthly_budget=5000,
                        requirements=[SkillRequirement(skill="Java", min_level=1, headcount=2)])]
    S = np.array([[0.95], [0.9], [0.1], [0.1]])
    C = np.zeros((4, 4))
    C[0, 1] = C[1, 0] = 0.01
    C[2, 3] = C[3, 2] = 0.9
    C[0, 2] = C[2, 0] = 0.02
    C[0, 3] = C[3, 0] = 0.03
    C[1, 2] = C[2, 1] = 0.04
    C[1, 3] = C[3, 1] = 0.05

    class _Graph:
        def __init__(self):
            self.people = people
            self.projects = projects
            self.cowork_months = np.zeros((4, 4))
            self.cowork_months[0, 1] = self.cowork_months[1, 0] = 10

    g = _Graph()
    ratio = 0.17  # (0,1)은 |C|가 작아 pruning으로 시너지 보상 대상에서 제외됨 (top-1 = (2,3)만 유지)
    assert (0, 1) not in set(pruned_pairs(C, ratio))

    results = {}
    for mu in (0.2, 0.0):
        params = MilpParams(mu=mu, lam=0.3, pair_keep_ratio=ratio,
                            clique_threshold_months=6, min_alloc=0.2, time_limit=30)
        plan = solve_milp(g, S, C, params)
        results[mu] = plan
        assigned = {e.person_id for e in plan.entries}
        assert assigned == {"p0", "p1"}, \
            f"p0/p1이 스킬상 압도적으로 우수해 mu={mu}에서도 항상 선택되어야 함, got {assigned}"

    diff = results[0.0].objective - results[0.2].objective
    assert diff == pytest.approx(0.2, abs=1e-4), (
        f"클리크 페널티(mu=0.2)가 pruned된 (0,1) 쌍에 적용되지 않은 것으로 보임 "
        f"(objective 차이={diff}, 기대값=0.2). 구버그(overfam을 pruned와 교집합)라면 "
        f"이 차이가 0이 되어 테스트가 실패한다.")


def test_milp_raises_on_no_incumbent(monkeypatch):
    """Finding 2 회귀 테스트: CBC가 time_limit 안에 실행가능해조차 하나도 못 찾으면
    모든 변수의 .value()가 None이 된다. 이 경우 구버그는 `aval is not None` 가드
    때문에 텅 빈/부분적인 PlanAssignment를 정상 결과처럼 조용히 반환했다. 실제 CBC를
    "무해 발견 실패" 상태로 몰아넣는 것은 결정론적이지 않으므로, `LpProblem.solve`를
    monkeypatch해 "Not Solved" 상태에서 변수 값이 전혀 채워지지 않은 상황을 결정론적으로
    재현한다."""
    def _fake_solve(self, solver=None, **kwargs):
        self.status = pulp.LpStatusNotSolved
        return pulp.LpStatusNotSolved

    monkeypatch.setattr(pulp.LpProblem, "solve", _fake_solve)

    people, projects, S, C = _budget_tight_project()
    g = _MockGraph(people, projects, n=2)
    with pytest.raises(RuntimeError, match="no incumbent"):
        solve_milp(g, S, C, MilpParams(time_limit=1))


def test_pruned_pairs_selects_highest_abs_c_not_arbitrary():
    """pruned_pairs가 |C| 상위 keep_ratio만 정확히 선택하는지 (임의 부분집합이 아님)를 검증.
    - 값이 오름차순으로 커지도록 채운 뒤, 상위 k개가 정확히 마지막(최댓값) 쌍들인지 확인.
    - 절댓값 기준 선택임을 증명하기 위해 가장 낮은 인덱스 쌍에 큰 음수를 넣어도 선택되는지 확인.
    """
    n = 6
    C = np.zeros((n, n))
    val = 1.0
    for i in range(n):
        for j in range(i + 1, n):
            C[i, j] = C[j, i] = val
            val += 1.0
    # (0,1)은 원래 최소값(1.0)이었으나 |C|가 가장 크도록 큰 음수로 덮어씀
    C[0, 1] = C[1, 0] = -100.0

    keep_ratio = 0.2
    total = n * (n - 1) // 2  # 15
    k = int(keep_ratio * total)  # 3
    pairs = pruned_pairs(C, keep_ratio)

    expected = sorted(((i, j) for i in range(n) for j in range(i + 1, n)),
                       key=lambda p: -abs(C[p]))[:k]
    assert len(pairs) == k
    assert pairs == expected
    # 절댓값이 가장 큰 음수 쌍이 반드시 포함되어야 함 (raw value가 아니라 |C| 기준)
    assert (0, 1) in pairs
    # 원래 값 기준으로 두번째로 작았던 쌍(값=2.0, (0,2))은 포함되면 안 됨
    assert (0, 2) not in pairs


def test_pruned_pairs_respects_absolute_cap():
    rng = np.random.default_rng(0)
    C = rng.normal(size=(200, 200)); C = (C + C.T) / 2
    np.fill_diagonal(C, 0.0)
    # 200명 → 19,900쌍, 15% = 2,985쌍. 상한 500이면 500이 이겨야 한다.
    pairs = pruned_pairs(C, 0.15, max_pairs=500)
    assert len(pairs) == 500
    assert all(i < j for i, j in pairs)
    assert len(set(pairs)) == 500


def test_pruned_pairs_cap_selects_strongest_by_abs_c():
    rng = np.random.default_rng(1)
    C = rng.normal(size=(60, 60)); C = (C + C.T) / 2
    np.fill_diagonal(C, 0.0)
    pairs = pruned_pairs(C, 1.0, max_pairs=25)
    chosen = min(abs(C[p]) for p in pairs)
    rejected = [(i, j) for i in range(60) for j in range(i + 1, 60) if (i, j) not in set(pairs)]
    assert chosen >= max(abs(C[p]) for p in rejected) - 1e-12


def test_pruned_pairs_ratio_wins_when_smaller_than_cap():
    rng = np.random.default_rng(2)
    C = rng.normal(size=(50, 50)); C = (C + C.T) / 2
    np.fill_diagonal(C, 0.0)
    # 50명 → 1,225쌍, 10% = 122쌍 < 상한 1000
    assert len(pruned_pairs(C, 0.10, max_pairs=1000)) == 122


def test_pruned_pairs_default_cap_bounds_large_n():
    rng = np.random.default_rng(3)
    C = rng.normal(size=(400, 400)); C = (C + C.T) / 2
    np.fill_diagonal(C, 0.0)
    # 기본 MilpParams.max_pairs(2026-10-05부터 200, 규모 리허설 근거)가 적용되어 정확히 상한에서 잘려야 한다
    p = MilpParams()
    assert p.max_pairs == 200
    assert len(pruned_pairs(C, p.pair_keep_ratio, max_pairs=p.max_pairs)) == 200


def test_pruned_pairs_tie_break_is_deterministic_by_index():
    """Ties in |C| values must resolve deterministically by (i,j) index order."""
    # (0,1), (0,2), (0,3) all have |C|=0.5 (tied). Asking for k=2 should pick (0,1), (0,2).
    C = np.zeros((4, 4))
    for i, j in [(0, 1), (0, 2), (0, 3)]:
        C[i, j] = C[j, i] = 0.5
    assert pruned_pairs(C, 1.0, max_pairs=2) == [(0, 1), (0, 2)]


def test_cap_applied_log_fires_when_keep_ratio_exceeds_cap(caplog):
    """solve_milp이 실제로 max_pairs에 의해 절삭될 때만 '캡 적용' 로그를 낸다."""
    import logging
    ds, g, S, C = _setup(n=25, j=5, seed=3)
    # n=25 -> total_pairs = 300, keep_ratio=0.15 -> precap=45 > max_pairs=10: 진짜로 캡됨.
    params = MilpParams(max_pairs=10, time_limit=30)
    with caplog.at_level(logging.INFO, logger="core.optimize.milp"):
        solve_milp(g, S, C, params)
    assert any("cap applied" in r.message for r in caplog.records)


def test_cap_applied_log_does_not_false_positive_when_keep_ratio_equals_cap(caplog):
    """최종 리뷰 Minor: keep_ratio만으로 고른 쌍 수가 우연히 max_pairs와 정확히
    같을 때는 max_pairs가 아무것도 잘라내지 않은 것이므로 '캡 적용' 로그가 뜨면
    안 된다(예전 구현은 len(pruned) == max_pairs만 비교해 이 경우도 캡이 걸린
    것처럼 오탐했다)."""
    import logging
    ds, g, S, C = _setup(n=25, j=5, seed=3)
    # n=25 -> total_pairs = 300, keep_ratio=0.15 -> precap = int(0.15*300) = 45.
    # max_pairs를 정확히 45로 맞추면 precap == max_pairs -> 캡이 실제로 아무것도
    # 자르지 않는다(precap이 이미 45였으므로).
    params = MilpParams(max_pairs=45, pair_keep_ratio=0.15, time_limit=30)
    with caplog.at_level(logging.INFO, logger="core.optimize.milp"):
        solve_milp(g, S, C, params)
    assert not any("cap applied" in r.message for r in caplog.records)


def test_pruned_pairs_matches_reference_sort_on_real_fixture():
    """On the real demo fixture, must match the reference sorted() implementation
    (deterministic, reproducible enumeration order) to ensure sweep benchmark numbers
    don't silently diverge due to tie-breaking randomness."""
    # Generate the demo fixture exactly as test_e2e_smoke does
    ds = generate_dataset(100, 20, seed=0)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    C = eng.synergy_matrix()

    n = C.shape[0]
    k = int(0.15 * (n * (n - 1) // 2))

    # Reference: the original sorted() implementation
    reference = sorted([(i, j) for i in range(n) for j in range(i + 1, n)],
                       key=lambda p: -abs(C[p]))[:k]

    # New implementation must produce identical ordered list, not just set
    result = pruned_pairs(C, 0.15, max_pairs=5000)
    assert result == reference, f"Mismatch: {len(result)} vs {len(reference)} or ordering differs"


def test_pruned_pairs_boundary_k_zero():
    """Edge case: keep_ratio=0 should return empty list."""
    C = np.random.default_rng(10).normal(size=(10, 10))
    C = (C + C.T) / 2
    np.fill_diagonal(C, 0.0)
    assert pruned_pairs(C, 0.0, max_pairs=5000) == []
    assert pruned_pairs(C, 0.0, max_pairs=10) == []


def test_pruned_pairs_boundary_k_one():
    """Edge case: asking for exactly 1 pair."""
    C = np.random.default_rng(11).normal(size=(10, 10))
    C = (C + C.T) / 2
    np.fill_diagonal(C, 0.0)
    pairs = pruned_pairs(C, 1.0 / 45, max_pairs=5000)  # C(10,2)=45, so 1/45 ≈ 0.022 -> int(1)
    assert len(pairs) == 1


def test_pruned_pairs_boundary_k_total():
    """Edge case: keep_ratio=1.0 returns all pairs (unless capped)."""
    C = np.ones((5, 5))  # All pairs equally strong
    np.fill_diagonal(C, 0.0)
    total = 5 * 4 // 2  # 10
    pairs = pruned_pairs(C, 1.0, max_pairs=5000)
    assert len(pairs) == total
    # With a cap smaller than total, should return exactly the cap count
    pairs_capped = pruned_pairs(C, 1.0, max_pairs=5)
    assert len(pairs_capped) == 5


def test_service_solver_is_highs_and_cbc_stays_selectable():
    ds, g, S, C = _setup(seed=2)
    from core.optimize.milp import solve_milp_diagnostic
    assert MilpParams().solver == "highs"
    raw = solve_milp_diagnostic(g, S, C, MilpParams(time_limit=60))
    assert raw.evidence.solver_name == "HiGHS"
    assert solve_milp_diagnostic(g, S, C, MilpParams(time_limit=60, solver="cbc")).evidence.solver_name == "CBC"


def test_epsilon_bound_residues_are_snapped_and_counted():
    from core.optimize.milp import BOUND_SNAP_EPS, _snap_bounds
    vals = {"a": 1.0000000000000007, "b": -1e-12, "c": 0.5, "d": 1 + 1e-6, "e": 0.9999999999}
    out, n = _snap_bounds(vals, 0.0, 1.0, integral=True)
    assert out["a"] == 1.0 and out["b"] == 0.0 and out["e"] == 1.0
    assert out["c"] == 0.5 and out["d"] == 1 + 1e-6          # beyond the epsilon -> left for the validator to reject
    assert n == 3 and BOUND_SNAP_EPS == 1e-9


def test_highs_without_any_solution_is_reported_as_no_incumbent():
    """HiGHS fills all variables with 0.0 when it finds nothing; that must not pass as a plan or as a
    validation rejection (review SHOULD-2)."""
    from core.optimize.milp import solve_milp_assessment
    ds = generate_dataset(120, 24, seed=5)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    e = ScoringEngine(g)
    with pytest.raises(RuntimeError, match="no incumbent"):
        solve_milp_assessment(g, e.skill_matrix({}), e.synergy_matrix(), MilpParams(time_limit=0))


def test_snapped_candidate_is_what_the_assessment_judges():
    """The assessment (C1 eligibility and refinement) must see the snapped values, and the evidence must
    say how many values were moved (review SHOULD-1)."""
    from core.optimize.milp import solve_milp_assessment
    ds, g, S, C = _setup(seed=2)
    a = solve_milp_assessment(g, S, C, MilpParams(time_limit=60))
    assert a.native_capture is a.validation_candidate or a.native_capture == a.validation_candidate
    snapped = a.validation_candidate.evidence.options.get("snapped_to_bounds", 0)
    assert snapped >= 0
    if snapped:
        assert 0 < a.validation_candidate.evidence.options["max_snap"] <= 1e-9
    assert all(v in (0.0, 1.0) for v in a.validation_candidate.z.values())
    assert a.accepted is not None
