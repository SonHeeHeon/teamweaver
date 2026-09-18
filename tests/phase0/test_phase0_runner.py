from dataclasses import replace

from core.optimize.milp import MilpParams
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
