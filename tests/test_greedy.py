import numpy as np
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine
from core.optimize.greedy import solve_greedy


def _setup(seed=3):
    ds = generate_dataset(30, 8, seed=seed)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    return ds, g, ScoringEngine(g).skill_matrix({})


def test_greedy_respects_availability():
    ds, g, S = _setup()
    plan = solve_greedy(g, S)
    from collections import defaultdict
    load = defaultdict(float)
    months = {j.id: j.months for j in ds.projects}
    for e in plan.entries:
        for m in months[e.project_id]:
            load[(e.person_id, m)] += e.alloc
    avail = {p.id: p.availability for p in ds.people}
    for (pid, m), v in load.items():
        assert v <= avail[pid][m] + 1e-9


def test_greedy_deterministic_and_reports_violations():
    ds, g, S = _setup()
    p1, p2 = solve_greedy(g, S), solve_greedy(g, S)
    assert p1.model_dump() == p2.model_dump()
    assert isinstance(p1.violations, list) and isinstance(p1.unfilled, list)


def test_greedy_violations_populated_deterministic():
    """Deterministic test that greedy violates budget on a tight-budget project.
    Constructs a project whose monthly_budget is far below the cost of its
    required grade composition, forcing greedy to produce violations."""
    from core.domain.models import Person, Project, Grade, Sector, ProjectPhase, SkillRequirement

    # Create people at fixed monthly rates
    people = [
        Person(id="p1", name="Senior1", grade=Grade.SENIOR, monthly_rate=1000,
               skills={"python": 3}, availability=[1.0] * 6),
        Person(id="p2", name="Senior2", grade=Grade.SENIOR, monthly_rate=1000,
               skills={"python": 3}, availability=[1.0] * 6),
    ]

    # Create a project that needs 2 senior staff (cost = 2*1000 = 2000)
    # but has a tiny budget (e.g., 1000), forcing budget violation
    projects = [
        Project(id="tight_proj", name="Tight Budget", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                grade_headcount={Grade.SENIOR: 2}, monthly_budget=1000,
                start_month=0, end_month=1,
                requirements=[SkillRequirement(skill="python", min_level=1, headcount=2)]),
    ]

    # Scoring matrix: both seniors score well
    S = np.array([[0.8],   # p1 -> tight_proj: score 0.8
                  [0.7]])  # p2 -> tight_proj: score 0.7

    # Mock MemoryGraph
    class MockMemoryGraph:
        def __init__(self, people, projects):
            self.people = people
            self.projects = projects
            self.pid_index = {p.id: i for i, p in enumerate(people)}

    g = MockMemoryGraph(people, projects)
    plan = solve_greedy(g, S)

    # Greedy should fill both slots (cost = 1.0*1000 + 1.0*1000 = 2000 > budget 1000)
    # and record a violation
    assert len(plan.violations) > 0, "Expected violations for over-budget project"
    assert "tight_proj" in plan.violations[0], \
        f"Expected 'tight_proj' in violation message, got: {plan.violations[0]}"
    assert "초과" in plan.violations[0], \
        f"Expected '초과' (overage) in violation message, got: {plan.violations[0]}"


def test_greedy_unfilled_populated():
    """Verify that unfilled list is populated when demand exceeds supply.
    Uses seed=4 which is known to produce unfilled slots."""
    ds, g, S = _setup(seed=4)
    plan = solve_greedy(g, S)
    # Seed 4 is known to have unfilled slots
    assert len(plan.unfilled) > 0, f"Expected unfilled slots with seed=4, got empty list"
    # Verify the format contains Korean text
    assert any("미충원" in str(u) for u in plan.unfilled), \
        f"Expected '미충원' in unfilled entries, got: {plan.unfilled}"


def test_greedy_selects_highest_s_candidate():
    """Hand-checked test on controlled data verifying greedy picks highest-S candidate.
    Uses a minimal dataset where we can verify which person gets assigned."""
    from core.domain.models import Person, Project, Grade, Sector, ProjectPhase, SkillRequirement

    # Create minimal dataset: 3 people of same grade, 1 project
    people = [
        Person(id="p1", name="Alice", grade=Grade.SENIOR, monthly_rate=100,
               skills={"python": 3}, availability=[1.0] * 6),
        Person(id="p2", name="Bob", grade=Grade.SENIOR, monthly_rate=100,
               skills={"python": 3}, availability=[1.0] * 6),
        Person(id="p3", name="Charlie", grade=Grade.SENIOR, monthly_rate=100,
               skills={"python": 3}, availability=[1.0] * 6),
    ]
    projects = [
        Project(id="prj1", name="Project 1", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
                grade_headcount={Grade.SENIOR: 1}, monthly_budget=200,
                start_month=0, end_month=1,
                requirements=[SkillRequirement(skill="python", min_level=1, headcount=1)]),
    ]

    # Manually create a scoring matrix where p1 > p2 > p3
    S = np.array([[0.9],   # p1 -> prj1: score 0.9
                  [0.5],   # p2 -> prj1: score 0.5
                  [0.3]])  # p3 -> prj1: score 0.3

    # Mock MemoryGraph
    class MockMemoryGraph:
        def __init__(self, people, projects):
            self.people = people
            self.projects = projects
            self.pid_index = {p.id: i for i, p in enumerate(people)}

    g = MockMemoryGraph(people, projects)
    plan = solve_greedy(g, S)

    # Verify that p1 (highest score 0.9) was selected
    assigned_people = {e.person_id for e in plan.entries if e.project_id == "prj1"}
    assert assigned_people == {"p1"}, \
        f"Expected p1 (score 0.9) to be selected, but got {assigned_people}"
