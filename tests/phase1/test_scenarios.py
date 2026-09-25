from dataclasses import FrozenInstanceError
from decimal import Decimal
import hashlib
import json

import pytest

from experiments.bench import datasets
from experiments.phase1.scenarios import (
    SCENARIOS,
    FrozenBenchmarkInput,
    SnapshotIntegrityError,
    build_snapshot,
)
from experiments.phase1.solvers import _PulpFactory, _build_model
from experiments.phase1.types import BenchmarkProblem


def _cached_dataset_fingerprint(n_people=50, n_projects=10, seed=42):
    dataset, _, _ = datasets.build_scale(n_people, n_projects, seed)
    payload = json.dumps(
        dataset.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _edge_map(dataset):
    return {
        tuple(sorted((record.a_id, record.b_id))): (
            record.co_months,
            record.project_count,
        )
        for record in dataset.coworks
    }


def test_identical_dense_snapshot_arguments_produce_identical_hashes():
    first = build_snapshot(50, 10, 42, "dense_collaboration")
    second = build_snapshot(50, 10, 42, "dense_collaboration")

    assert first.hashes == second.hashes
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert set(first.hashes.as_dict()) == {
        "dataset_sha256",
        "parsed_reviews_sha256",
        "skill_sha256",
        "synergy_sha256",
        "pair_scopes_sha256",
        "metrics_sha256",
        "model_sha256",
        "snapshot_sha256",
    }
    assert all(len(value) == 64 for value in first.hashes.as_dict().values())


@pytest.mark.parametrize(
    "scenario",
    ["availability_pressure", "budget_pressure", "dense_collaboration"],
)
def test_scenario_transformation_never_mutates_cached_base_dataset(scenario):
    before = _cached_dataset_fingerprint()

    transformed = build_snapshot(50, 10, 42, scenario)
    execution = transformed.materialize()

    assert execution.dataset is not datasets.build_scale(50, 10, 42)[0]
    assert _cached_dataset_fingerprint() == before


def test_budget_pressure_round_trip_preserves_literal_exact_decimal_budgets():
    frozen = build_snapshot(50, 10, 42, "budget_pressure")
    restored = FrozenBenchmarkInput.from_json(frozen.to_json())
    pressured = restored.materialize()

    assert [project.monthly_budget_exact for project in pressured.dataset.projects] == [
        Decimal("2842.4"),
        Decimal("4341.8"),
        Decimal("3400.85"),
        Decimal("1738.25"),
        Decimal("5400.9"),
        Decimal("4287.4"),
        Decimal("6551.8"),
        Decimal("6932.6"),
        Decimal("5763"),
        Decimal("4416.6"),
    ]
    assert '"monthly_budget":"3400.85"' in frozen.to_json()
    assert pressured.metrics["budget_before"][2] == "4001"
    assert pressured.metrics["budget_after"][2] == "3400.85"
    pressured.verify_integrity()


@pytest.mark.filterwarnings("ignore:Constructing LpVariable.*:DeprecationWarning")
def test_exact_budget_snapshot_materializes_into_the_real_solver_builder():
    execution = build_snapshot(50, 10, 42, "budget_pressure").materialize()
    problem = BenchmarkProblem(execution.graph, execution.S, execution.C, execution.params)

    built = _build_model(problem, _PulpFactory())

    assert len(built.z) == 500
    assert execution.graph.projects[2].monthly_budget == 3400.85


def test_model_hash_changes_when_budget_constraint_coefficients_change():
    baseline = build_snapshot(50, 10, 42, "baseline")
    pressured = build_snapshot(50, 10, 42, "budget_pressure")

    assert pressured.hashes.model_sha256 != baseline.hashes.model_sha256


def test_availability_pressure_scales_only_availability_to_peak_ratio_one():
    baseline = build_snapshot(50, 10, 42, "baseline").materialize()
    pressured = build_snapshot(50, 10, 42, "availability_pressure").materialize()
    demand = baseline.metrics["monthly_demand"]
    supply = baseline.metrics["monthly_supply"]
    expected_scale = max(
        month_demand / month_supply
        for month_demand, month_supply in zip(demand, supply)
        if month_supply > 0
    )

    assert pressured.metrics["availability_scale"] == expected_scale
    assert pressured.metrics["availability_base_peak_ratio"] == expected_scale
    assert pressured.metrics["availability_target_peak_ratio"] == 1.0
    assert pressured.metrics["peak_demand_supply_ratio"] == pytest.approx(1.0)
    assert pressured.dataset.projects == baseline.dataset.projects
    assert [record.model_dump() for record in pressured.dataset.coworks] == [
        record.model_dump() for record in baseline.dataset.coworks
    ]
    for base_person, pressured_person in zip(
        baseline.dataset.people, pressured.dataset.people
    ):
        assert pressured_person.availability == [
            value * expected_scale for value in base_person.availability
        ]


def test_dense_collaboration_preserves_edges_and_adds_only_absent_pairs():
    baseline = build_snapshot(50, 10, 42, "baseline").materialize()
    dense = build_snapshot(50, 10, 42, "dense_collaboration").materialize()
    baseline_edges = _edge_map(baseline.dataset)
    dense_edges = _edge_map(dense.dataset)

    assert len(dense_edges) == round(5.5 * 50)
    assert len(dense_edges) == len(dense.dataset.coworks)
    assert dense_edges.keys() >= baseline_edges.keys()
    assert all(dense_edges[pair] == values for pair, values in baseline_edges.items())
    assert all(
        1 <= record.co_months <= 18 and 1 <= record.project_count <= 3
        for record in dense.dataset.coworks
    )
    assert dense.metrics["cowork_edge_count"] == round(5.5 * 50)
    assert dense.metrics["average_cowork_degree"] == 11.0


def test_snapshot_contains_real_matrices_pair_scopes_and_supported_scenario_names():
    snapshot = build_snapshot(50, 10, 42, "baseline").materialize()

    assert SCENARIOS == (
        "baseline",
        "availability_pressure",
        "budget_pressure",
        "dense_collaboration",
    )
    assert snapshot.S.shape == (50, 10)
    assert snapshot.C.shape == (50, 50)
    assert snapshot.model_pairs == tuple(
        sorted(set(snapshot.reward_pairs) | set(snapshot.penalty_pairs))
    )
    assert snapshot.metrics["reward_pair_count"] == len(snapshot.reward_pairs)
    assert snapshot.metrics["penalty_pair_count"] == len(snapshot.penalty_pairs)
    assert snapshot.metrics["model_pair_count"] == len(snapshot.model_pairs)
    assert snapshot.metrics["y_variable_count"] == len(snapshot.model_pairs) * 10

    with pytest.raises(ValueError, match="unknown scenario"):
        build_snapshot(50, 10, 42, "result_tuned")


def test_each_materialization_is_fresh_and_initially_integrity_verified():
    frozen = build_snapshot(50, 10, 42, "baseline")

    first = frozen.materialize()
    second = frozen.materialize()

    assert first is not second
    assert first.dataset is not second.dataset
    assert first.S is not second.S
    assert first.metrics is not second.metrics
    first.verify_integrity()
    second.verify_integrity()
    frozen.verify_integrity()


def test_json_load_rejects_payload_that_does_not_match_manifest_digest():
    frozen = build_snapshot(50, 10, 42, "budget_pressure")
    payload = json.loads(frozen.to_json())
    payload["dataset"]["projects"][0]["monthly_budget"] = "0.01"
    payload["dataset"]["projects"][0]["monthly_budget_coefficient"] = "0.01"

    with pytest.raises(SnapshotIntegrityError) as caught:
        FrozenBenchmarkInput.from_json(
            json.dumps(payload),
            expected_snapshot_sha256=frozen.snapshot_sha256,
        )

    assert "snapshot_sha256" in caught.value.mismatches


def test_json_load_rejects_budget_coefficient_inconsistent_with_exact_budget():
    frozen = build_snapshot(50, 10, 42, "budget_pressure")
    payload = json.loads(frozen.to_json())
    payload["dataset"]["projects"][2]["monthly_budget_coefficient"] = "3400.84"

    with pytest.raises(SnapshotIntegrityError) as caught:
        FrozenBenchmarkInput.from_json(json.dumps(payload))

    assert caught.value.mismatches["monthly_budget_coefficient"] == (
        "3400.85", "3400.84"
    )


def test_integrity_rejects_project_budget_mutation_before_execution():
    execution = build_snapshot(50, 10, 42, "budget_pressure").materialize()
    execution.dataset.projects[0].monthly_budget += 0.01

    with pytest.raises(SnapshotIntegrityError) as caught:
        execution.verify_integrity()

    assert "dataset_sha256" in caught.value.mismatches
    assert "model_sha256" in caught.value.mismatches


def test_integrity_rejects_metrics_mutation_and_hashes_are_immutable():
    frozen = build_snapshot(50, 10, 42, "baseline")
    execution = frozen.materialize()
    execution.metrics["cowork_edge_count"] = -1

    with pytest.raises(SnapshotIntegrityError) as caught:
        execution.verify_integrity()
    assert "metrics_sha256" in caught.value.mismatches

    with pytest.raises(FrozenInstanceError):
        frozen.hashes.metrics_sha256 = "0" * 64
    with pytest.raises(FrozenInstanceError):
        execution.hashes = frozen.hashes
    with pytest.raises(FrozenInstanceError):
        frozen._serialized = b"{}"


@pytest.mark.parametrize(
    ("matrix_name", "component"), [("S", "skill_sha256"), ("C", "synergy_sha256")]
)
def test_integrity_rejects_matrix_write_reenable_and_mutation(matrix_name, component):
    execution = build_snapshot(50, 10, 42, "baseline").materialize()
    matrix = getattr(execution, matrix_name)
    matrix.setflags(write=True)

    with pytest.raises(SnapshotIntegrityError) as writable:
        execution.verify_integrity()
    assert component in writable.value.mismatches

    matrix[0, 0] += 0.125
    matrix.setflags(write=False)

    with pytest.raises(SnapshotIntegrityError) as caught:
        execution.verify_integrity()

    assert component in caught.value.mismatches
    assert "model_sha256" in caught.value.mismatches


def test_model_hash_preserves_grade_headcount_insertion_order():
    execution = build_snapshot(50, 10, 42, "baseline").materialize()
    project = next(
        project
        for project in execution.dataset.projects
        if len(project.grade_headcount) > 1
    )
    project.grade_headcount = dict(reversed(tuple(project.grade_headcount.items())))

    with pytest.raises(SnapshotIntegrityError) as caught:
        execution.verify_integrity()

    assert "model_sha256" in caught.value.mismatches
    expected, actual = caught.value.mismatches["model_sha256"]
    assert actual != expected
