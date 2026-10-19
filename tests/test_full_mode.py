"""전일 투입(allocation_mode="full") + 사람 단위 동시 수행(2026-10-19 사용자 결정, 설계 .omc/plan/2026-10-19-concurrency.md 3절).

같은 등급(중급) 네 사람, 겹치는 두 사업(j0: 0~2월, j1: 1~3월)이 각 2명씩 원한다. p3는 2월에 육아휴직(가용률 0)이라
두 사업 모두 자격이 없다. 예산은 넉넉하다(별도 시험 제외)."""
from dataclasses import replace

import numpy as np
import pytest

from core.domain.models import Grade, Project, ProjectPhase, Sector, SkillRequirement
from core.evaluate.plan_eval import evaluate_plan
from core.optimize.milp import MilpParams, full_eligible, solve_milp_diagnostic
from core.optimize.types import AssignEntry
from core.optimize.validation import validate_raw_solution
from tests.phase0.factories import _graph, _person

FULL = MilpParams(pair_keep_ratio=0.0, allocation_mode="full", gap=0.0)


def _fixture(concurrent: dict[str, int] | None = None, budget: int = 10_000):
    people = [_person(f"p{k}", Grade.MID) for k in range(4)]
    people[3] = people[3].model_copy(update={"availability": [1.0, 1.0, 0.0, 1.0, 1.0, 1.0]})
    for pid, n in (concurrent or {}).items():
        k = int(pid[1:])
        people[k] = people[k].model_copy(update={"max_concurrent": n})
    projects = [Project(id=f"j{k}", name=f"사업 {k}", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                        start_month=k, end_month=k + 2, grade_headcount={Grade.MID: 2},
                        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                        monthly_budget=budget) for k in range(2)]
    S = np.array([[0.9, 0.8], [0.7, 0.6], [0.5, 0.9], [1.0, 1.0]])
    return _graph(people, projects), S, np.zeros((4, 4))


def _count(entries, pid):
    return sum(1 for e in entries if e.person_id == pid)


def test_eligibility_needs_full_availability_in_every_project_month():
    graph, _, _ = _fixture()
    p3 = graph.people[3]
    assert not full_eligible(p3, graph.projects[0]) and not full_eligible(p3, graph.projects[1])
    assert full_eligible(graph.people[0], graph.projects[0])


def test_full_mode_assigns_whole_people_and_nobody_twice_by_default():
    graph, S, C = _fixture()
    raw = solve_milp_diagnostic(graph, S, C, FULL)
    entries = raw.plan.entries
    assert entries and all(e.alloc == 1.0 for e in entries)
    assert _count(entries, "p3") == 0                       # 휴직 달이 걸려 자격 없음
    assert all(_count(entries, p) <= 1 for p in ("p0", "p1", "p2"))
    assert len(entries) == 3 and raw.plan.unfilled == ["j1:중급:1명 미충원"]      # S 합이 가장 큰 배치(j0에 p0·p1)
    assert validate_raw_solution(graph, S, C, FULL, raw).valid


def test_a_permitted_person_fills_two_overlapping_projects_at_full_rate():
    graph, S, C = _fixture({"p0": 2})
    raw = solve_milp_diagnostic(graph, S, C, FULL)
    entries = raw.plan.entries
    assert _count(entries, "p0") == 2 and not raw.plan.unfilled
    assert all(e.alloc == 1.0 for e in entries)
    # 동시 수행자는 사업마다 단가 전액이 든다
    assert all(sum(1_000 for e in entries if e.project_id == j) == 2_000 for j in ("j0", "j1"))
    assert validate_raw_solution(graph, S, C, FULL, raw).valid


def test_full_time_budget_leaves_a_seat_empty_where_split_allocation_would_fill_it():
    graph, S, C = _fixture(budget=1_500)                     # 두 자리 × 1,000 > 1,500
    full = solve_milp_diagnostic(graph, S, C, FULL)
    assert all(sum(1 for e in full.plan.entries if e.project_id == j) == 1 for j in ("j0", "j1"))
    split = solve_milp_diagnostic(graph, S, C, MilpParams(pair_keep_ratio=0.0, gap=0.0, min_alloc=0.3))
    assert sum(1 for e in split.plan.entries if e.project_id == "j0") == 2          # 예전 방식은 쪼개서 채운다


def test_validator_rejects_part_time_or_leave_month_or_unpermitted_double_seat():
    graph, S, C = _fixture({"p0": 2})
    raw = solve_milp_diagnostic(graph, S, C, FULL)
    codes = lambda r: {i.code for i in validate_raw_solution(graph, S, C, FULL, r).issues}  # noqa: E731
    half = raw.a | {(0, 0): 0.5}
    assert "full_time_allocation" in codes(replace(raw, a=half))
    unpermitted, _, _ = _fixture()                           # 같은 해를 p0 동시 수행 허용 없는 데이터로
    issues = {i.code for i in validate_raw_solution(unpermitted, S, C, FULL, raw).issues}
    assert "concurrent_projects" in issues


