from dataclasses import replace

from core.optimize.milp import MilpParams
from core.optimize.validation import validate_raw_solution
from experiments.bench import phase0_model
from tests.phase0.factories import all_terms_fixture, one_project_fixture


def test_runner_records_truth_boundary_and_checkpoints_after_each_case():
    graph, skill, synergy = one_project_fixture()
    checkpoints = []
    case = phase0_model.OracleCase(
        "one-slot",
        lambda: (graph, skill, synergy, MilpParams(pair_keep_ratio=0.0, time_limit=30)),
    )

    result = phase0_model.run(
        oracle_cases=(case,),
        run_invariants=False,
        run_pair_cap=False,
        run_smoke=False,
        on_case_done=lambda partial: checkpoints.append(partial["completed_cases"]),
    )

    assert result["business_validity"] == "NOT_CALIBRATED"
    assert result["calculation_status"] == "PASS"
    assert result["completed_cases"] == 1
    assert checkpoints == [1]
    row = result["cases"][0]
    assert row["oracle_objective"] == 0.9
    assert row["objective_difference"] <= 1e-6
    assert row["validation_valid"] is True
    assert row["data_source"] == "synthetic_handcrafted"


def test_runner_records_one_failed_case_and_continues_to_the_next_case():
    graph, skill, synergy = one_project_fixture()
    failed = phase0_model.OracleCase(
        "broken-input",
        lambda: (_ for _ in ()).throw(ValueError("broken input")),
    )
    valid = phase0_model.OracleCase(
        "one-slot",
        lambda: (graph, skill, synergy, MilpParams(pair_keep_ratio=0.0, time_limit=30)),
    )

    result = phase0_model.run(
        oracle_cases=(failed, valid),
        run_invariants=False,
        run_pair_cap=False,
        run_smoke=False,
    )

    assert result["calculation_status"] == "FAIL"
    assert result["completed_cases"] == 2
    assert result["failures"] == [{"name": "broken-input", "reason": "ValueError: broken input"}]
    assert result["cases"][-1]["name"] == "one-slot"


def test_runner_stops_before_starting_units_when_wall_clock_budget_is_exhausted():
    graph, skill, synergy = one_project_fixture()
    case = phase0_model.OracleCase(
        "one-slot",
        lambda: (graph, skill, synergy, MilpParams(pair_keep_ratio=0.0, time_limit=30)),
    )

    result = phase0_model.run(
        oracle_cases=(case, case),
        run_invariants=False,
        run_pair_cap=False,
        run_smoke=False,
        max_wall_seconds=0.0,
    )

    assert result["completed_cases"] == 0
    assert result["cases"] == []
    assert result["failures"] == [{"name": "one-slot", "reason": "deadline exceeded before start"}]


def test_remaining_wall_clock_time_limits_each_cbc_invocation():
    params = MilpParams(time_limit=30)

    bounded = phase0_model._params_for_remaining_solver_time(params, 2.9)

    assert bounded.time_limit == 2
    assert params.time_limit == 30


def test_smoke_accepts_a_valid_time_limited_incumbent():
    graph, skill, synergy, params, raw = all_terms_fixture()
    validation = validate_raw_solution(graph, skill, synergy, params, raw)

    accepted = phase0_model._has_valid_incumbent(
        replace(raw, status="Not Solved"), validation
    )

    assert accepted is True


def test_runner_records_budget_and_availability_monotonicity():
    result = phase0_model.run(
        oracle_cases=(),
        run_invariants=True,
        run_pair_cap=False,
        run_smoke=False,
    )

    assert result["calculation_status"] == "PASS"
    checks = {row["name"]: row for row in result["invariants"]}
    assert checks["budget_increase"]["passed"] is True
    assert checks["availability_increase"]["passed"] is True
    assert all(row["raised_objective"] >= row["base_objective"] - 1e-6 for row in checks.values())


def test_default_oracle_cases_include_a_partial_pair_pruning_case():
    cases = {case.name: case for case in phase0_model.default_oracle_cases()}

    _, _, _, params = cases["generated_seed_11_partial_pruning"].build()

    assert params.pair_keep_ratio == 1 / 3


def test_pair_cap_comparison_recomputes_missing_reward_products_from_z():
    _, skill, synergy, params, raw = all_terms_fixture()

    full_scope_objective = phase0_model._objective_with_reward_pairs(
        replace(raw, y={}), skill, synergy, params, ((0, 1),)
    )

    assert full_scope_objective == -98.38
