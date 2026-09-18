"""Bounded mathematical validation for the base TeamWeaver MILP.

This runner verifies the implemented optimization problem. It intentionally
does not claim that the synthetic score predicts real project outcomes.
"""
from dataclasses import asdict, dataclass
from typing import Callable
import time

import numpy as np
import pulp

from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import (
    CoworkRecord,
    Dataset,
    Grade,
    Person,
    Project,
    ProjectPhase,
    Sector,
    SkillRequirement,
)
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, solve_milp_diagnostic
from core.optimize.validation import validate_raw_solution
from core.scoring.engine import ScoringEngine
from experiments.bench import datasets, harness
from experiments.phase0.oracle import solve_tiny_oracle

TOLERANCE = 1e-6
DEFAULT_MAX_WALL_SECONDS = 240.0


@dataclass(frozen=True)
class OracleCase:
    name: str
    build: Callable[[], tuple[MemoryGraph, np.ndarray, np.ndarray, MilpParams]]
    data_source: str = "synthetic_handcrafted"


def _person(person_id: str, grade: Grade, availability: float = 1.0) -> Person:
    return Person(
        id=person_id,
        name=person_id.upper(),
        grade=grade,
        monthly_rate=1_000,
        skills={"Python": 3},
        availability=[availability] * 6,
    )


def _graph(
    people: list[Person], projects: list[Project], coworks: list[CoworkRecord] | None = None
) -> MemoryGraph:
    dataset = Dataset(people=people, projects=projects, coworks=coworks or [], reviews=[])
    return MemoryGraph.build(dataset, parsed=[])


def _one_slot_case() -> tuple[MemoryGraph, np.ndarray, np.ndarray, MilpParams]:
    people = [_person("p0", Grade.MID), _person("p1", Grade.MID)]
    project = Project(
        id="j0", name="한 자리", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
        start_month=0, end_month=0, grade_headcount={Grade.MID: 1},
        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=1)],
        monthly_budget=5_000,
    )
    return _graph(people, [project]), np.array([[0.9], [0.4]]), np.zeros((2, 2)), MilpParams(
        pair_keep_ratio=0.0, time_limit=30
    )


def _budget_shortfall_case() -> tuple[MemoryGraph, np.ndarray, np.ndarray, MilpParams]:
    people = [_person("p0", Grade.SENIOR), _person("p1", Grade.SENIOR)]
    project = Project(
        id="j0", name="예산 부족", sector=Sector.INTERNAL, phase=ProjectPhase.EXECUTION,
        start_month=0, end_month=0, grade_headcount={Grade.SENIOR: 2},
        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=2)],
        monthly_budget=350,
    )
    return _graph(people, [project]), np.array([[0.9], [0.7]]), np.zeros((2, 2)), MilpParams(
        pair_keep_ratio=0.0, time_limit=30
    )


def _all_terms_case() -> tuple[MemoryGraph, np.ndarray, np.ndarray, MilpParams]:
    people = [_person("p0", Grade.MID), _person("p1", Grade.MID)]
    project = Project(
        id="j0", name="전체 목적함수 항", sector=Sector.INTERNAL,
        phase=ProjectPhase.EXECUTION, start_month=0, end_month=0,
        grade_headcount={Grade.MID: 3},
        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=2)],
        monthly_budget=5_000,
    )
    graph = _graph(
        people,
        [project],
        [CoworkRecord(a_id="p0", b_id="p1", co_months=10, project_count=2)],
    )
    synergy = np.array([[0.0, 0.4], [0.4, 0.0]])
    return graph, np.array([[0.9], [0.8]]), synergy, MilpParams(
        lam=0.3, mu=0.2, pair_keep_ratio=1.0, clique_threshold_months=6,
        slack_penalty=100.0, time_limit=30,
    )