def test_full_time_plan_is_exactly_100_percent_despite_solver_residue():
    """2026-10-19(v5 재측정 실측): HiGHS가 z = 1 − 5e-10을 돌려주면 명단이 0.99로 내림되고 경계 정리(1.0)를 본 검증기가 해를 거절했다.
    전일 투입은 배치 = 100% -- 명단과 검증기의 독립 사본 모두 1.0이다."""
    from core.optimize.milp import plan_entries
    graph, S, C = _fixture()
    raw = solve_milp_diagnostic(graph, S, C, FULL)
    near = {k: (1.0 - 5e-10 if v > 0.5 else v) for k, v in raw.z.items()}
    entries = plan_entries(graph, FULL, near, near)
    assert entries and all(e.alloc == 1.0 for e in entries)
    residue = replace(raw, z=near, a=near, plan=raw.plan.model_copy(update={"entries": entries}))
    assert not {i.code for i in validate_raw_solution(graph, S, C, FULL, residue).issues} & {"plan_allocation"}


def test_evaluator_full_rules():
    graph, S, C = _fixture({"p0": 2})
    ok = [AssignEntry(person_id="p0", project_id="j0", alloc=1.0), AssignEntry(person_id="p0", project_id="j1", alloc=1.0)]
    assert not evaluate_plan(graph, S, C, FULL, ok).violations
    v = lambda entries, g=graph: {x.code for x in evaluate_plan(g, S, C, FULL, entries).violations}  # noqa: E731
    assert v([AssignEntry(person_id="p1", project_id="j0", alloc=0.5)]) == {"alloc_range"}
    assert v([AssignEntry(person_id="p3", project_id="j0", alloc=1.0)]) == {"availability"}
    single, _, _ = _fixture()
    assert v(ok, single) == {"concurrent_projects"}
    # 예전 방식(fixed)으로 같은 명단을 보면 예전 규칙 그대로 -- 가용률 합 2.0 > 1.0
    fixed = MilpParams(pair_keep_ratio=0.0)
    assert "availability" in {x.code for x in evaluate_plan(graph, S, C, fixed, ok).violations}


@pytest.mark.parametrize("mode", ["fixed", "monthly"])
def test_old_modes_ignore_person_concurrency(mode):
    """max_concurrent는 전일 투입에서만 본다 -- 예전 방식은 전체 상한(max_concurrent_projects) 그대로."""
    graph, S, C = _fixture()
    a = solve_milp_diagnostic(graph, S, C, MilpParams(pair_keep_ratio=0.0, gap=0.0, allocation_mode=mode))
    assert any(_count(a.plan.entries, p) == 2 for p in ("p0", "p1", "p2"))


def test_optimization_ratio_stays_at_most_one_in_full_mode():
    from core.optimize.metrics import optimization_ratio
    graph, S, C = _fixture({"p0": 2})
    plan = solve_milp_diagnostic(graph, S, C, FULL).plan
    ratio = optimization_ratio(graph, S, plan, FULL)
    assert 0.0 < ratio <= 1.0 + 1e-9


# --- Codex 리뷰(2026-10-19) 회귀: 보강 후보·보강 계산·자격 문턱 ---

def _staffed(concurrent=None):
    """p0만 j0에 들어가 있다. j1 기간(1~3월)은 j0(0~2월)과 겹친다."""
    graph, S, C = _fixture(concurrent)
    return graph, S, C, [AssignEntry(person_id="p0", project_id="j0", alloc=1.0)]


def test_permitted_person_is_a_concurrent_candidate_and_others_are_not():
    from core.evaluate.staffing_sim import rank_candidates
    graph, S, C, entries = _staffed({"p0": 2})
    rows = {r["person_id"]: r for r in rank_candidates(graph, S, C, FULL, entries, "j1", top=None)}
    assert rows["p0"]["source"] == "concurrent" and rows["p0"]["alloc"] == 1.0
    assert "p3" not in rows                                          # 휴직 달 -- 자격 없음
    single, S2, C2, entries2 = _staffed()
    assert "p0" not in {r["person_id"] for r in rank_candidates(single, S2, C2, FULL, entries2, "j1", top=None)}
    pulled = {r["person_id"]: r for r in rank_candidates(single, S2, C2, FULL, entries2, "j1", top=None, include_pull=True)}
    assert pulled["p0"]["source"] == "pull"


def test_monthly_values_on_the_base_plan_do_not_switch_full_time_to_monthly():
    from core.evaluate.staffing_sim import best_additions
    graph, S, C, entries = _staffed()
    people = list(graph.people)
    people[1] = people[1].model_copy(update={"availability": [0.5] * 6})        # 시간제 -- 전일 투입 불가
    graph = _graph(people, list(graph.projects))
    entries = [AssignEntry(person_id="p0", project_id="j0", alloc=1.0, monthly_alloc={0: 1.0, 1: 1.0, 2: 1.0})]
    out = best_additions(graph, S, C, FULL, entries, "j1", 1)
    assert out["accepted"] and all(e.alloc == 1.0 for e in out["entries"])
    assert "p1" not in {e.person_id for e in out["entries"]}
    assert not out["new_violations"]


