"""Deterministic, clone-only inputs for the Phase 1 solver benchmark."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import random
from typing import Any

import numpy as np
from pydantic import BaseModel

from core.config import load_review_items
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import (
    HORIZON_MONTHS,
    CoworkRecord,
    Dataset,
    ParsedReview,
    PeerReview,
    ReviewSection,
)
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, _overfamiliar_pairs, pruned_pairs
from core.scoring.engine import ScoringEngine
from experiments.bench import datasets


SCENARIOS = (
    "baseline",
    "availability_pressure",
    "budget_pressure",
    "dense_collaboration",
)


@dataclass(frozen=True)
class FrozenBenchmarkInput:
    """One fully derived benchmark input whose identity is content-addressed."""

    n_people: int
    n_projects: int
    seed: int
    scenario: str
    dataset: Dataset
    parsed_reviews: tuple[ParsedReview, ...]
    graph: MemoryGraph
    S: np.ndarray
    C: np.ndarray
    params: MilpParams
    reward_pairs: tuple[tuple[int, int], ...]
    penalty_pairs: tuple[tuple[int, int], ...]
    model_pairs: tuple[tuple[int, int], ...]
    metrics: dict[str, Any]
    hashes: dict[str, str]

    @property
    def snapshot_sha256(self) -> str:
        return self.hashes["snapshot_sha256"]


def _template_text(items: list[str], positive: bool) -> str:
    tone = "뛰어나 함께 일하기 좋았습니다" if positive else "아쉬워 협업에 어려움이 있었습니다"
    return f"{', '.join(items)} 측면이 {tone}."


def _decimal_text(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("benchmark snapshots cannot contain non-finite decimals")
    decimal = Decimal(str(value))
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _canonical_value(value: Any) -> Any:
    """Return a JSON-safe tree with one stable representation for decimals."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", warnings=False)
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float):
        return {"decimal": _decimal_text(value)}
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, str | int | bool):
        return value
    raise TypeError(f"unsupported snapshot value: {type(value).__name__}")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _monthly_demand(dataset: Dataset) -> list[int]:
    return [
        sum(
            sum(project.grade_headcount.values())
            for project in dataset.projects
            if month in project.months
        )
        for month in range(HORIZON_MONTHS)
    ]


def _monthly_supply(dataset: Dataset) -> list[float]:
    return [
        sum(person.availability[month] for person in dataset.people)
        for month in range(HORIZON_MONTHS)
    ]


def _peak_demand_supply_ratio(dataset: Dataset) -> float:
    ratios = []
    for demand, supply in zip(_monthly_demand(dataset), _monthly_supply(dataset)):
        if demand > 0 and supply <= 0:
            raise ValueError("scenario has demand in a month with zero availability supply")
        if supply > 0:
            ratios.append(demand / supply)
    if not ratios or max(ratios) == 0:
        raise ValueError("scenario has no positive demand/supply ratio")
    return max(ratios)


def _apply_availability_pressure(dataset: Dataset) -> float:
    scale = min(1.0, _peak_demand_supply_ratio(dataset))
    for person in dataset.people:
        person.availability = [value * scale for value in person.availability]
    return scale


def _apply_budget_pressure(dataset: Dataset) -> None:
    for project in dataset.projects:
        # The domain generator emits integer currency units, but this experiment's
        # pre-registered transformation is exactly 0.85 rather than rounded money.
        project.monthly_budget = project.monthly_budget * 0.85


def _review_from_items(
    reviewer_id: str,
    reviewee_id: str,
    review_items: list[str],
    rng: random.Random,
) -> PeerReview:
    positive = rng.sample(review_items, rng.randint(1, 5))
    negative = rng.sample(
        [item for item in review_items if item not in positive], rng.randint(1, 5)
    )
    return PeerReview(
        reviewer_id=reviewer_id,
        reviewee_id=reviewee_id,
        positive=ReviewSection(
            items=positive,
            text=_template_text(positive, True),
        ),
        negative=ReviewSection(
            items=negative,
            text=_template_text(negative, False),
        ),
    )