def _generated_tiny_case(seed: int) -> tuple[MemoryGraph, np.ndarray, np.ndarray, MilpParams]:
    rng = np.random.default_rng(seed)
    people = [_person(f"p{i}", Grade.MID) for i in range(3)]
    project = Project(
        id="j0", name=f"생성 시드 {seed}", sector=Sector.INTERNAL,
        phase=ProjectPhase.EXECUTION, start_month=0, end_month=1,
        grade_headcount={Grade.MID: 2},
        requirements=[SkillRequirement(skill="Python", min_level=2, headcount=2)],
        monthly_budget=4_000,
    )
    synergy = np.zeros((3, 3))
    values = rng.uniform(-0.2, 0.8, size=3)
    synergy[0, 1] = synergy[1, 0] = values[0]
    synergy[0, 2] = synergy[2, 0] = values[1]
    synergy[1, 2] = synergy[2, 1] = values[2]
    return _graph(people, [project]), rng.uniform(0.2, 0.95, size=(3, 1)), synergy, MilpParams(
        pair_keep_ratio=1.0, time_limit=30
    )


def default_oracle_cases() -> tuple[OracleCase, ...]:
    return (
        OracleCase("one_slot", _one_slot_case),
        OracleCase("budget_shortfall", _budget_shortfall_case),
        OracleCase("all_objective_terms", _all_terms_case),
        *(OracleCase(f"generated_seed_{seed}", lambda seed=seed: _generated_tiny_case(seed),
                     "synthetic_seeded") for seed in (7, 11, 19)),
    )


def _objective_with_reward_pairs(raw, skill, synergy, params, reward_pairs) -> float:
    n_projects = skill.shape[1]
    skill_term = sum(float(skill[i, j]) * value for (i, j), value in raw.a.items())
    reward_term = params.lam * sum(
        float(synergy[p, q]) * raw.z.get((p, j), 0.0) * raw.z.get((q, j), 0.0)
        for p, q in reward_pairs
        for j in range(n_projects)
    )
    penalty_term = -params.mu * sum(
        raw.z.get((p, j), 0.0) * raw.z.get((q, j), 0.0)
        for p, q in raw.penalty_pairs
        for j in range(n_projects)
    )
    return skill_term + reward_term + penalty_term - params.slack_penalty * sum(raw.slack.values())


def _case_record(case: OracleCase) -> dict:
    started = time.perf_counter()
    built = time.perf_counter()
    graph, skill, synergy, params = case.build()
    built = time.perf_counter() - built
    solved = time.perf_counter()
    cbc = solve_milp_diagnostic(graph, skill, synergy, params)
    solve_seconds = time.perf_counter() - solved
    oracle = solve_tiny_oracle(graph, skill, synergy, params)
    validated = time.perf_counter()
    validation = validate_raw_solution(graph, skill, synergy, params, cbc)
    validate_seconds = time.perf_counter() - validated
    difference = abs(cbc.objective - oracle.objective)
    passed = difference <= TOLERANCE and validation.valid
    return {
        "name": case.name,
        "data_source": case.data_source,
        "cbc_status": cbc.status,
        "cbc_objective": cbc.objective,
        "oracle_objective": oracle.objective,
        "objective_difference": difference,
        "oracle_enumerated_assignments": oracle.enumerated_assignments,
        "validation_valid": validation.valid,
        "validation_issues": [asdict(issue) for issue in validation.issues],
        "objective_components": asdict(validation.objective),
        "model_size": {"variables": cbc.variable_count, "constraints": cbc.constraint_count},
        "timings_seconds": {
            "build": built,
            "solve": solve_seconds,
            "validate": validate_seconds,
            "total": time.perf_counter() - started,
        },
        "passed": passed,
    }


