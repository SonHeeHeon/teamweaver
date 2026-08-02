import numpy as np
import pytest
from collections import defaultdict
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import (Grade, Project, ProjectPhase, Sector,
    SkillRequirement)
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine
from core.optimize.milp import MilpParams, solve_milp, pruned_pairs


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
    from core.domain.models import Person
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


@pytest.mark.parametrize("seed", [1, 4])
def test_milp_violations_always_empty(seed):
    """MILP는 slack으로 흡수하므로 violations는 항상 빈 리스트여야 한다 (Greedy와의 구분점)."""
    ds, g, S, C = _setup(seed=seed)
    plan = solve_milp(g, S, C, MilpParams(time_limit=60))
    assert plan.violations == []
    assert isinstance(plan.violations, list)


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