def _apply_dense_collaboration(dataset: Dataset, seed: int) -> int:
    rng = random.Random(seed)
    person_ids = [person.id for person in dataset.people]
    target = round(5.5 * len(person_ids))
    maximum = len(person_ids) * (len(person_ids) - 1) // 2
    if target > maximum:
        raise ValueError(
            f"dense_collaboration target {target} exceeds {maximum} possible pairs"
        )

    existing = {
        tuple(sorted((record.a_id, record.b_id))) for record in dataset.coworks
    }
    absent = [
        (person_ids[left], person_ids[right])
        for left in range(len(person_ids))
        for right in range(left + 1, len(person_ids))
        if (person_ids[left], person_ids[right]) not in existing
    ]
    add_count = target - len(existing)
    if add_count < 0:
        raise ValueError(
            f"base dataset already has {len(existing)} edges above dense target {target}"
        )
    for a_id, b_id in rng.sample(absent, add_count):
        dataset.coworks.append(
            CoworkRecord(
                a_id=a_id,
                b_id=b_id,
                co_months=rng.randint(1, 18),
                project_count=rng.randint(1, 3),
            )
        )

    partners = {person_id: [] for person_id in person_ids}
    for record in dataset.coworks:
        partners[record.a_id].append(record.b_id)
        partners[record.b_id].append(record.a_id)
    review_counts = {
        person_id: sum(review.reviewee_id == person_id for review in dataset.reviews)
        for person_id in person_ids
    }
    review_items = load_review_items()
    reviews = []
    for reviewee_id in person_ids:
        reviewer_ids = sorted(partners[reviewee_id])
        count = review_counts[reviewee_id]
        if not 2 <= count <= 4:
            raise ValueError("base generator review count is outside its fixed 2..4 range")
        if len(reviewer_ids) < count:
            raise ValueError("dense collaboration did not provide enough review partners")
        for reviewer_id in rng.sample(reviewer_ids, count):
            reviews.append(
                _review_from_items(reviewer_id, reviewee_id, review_items, rng)
            )
    dataset.reviews = reviews
    return target


def _metrics(
    dataset: Dataset,
    C: np.ndarray,
    reward_pairs: tuple[tuple[int, int], ...],
    penalty_pairs: tuple[tuple[int, int], ...],
    model_pairs: tuple[tuple[int, int], ...],
    budget_before: list[int],
    availability_base_peak_ratio: float,
    availability_scale: float,
    availability_target_peak_ratio: float | None,
    dense_target: int | None,
) -> dict[str, Any]:
    demand = _monthly_demand(dataset)
    supply = _monthly_supply(dataset)
    grade_demand = []
    for month in range(HORIZON_MONTHS):
        by_grade: dict[str, int] = {}
        for project in dataset.projects:
            if month not in project.months:
                continue
            for grade, count in project.grade_headcount.items():
                by_grade[grade.value] = by_grade.get(grade.value, 0) + count
        grade_demand.append(by_grade)
    total_pairs = len(dataset.people) * (len(dataset.people) - 1) // 2
    nonzero_pairs = int(np.count_nonzero(np.triu(C, k=1)))
    cowork_months = [record.co_months for record in dataset.coworks]
    return {
        "monthly_demand": demand,
        "monthly_supply": supply,
        "monthly_grade_demand": grade_demand,
        "peak_demand_supply_ratio": _peak_demand_supply_ratio(dataset),
        "availability_base_peak_ratio": availability_base_peak_ratio,
        "availability_scale": availability_scale,
        "availability_target_peak_ratio": availability_target_peak_ratio,
        "availability_min": min(
            value for person in dataset.people for value in person.availability
        ),
        "availability_max": max(
            value for person in dataset.people for value in person.availability
        ),
        "budget_before": budget_before,
        "budget_after": [project.monthly_budget for project in dataset.projects],
        "cowork_edge_target": dense_target,
        "cowork_edge_count": len(dataset.coworks),
        "average_cowork_degree": 2.0 * len(dataset.coworks) / len(dataset.people),
        "cowork_months_min": min(cowork_months) if cowork_months else None,
        "cowork_months_max": max(cowork_months) if cowork_months else None,
        "synergy_nonzero_pair_count": nonzero_pairs,
        "synergy_nonzero_pair_ratio": nonzero_pairs / total_pairs if total_pairs else 0.0,
        "reward_pair_count": len(reward_pairs),
        "penalty_pair_count": len(penalty_pairs),
        "model_pair_count": len(model_pairs),
        "y_variable_count": len(model_pairs) * len(dataset.projects),
    }


