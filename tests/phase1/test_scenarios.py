import hashlib
import json

import pytest

from experiments.bench import datasets
from experiments.phase1.scenarios import SCENARIOS, build_snapshot


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
    assert set(first.hashes) == {
        "dataset_sha256",
        "parsed_reviews_sha256",
        "skill_sha256",
        "synergy_sha256",
        "pair_scopes_sha256",
        "metrics_sha256",
        "model_sha256",
        "snapshot_sha256",
    }
    assert all(len(value) == 64 for value in first.hashes.values())


@pytest.mark.parametrize(
    "scenario",
    ["availability_pressure", "budget_pressure", "dense_collaboration"],
)
def test_scenario_transformation_never_mutates_cached_base_dataset(scenario):
    before = _cached_dataset_fingerprint()

    transformed = build_snapshot(50, 10, 42, scenario)

    assert transformed.dataset is not datasets.build_scale(50, 10, 42)[0]
    assert _cached_dataset_fingerprint() == before


def test_budget_pressure_multiplies_every_budget_by_exactly_point_eighty_five():
    baseline = build_snapshot(50, 10, 42, "baseline")
    pressured = build_snapshot(50, 10, 42, "budget_pressure")

    assert [
        project.monthly_budget for project in pressured.dataset.projects
    ] == [
        project.monthly_budget * 0.85 for project in baseline.dataset.projects
    ]
    assert pressured.metrics["budget_before"] == [
        project.monthly_budget for project in baseline.dataset.projects
    ]
    assert pressured.metrics["budget_after"] == [
        project.monthly_budget for project in pressured.dataset.projects
    ]


def test_model_hash_changes_when_budget_constraint_coefficients_change():
    baseline = build_snapshot(50, 10, 42, "baseline")
    pressured = build_snapshot(50, 10, 42, "budget_pressure")

    assert pressured.hashes["model_sha256"] != baseline.hashes["model_sha256"]


def test_availability_pressure_scales_only_availability_to_peak_ratio_one():
    baseline = build_snapshot(50, 10, 42, "baseline")
    pressured = build_snapshot(50, 10, 42, "availability_pressure")
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
    assert [project.model_dump() for project in pressured.dataset.projects] == [
        project.model_dump() for project in baseline.dataset.projects
    ]
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
    baseline = build_snapshot(50, 10, 42, "baseline")
    dense = build_snapshot(50, 10, 42, "dense_collaboration")
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
    snapshot = build_snapshot(50, 10, 42, "baseline")

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