def test_full_time_threshold_is_the_same_in_model_validator_and_evaluator():
    graph, S, C = _fixture()
    people = list(graph.people)
    people[0] = people[0].model_copy(update={"availability": [0.9999995] * 6})
    graph = _graph(people, list(graph.projects))
    assert not full_eligible(graph.people[0], graph.projects[0])
    entry = [AssignEntry(person_id="p0", project_id="j0", alloc=1.0)]
    assert "availability" in {v.code for v in evaluate_plan(graph, S, C, FULL, entry).violations}
    raw = solve_milp_diagnostic(graph, S, C, FULL)
    assert all(e.person_id != "p0" for e in raw.plan.entries)
    forced = replace(raw, z=raw.z | {(0, 0): 1.0}, a=raw.a | {(0, 0): 1.0})
    assert "availability" in {i.code for i in validate_raw_solution(graph, S, C, FULL, forced).issues}


def test_a_person_whose_project_ends_before_the_target_is_not_concurrent():
    """폴백 리뷰 2라운드 P2: 기간이 겹치지 않는 다른 배치는 동시 수행이 아니다(그 기간엔 비어 있다)."""
    from core.evaluate.staffing_sim import rank_candidates
    graph, S, C = _fixture()
    late = Project(id="j2", name="늦은 사업", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION, start_month=4, end_month=5,
                   grade_headcount={Grade.MID: 1}, requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
                   monthly_budget=10_000)
    graph = _graph(list(graph.people), list(graph.projects) + [late])
    S = np.hstack([S, np.full((4, 1), 0.5)])
    entries = [AssignEntry(person_id="p0", project_id="j0", alloc=1.0)]
    rows = {r["person_id"]: r for r in rank_candidates(graph, S, C, FULL, entries, "j2", top=None)}
    assert rows["p0"]["source"] == "bench" and rows["p0"]["needs_grant"] is False


def test_simple_rule_in_full_mode_assigns_whole_people_without_double_booking():
    from core.optimize.greedy import solve_greedy
    graph, S, C = _fixture()
    plan = solve_greedy(graph, S, full=True)
    assert plan.entries and all(e.alloc == 1.0 for e in plan.entries)
    assert all(_count(plan.entries, p) <= 1 for p in ("p0", "p1", "p2")) and _count(plan.entries, "p3") == 0
    assert not {v.code for v in evaluate_plan(graph, S, C, FULL, plan.entries).violations} - {"budget"}


def test_current_teammates_are_not_penalised_on_their_current_project():
    """2026-10-19 사용자 결정 "지금 사업에서는 감점 안 함": 오래 함께 일한 두 사람이 지금 같은 사업에 있으면 그 사업에 계속 있는 한
    익숙한 쌍 감점이 없다(평가기·MILP·독립 검증기 같은 규칙). 다른 사업에 둘을 함께 넣으면 그대로 감점한다."""
    from scipy.sparse import csr_matrix
    graph, S, C = _fixture()
    cw = np.zeros((4, 4))
    cw[0, 1] = cw[1, 0] = 24
    params = FULL.model_copy(update={"mu": 0.5})
    familiar = replace(graph, cowork_months=csr_matrix(cw), _within_cache={})
    current = replace(familiar, current_teams=frozenset({(0, 1, 0)}))
    on_j0 = [AssignEntry(person_id="p0", project_id="j0", alloc=1.0), AssignEntry(person_id="p1", project_id="j0", alloc=1.0)]
    on_j1 = [AssignEntry(person_id="p0", project_id="j1", alloc=1.0), AssignEntry(person_id="p1", project_id="j1", alloc=1.0)]
    assert evaluate_plan(familiar, S, C, params, on_j0).objective.overfamiliarity == -0.5
    assert evaluate_plan(current, S, C, params, on_j0).objective.overfamiliarity == 0.0
    assert evaluate_plan(current, S, C, params, on_j1).objective.overfamiliarity == -0.5
    for g in (familiar, current):
        raw = solve_milp_diagnostic(g, S, C, params)
        assert validate_raw_solution(g, S, C, params, raw).valid
        assert abs(raw.objective - evaluate_plan(g, S, C, params, raw.plan.entries).objective.total) < 1e-6


def test_graph_lists_current_teams_from_the_roster():
    from pathlib import Path
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    b, rep = load_bundle(Path(__file__).resolve().parents[1] / "demo" / "org-n100-operating")
    ds, parsed = to_dataset(b, rep)
    g = MemoryGraph.build(ds, parsed)
    on = {(c.person_id, c.project_id) for c in ds.current}
    assert g.current_teams and all((g.people[p].id, g.projects[j].id) in on and (g.people[q].id, g.projects[j].id) in on
                                   and p < q for p, q, j in g.current_teams)