def _monotonic_invariants() -> list[dict]:
    budget_graph, budget_skill, budget_synergy, budget_params = _budget_shortfall_case()
    budget_base = solve_milp_diagnostic(budget_graph, budget_skill, budget_synergy, budget_params)
    budget_graph.projects[0].monthly_budget = 5_000
    budget_raised = solve_milp_diagnostic(budget_graph, budget_skill, budget_synergy, budget_params)

    people = [_person("p0", Grade.MID, availability=0.25), _person("p1", Grade.MID)]
    project = Project(
        id="j0", name="가용성 단조성", sector=Sector.INTERNAL,
        phase=ProjectPhase.EXECUTION, start_month=0, end_month=0,
        grade_headcount={Grade.MID: 2},
        requirements=[SkillRequirement(skill="Python", min_level=1, headcount=2)],
        monthly_budget=5_000,
    )
    availability_graph = _graph(people, [project])
    availability_skill = np.array([[0.9], [0.8]])
    availability_synergy = np.zeros((2, 2))
    availability_params = MilpParams(pair_keep_ratio=0.0, time_limit=30)
    availability_base = solve_milp_diagnostic(
        availability_graph, availability_skill, availability_synergy, availability_params
    )
    availability_graph.people[0].availability = [1.0] * 6
    availability_raised = solve_milp_diagnostic(
        availability_graph, availability_skill, availability_synergy, availability_params
    )

    return [
        {
            "name": "budget_increase",
            "base_objective": budget_base.objective,
            "raised_objective": budget_raised.objective,
            "passed": budget_raised.objective >= budget_base.objective - TOLERANCE,
        },
        {
            "name": "availability_increase",
            "base_objective": availability_base.objective,
            "raised_objective": availability_raised.objective,
            "passed": availability_raised.objective >= availability_base.objective - TOLERANCE,
        },
    ]


def _pair_cap_record() -> dict:
    dataset = generate_dataset(20, 4, seed=42)
    graph = MemoryGraph.build(dataset, parse_reviews_rule_based(dataset.reviews))
    engine = ScoringEngine(graph)
    skill, synergy = engine.skill_matrix({}), engine.synergy_matrix()
    full_params = MilpParams(pair_keep_ratio=1.0, max_pairs=10_000, time_limit=30)
    capped_params = full_params.model_copy(update={"max_pairs": 5})
    full = solve_milp_diagnostic(graph, skill, synergy, full_params)
    capped = solve_milp_diagnostic(graph, skill, synergy, capped_params)
    full_validation = validate_raw_solution(graph, skill, synergy, full_params, full)
    capped_validation = validate_raw_solution(graph, skill, synergy, capped_params, capped)
    capped_full_objective = _objective_with_reward_pairs(
        capped, skill, synergy, full_params, full.reward_pairs
    )
    delta = full.objective - capped_full_objective
    relative_loss = delta / abs(full.objective) * 100 if abs(full.objective) > TOLERANCE else None
    return {
        "data_source": "synthetic_current_generator",
        "full_reward_pairs": len(full.reward_pairs),
        "capped_reward_pairs": len(capped.reward_pairs),
        "full_objective": full.objective,
        "capped_solver_objective": capped.objective,
        "capped_evaluated_under_full_objective": capped_full_objective,
        "objective_delta": delta,
        "relative_loss_pct": relative_loss,
        "validation_valid": full_validation.valid and capped_validation.valid,
        "passed": full_validation.valid and capped_validation.valid and delta >= -TOLERANCE,
    }


def _smoke_record() -> dict:
    started = time.perf_counter()
    dataset, parsed, graph = datasets.build_scale(50, 10, seed=42)
    engine = ScoringEngine(graph)
    skill, synergy = engine.skill_matrix({}), engine.synergy_matrix()
    params = MilpParams(time_limit=60)
    solved = time.perf_counter()
    raw = solve_milp_diagnostic(graph, skill, synergy, params)
    solve_seconds = time.perf_counter() - solved
    validation = validate_raw_solution(graph, skill, synergy, params, raw)
    return {
        "name": "cbc_50x10_seed42",
        "data_source": "synthetic_current_generator",
        "people": len(dataset.people),
        "projects": len(dataset.projects),
        "parsed_reviews": len(parsed),
        "status": raw.status,
        "objective": raw.objective,
        "validation_valid": validation.valid,
        "validation_issues": [asdict(issue) for issue in validation.issues],
        "model_size": {"variables": raw.variable_count, "constraints": raw.constraint_count},
        "timings_seconds": {"solve": solve_seconds, "total": time.perf_counter() - started},
        "passed": raw.status == "Optimal" and validation.valid,
    }