def build_snapshot(
    n_people: int,
    n_projects: int,
    seed: int,
    scenario: str,
) -> FrozenBenchmarkInput:
    """Build one scenario without ever mutating ``datasets.build_scale`` caches."""

    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")

    cached_dataset, _, _ = datasets.build_scale(n_people, n_projects, seed)
    dataset = deepcopy(cached_dataset)
    budget_before = [project.monthly_budget for project in dataset.projects]
    availability_base_peak_ratio = _peak_demand_supply_ratio(dataset)
    availability_scale = 1.0
    availability_target_peak_ratio = None
    dense_target = None
    if scenario == "availability_pressure":
        availability_scale = _apply_availability_pressure(dataset)
        availability_target_peak_ratio = 1.0
    elif scenario == "budget_pressure":
        _apply_budget_pressure(dataset)
    elif scenario == "dense_collaboration":
        dense_target = _apply_dense_collaboration(dataset, seed)

    parsed_reviews = tuple(parse_reviews_rule_based(dataset.reviews))
    graph = MemoryGraph.build(dataset, list(parsed_reviews))
    scoring = ScoringEngine(graph)
    skill = scoring.skill_matrix({})
    synergy = scoring.synergy_matrix()
    skill.setflags(write=False)
    synergy.setflags(write=False)
    params = MilpParams(
        lam=0.3,
        mu=0.2,
        min_alloc=0.2,
        clique_threshold_months=6,
        pair_keep_ratio=0.15,
        slack_penalty=100.0,
        time_limit=240,
        gap=0.0,
        max_pairs=5000,
    )
    reward_pairs = tuple(pruned_pairs(synergy, params.pair_keep_ratio, params.max_pairs))
    penalty_pairs = tuple(sorted(_overfamiliar_pairs(graph, params.clique_threshold_months)))
    model_pairs = tuple(sorted(set(reward_pairs) | set(penalty_pairs)))
    metrics = _metrics(
        dataset,
        synergy,
        reward_pairs,
        penalty_pairs,
        model_pairs,
        budget_before,
        availability_base_peak_ratio,
        availability_scale,
        availability_target_peak_ratio,
        dense_target,
    )

    model_payload = {
        "params": params,
        "person_order": [person.id for person in dataset.people],
        "project_order": [project.id for project in dataset.projects],
        "skill_coefficients": skill,
        "synergy_coefficients": synergy,
        "people_constraints": [
            {
                "id": person.id,
                "grade": person.grade,
                "monthly_rate": person.monthly_rate,
                "availability": person.availability,
            }
            for person in dataset.people
        ],
        "project_constraints": [
            {
                "id": project.id,
                "months": project.months,
                "grade_headcount": project.grade_headcount,
                "monthly_budget": project.monthly_budget,
            }
            for project in dataset.projects
        ],
        "reward_pairs": reward_pairs,
        "penalty_pairs": penalty_pairs,
        "model_pairs": model_pairs,
        "variable_bounds": {
            "z": [0, 1, "binary"],
            "a": [0.0, 1.0, "continuous"],
            "y": [0.0, 1.0, "continuous"],
            "slack": [0.0, None, "continuous"],
        },
    }
    hashes = {
        "dataset_sha256": _sha256(dataset),
        "parsed_reviews_sha256": _sha256(parsed_reviews),
        "skill_sha256": _sha256(skill),
        "synergy_sha256": _sha256(synergy),
        "pair_scopes_sha256": _sha256(
            {
                "reward_pairs": reward_pairs,
                "penalty_pairs": penalty_pairs,
                "model_pairs": model_pairs,
            }
        ),
        "metrics_sha256": _sha256(metrics),
        "model_sha256": _sha256(model_payload),
    }
    hashes["snapshot_sha256"] = _sha256(
        {
            "n_people": n_people,
            "n_projects": n_projects,
            "seed": seed,
            "scenario": scenario,
            "component_hashes": hashes,
        }
    )
    return FrozenBenchmarkInput(
        n_people=n_people,
        n_projects=n_projects,
        seed=seed,
        scenario=scenario,
        dataset=dataset,
        parsed_reviews=parsed_reviews,
        graph=graph,
        S=skill,
        C=synergy,
        params=params,
        reward_pairs=reward_pairs,
        penalty_pairs=penalty_pairs,
        model_pairs=model_pairs,
        metrics=metrics,
        hashes=hashes,
    )