def _refresh_status(result: dict, max_wall_seconds: float, started: float) -> None:
    result["elapsed_seconds"] = time.perf_counter() - started
    passed_cases = all(row.get("passed", False) for row in result["cases"])
    passed_invariants = all(row.get("passed", False) for row in result["invariants"])
    pair_ok = result["pair_cap"] is None or result["pair_cap"]["passed"]
    smoke_ok = result["smoke"] is None or result["smoke"]["passed"]
    within_budget = result["elapsed_seconds"] <= max_wall_seconds
    result["calculation_status"] = "PASS" if (
        not result["failures"] and passed_cases and passed_invariants and pair_ok and smoke_ok and within_budget
    ) else "FAIL"


def run(
    oracle_cases: tuple[OracleCase, ...] | None = None,
    run_invariants: bool = True,
    run_pair_cap: bool = True,
    run_smoke: bool = True,
    max_wall_seconds: float = DEFAULT_MAX_WALL_SECONDS,
    on_case_done: Callable[[dict], None] | None = None,
) -> dict:
    """Run independent Phase 0 checks, retaining failures as evidence."""
    started = time.perf_counter()
    result = {
        "phase": "phase0_model_validation",
        "data_boundary": "All current inputs are synthetic; no project outcome is inferred.",
        "business_validity": "NOT_CALIBRATED",
        "solver": "CBC",
        "available_pulp_solvers": pulp.listSolvers(onlyAvailable=True),
        "cases": [],
        "invariants": [],
        "pair_cap": None,
        "smoke": None,
        "failures": [],
        "completed_cases": 0,
    }

    def checkpoint() -> None:
        _refresh_status(result, max_wall_seconds, started)
        if on_case_done is not None:
            on_case_done(result.copy())

    def run_unit(name: str, callback: Callable[[], None]) -> None:
        if time.perf_counter() - started > max_wall_seconds:
            result["failures"].append({"name": name, "reason": "deadline exceeded before start"})
        else:
            try:
                callback()
            except Exception as exc:  # records evidence and continues to independent cases
                result["failures"].append({"name": name, "reason": f"{type(exc).__name__}: {exc}"})
        result["completed_cases"] += 1
        checkpoint()

    for case in oracle_cases if oracle_cases is not None else default_oracle_cases():
        def record_oracle(case=case):
            row = _case_record(case)
            result["cases"].append(row)
            if not row["passed"]:
                result["failures"].append({"name": case.name, "reason": "oracle or validation check failed"})
        run_unit(case.name, record_oracle)

    if run_invariants:
        def record_invariants():
            result["invariants"] = _monotonic_invariants()
            for row in result["invariants"]:
                if not row["passed"]:
                    result["failures"].append({"name": row["name"], "reason": "monotonicity failed"})
        run_unit("monotonicity", record_invariants)

    if run_pair_cap:
        def record_pair_cap():
            result["pair_cap"] = _pair_cap_record()
            if not result["pair_cap"]["passed"]:
                result["failures"].append({"name": "pair_cap", "reason": "pair-cap validation failed"})
        run_unit("pair_cap", record_pair_cap)

    if run_smoke:
        def record_smoke():
            result["smoke"] = _smoke_record()
            if not result["smoke"]["passed"]:
                result["failures"].append({"name": "cbc_50x10_seed42", "reason": "compatibility smoke failed"})
        run_unit("cbc_50x10_seed42", record_smoke)

    _refresh_status(result, max_wall_seconds, started)
    return result


def main() -> None:
    def save_checkpoint(partial: dict) -> None:
        harness.save_result("phase0_model_validation", partial)

    result = run(on_case_done=save_checkpoint)
    harness.save_result("phase0_model_validation", result)
    print(
        f"phase0={result['calculation_status']} business={result['business_validity']} "
        f"cases={result['completed_cases']} elapsed={result['elapsed_seconds']:.2f}s"
    )
    if result["calculation_status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
