"""Deterministic, clone-only inputs for the Phase 1 solver benchmark."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
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
    Grade,
    ParsedReview,
    PeerReview,
    Person,
    ProjectPhase,
    ReviewSection,
    Sector,
    SkillRequirement,
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
_SNAPSHOT_SCHEMA_VERSION = 1
_BUDGET_PRESSURE_FACTOR = Decimal("0.85")


@dataclass
class BenchmarkProject:
    """Project DTO whose budget remains an exact decimal benchmark coefficient."""

    id: str
    name: str
    sector: Sector
    phase: ProjectPhase
    start_month: int
    end_month: int
    grade_headcount: dict[Grade, int]
    requirements: list[SkillRequirement]
    monthly_budget_exact: Decimal
    monthly_budget: float

    @property
    def months(self) -> list[int]:
        return list(range(self.start_month, self.end_month + 1))


@dataclass
class BenchmarkDataset:
    people: list[Person]
    projects: list[BenchmarkProject]
    coworks: list[CoworkRecord]
    reviews: list[PeerReview]


@dataclass(frozen=True)
class SnapshotHashes:
    dataset_sha256: str
    parsed_reviews_sha256: str
    skill_sha256: str
    synergy_sha256: str
    pair_scopes_sha256: str
    metrics_sha256: str
    model_sha256: str
    snapshot_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


class SnapshotIntegrityError(RuntimeError):
    def __init__(self, mismatches: dict[str, tuple[str, str]]):
        names = ", ".join(sorted(mismatches))
        super().__init__(f"benchmark snapshot integrity mismatch: {names}")
        self.mismatches = mismatches


@dataclass(frozen=True)
class MaterializedBenchmarkInput:
    """Fresh executable objects derived from an immutable frozen snapshot."""

    n_people: int
    n_projects: int
    seed: int
    scenario: str
    dataset: BenchmarkDataset
    parsed_reviews: tuple[ParsedReview, ...]
    graph: MemoryGraph
    S: np.ndarray
    C: np.ndarray
    params: MilpParams
    reward_pairs: tuple[tuple[int, int], ...]
    penalty_pairs: tuple[tuple[int, int], ...]
    model_pairs: tuple[tuple[int, int], ...]
    metrics: dict[str, Any]
    _expected_hashes: SnapshotHashes | None = None

    @property
    def hashes(self) -> SnapshotHashes:
        if self._expected_hashes is None:
            raise RuntimeError("materialized snapshot has not been sealed")
        return self._expected_hashes

    @property
    def snapshot_sha256(self) -> str:
        return self.hashes.snapshot_sha256

    def verify_integrity(self) -> bool:
        """Re-hash the objects that will actually be passed to the solver."""

        expected = self.hashes.as_dict()
        actual = _compute_hashes(self).as_dict()
        mismatches = {
            name: (expected[name], actual[name])
            for name in expected
            if expected[name] != actual[name]
        }
        if mismatches:
            raise SnapshotIntegrityError(mismatches)
        return True


@dataclass(frozen=True, init=False)
class FrozenBenchmarkInput:
    """Immutable canonical DTO; each consumer receives a fresh materialization."""

    n_people: int
    n_projects: int
    seed: int
    scenario: str
    _serialized: bytes
    _hashes: SnapshotHashes

    def __init__(self, serialized: str | bytes):
        raw = serialized.encode("utf-8") if isinstance(serialized, str) else serialized
        content = json.loads(raw.decode("utf-8"))
        canonical = _canonical_json(content)
        materialized = _materialize_content(content)
        hashes = _compute_hashes(materialized)
        object.__setattr__(materialized, "_expected_hashes", hashes)
        materialized.verify_integrity()
        object.__setattr__(self, "n_people", materialized.n_people)
        object.__setattr__(self, "n_projects", materialized.n_projects)
        object.__setattr__(self, "seed", materialized.seed)
        object.__setattr__(self, "scenario", materialized.scenario)
        object.__setattr__(self, "_serialized", canonical)
        object.__setattr__(self, "_hashes", hashes)

    @classmethod
    def from_json(
        cls,
        serialized: str | bytes,
        expected_snapshot_sha256: str | None = None,
    ) -> FrozenBenchmarkInput:
        frozen = cls(serialized)
        if (
            expected_snapshot_sha256 is not None
            and frozen.snapshot_sha256 != expected_snapshot_sha256
        ):
            raise SnapshotIntegrityError(
                {
                    "snapshot_sha256": (
                        expected_snapshot_sha256,
                        frozen.snapshot_sha256,
                    )
                }
            )
        return frozen

    @property
    def hashes(self) -> SnapshotHashes:
        return self._hashes

    @property
    def snapshot_sha256(self) -> str:
        return self._hashes.snapshot_sha256

    def to_json(self) -> str:
        return self._serialized.decode("utf-8")

    def materialize(self) -> MaterializedBenchmarkInput:
        materialized = _materialize_content(json.loads(self._serialized))
        object.__setattr__(materialized, "_expected_hashes", self._hashes)
        materialized.verify_integrity()
        return materialized

    def verify_integrity(self) -> bool:
        return self.materialize().verify_integrity()


def _template_text(items: list[str], positive: bool) -> str:
    tone = "뛰어나 함께 일하기 좋았습니다" if positive else "아쉬워 협업에 어려움이 있었습니다"
    return f"{', '.join(items)} 측면이 {tone}."


def _decimal_text(value: Decimal | float | int) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("benchmark snapshots cannot contain non-finite decimals")
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    if not decimal.is_finite():
        raise ValueError("benchmark snapshots cannot contain non-finite decimals")
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", warnings=False)
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal | float):
        return {"$decimal": _decimal_text(value)}
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


def _decode_canonical_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_canonical_value(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$decimal"}:
            return float(value["$decimal"])
        return {key: _decode_canonical_value(item) for key, item in value.items()}
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _clone_dataset(base: Dataset) -> BenchmarkDataset:
    return BenchmarkDataset(
        people=deepcopy(base.people),
        projects=[
            BenchmarkProject(
                id=project.id,
                name=project.name,
                sector=project.sector,
                phase=project.phase,
                start_month=project.start_month,
                end_month=project.end_month,
                grade_headcount=deepcopy(project.grade_headcount),
                requirements=deepcopy(project.requirements),
                monthly_budget_exact=Decimal(project.monthly_budget),
                monthly_budget=float(project.monthly_budget),
            )
            for project in base.projects
        ],
        coworks=deepcopy(base.coworks),
        reviews=deepcopy(base.reviews),
    )


def _person_payload(person: Person) -> dict[str, Any]:
    return {
        "id": person.id,
        "name": person.name,
        "grade": person.grade.value,
        "monthly_rate": person.monthly_rate,
        "skills": [[skill, level] for skill, level in person.skills.items()],
        "availability": [_decimal_text(value) for value in person.availability],
    }


def _project_payload(project: BenchmarkProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "sector": project.sector.value,
        "phase": project.phase.value,
        "start_month": project.start_month,
        "end_month": project.end_month,
        "grade_headcount": [
            [grade.value, count] for grade, count in project.grade_headcount.items()
        ],
        "requirements": [
            {
                "skill": requirement.skill,
                "min_level": requirement.min_level,
                "headcount": requirement.headcount,
            }
            for requirement in project.requirements
        ],
        "monthly_budget": _decimal_text(project.monthly_budget_exact),
        "monthly_budget_coefficient": _decimal_text(project.monthly_budget),
    }


def _review_payload(review: PeerReview) -> dict[str, Any]:
    return {
        "reviewer_id": review.reviewer_id,
        "reviewee_id": review.reviewee_id,
        "positive": {
            "items": list(review.positive.items),
            "text": review.positive.text,
        },
        "negative": {
            "items": list(review.negative.items),
            "text": review.negative.text,
        },
    }


def _dataset_payload(dataset: BenchmarkDataset) -> dict[str, Any]:
    return {
        "people": [_person_payload(person) for person in dataset.people],
        "projects": [_project_payload(project) for project in dataset.projects],
        "coworks": [
            {
                "a_id": record.a_id,
                "b_id": record.b_id,
                "co_months": record.co_months,
                "project_count": record.project_count,
            }
            for record in dataset.coworks
        ],
        "reviews": [_review_payload(review) for review in dataset.reviews],
    }


def _dataset_from_payload(payload: dict[str, Any]) -> BenchmarkDataset:
    return BenchmarkDataset(
        people=[
            Person(
                id=person["id"],
                name=person["name"],
                grade=Grade(person["grade"]),
                monthly_rate=person["monthly_rate"],
                skills=dict(person["skills"]),
                availability=[float(value) for value in person["availability"]],
            )
            for person in payload["people"]
        ],
        projects=[
            BenchmarkProject(
                id=project["id"],
                name=project["name"],
                sector=Sector(project["sector"]),
                phase=ProjectPhase(project["phase"]),
                start_month=project["start_month"],
                end_month=project["end_month"],
                grade_headcount={
                    Grade(grade): count for grade, count in project["grade_headcount"]
                },
                requirements=[
                    SkillRequirement(**requirement)
                    for requirement in project["requirements"]
                ],
                monthly_budget_exact=Decimal(project["monthly_budget"]),
                monthly_budget=_budget_coefficient_from_payload(project),
            )
            for project in payload["projects"]
        ],
        coworks=[CoworkRecord(**record) for record in payload["coworks"]],
        reviews=[
            PeerReview(
                reviewer_id=review["reviewer_id"],
                reviewee_id=review["reviewee_id"],
                positive=ReviewSection(**review["positive"]),
                negative=ReviewSection(**review["negative"]),
            )
            for review in payload["reviews"]
        ],
    )


def _budget_coefficient_from_payload(project: dict[str, Any]) -> float:
    """Use the exact decimal source, rejecting a contradictory recorded coefficient."""

    coefficient = float(Decimal(project["monthly_budget"]))
    expected = _decimal_text(coefficient)
    actual = _decimal_text(float(project["monthly_budget_coefficient"]))
    if expected != actual:
        raise SnapshotIntegrityError(
            {"monthly_budget_coefficient": (expected, actual)}
        )
    return coefficient


def _parsed_payload(parsed_reviews: tuple[ParsedReview, ...]) -> list[dict[str, Any]]:
    return [
        {
            "reviewer_id": review.reviewer_id,
            "reviewee_id": review.reviewee_id,
            "text_polarity": _decimal_text(review.text_polarity),
            "evidence": list(review.evidence),
        }
        for review in parsed_reviews
    ]


def _parsed_from_payload(payload: list[dict[str, Any]]) -> tuple[ParsedReview, ...]:
    return tuple(
        ParsedReview(
            reviewer_id=review["reviewer_id"],
            reviewee_id=review["reviewee_id"],
            text_polarity=float(review["text_polarity"]),
            evidence=review["evidence"],
        )
        for review in payload
    )


def _matrix_payload(matrix: np.ndarray) -> list[list[str]]:
    return [[_decimal_text(value) for value in row] for row in matrix]


def _matrix_from_payload(payload: list[list[str]]) -> np.ndarray:
    matrix = np.array([[float(value) for value in row] for row in payload])
    matrix.setflags(write=False)
    return matrix


def _matrix_integrity_payload(matrix: np.ndarray) -> dict[str, Any]:
    return {
        "values": _matrix_payload(matrix),
        "writeable": bool(matrix.flags.writeable),
    }


def _params_payload(params: MilpParams) -> dict[str, Any]:
    return {
        "lam": _decimal_text(params.lam),
        "mu": _decimal_text(params.mu),
        "min_alloc": _decimal_text(params.min_alloc),
        "clique_threshold_months": params.clique_threshold_months,
        "pair_keep_ratio": _decimal_text(params.pair_keep_ratio),
        "slack_penalty": _decimal_text(params.slack_penalty),
        "time_limit": params.time_limit,
        "gap": _decimal_text(params.gap),
        "max_pairs": params.max_pairs,
    }


def _params_from_payload(payload: dict[str, Any]) -> MilpParams:
    return MilpParams(
        lam=float(payload["lam"]),
        mu=float(payload["mu"]),
        min_alloc=float(payload["min_alloc"]),
        clique_threshold_months=payload["clique_threshold_months"],
        pair_keep_ratio=float(payload["pair_keep_ratio"]),
        slack_penalty=float(payload["slack_penalty"]),
        time_limit=payload["time_limit"],
        gap=float(payload["gap"]),
        max_pairs=payload["max_pairs"],
    )


def _content_payload(snapshot: MaterializedBenchmarkInput) -> dict[str, Any]:
    return {
        "schema_version": _SNAPSHOT_SCHEMA_VERSION,
        "identity": {
            "n_people": snapshot.n_people,
            "n_projects": snapshot.n_projects,
            "seed": snapshot.seed,
            "scenario": snapshot.scenario,
        },
        "dataset": _dataset_payload(snapshot.dataset),
        "parsed_reviews": _parsed_payload(snapshot.parsed_reviews),
        "skill": _matrix_payload(snapshot.S),
        "synergy": _matrix_payload(snapshot.C),
        "params": _params_payload(snapshot.params),
        "reward_pairs": [list(pair) for pair in snapshot.reward_pairs],
        "penalty_pairs": [list(pair) for pair in snapshot.penalty_pairs],
        "model_pairs": [list(pair) for pair in snapshot.model_pairs],
        "metrics": _canonical_value(snapshot.metrics),
    }


def _materialize_content(content: dict[str, Any]) -> MaterializedBenchmarkInput:
    if content.get("schema_version") != _SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported benchmark snapshot schema")
    identity = content["identity"]
    dataset = _dataset_from_payload(content["dataset"])
    parsed_reviews = _parsed_from_payload(content["parsed_reviews"])
    graph = MemoryGraph.build(dataset, list(parsed_reviews))
    return MaterializedBenchmarkInput(
        n_people=identity["n_people"],
        n_projects=identity["n_projects"],
        seed=identity["seed"],
        scenario=identity["scenario"],
        dataset=dataset,
        parsed_reviews=parsed_reviews,
        graph=graph,
        S=_matrix_from_payload(content["skill"]),
        C=_matrix_from_payload(content["synergy"]),
        params=_params_from_payload(content["params"]),
        reward_pairs=tuple(tuple(pair) for pair in content["reward_pairs"]),
        penalty_pairs=tuple(tuple(pair) for pair in content["penalty_pairs"]),
        model_pairs=tuple(tuple(pair) for pair in content["model_pairs"]),
        metrics=_decode_canonical_value(content["metrics"]),
    )


def _monthly_demand(dataset: BenchmarkDataset) -> list[int]:
    return [
        sum(
            sum(project.grade_headcount.values())
            for project in dataset.projects
            if month in project.months
        )
        for month in range(HORIZON_MONTHS)
    ]


def _monthly_supply(dataset: BenchmarkDataset) -> list[float]:
    return [
        sum(person.availability[month] for person in dataset.people)
        for month in range(HORIZON_MONTHS)
    ]


def _peak_demand_supply_ratio(dataset: BenchmarkDataset) -> float:
    ratios = []
    for demand, supply in zip(_monthly_demand(dataset), _monthly_supply(dataset)):
        if demand > 0 and supply <= 0:
            raise ValueError("scenario has demand in a month with zero availability supply")
        if supply > 0:
            ratios.append(demand / supply)
    if not ratios or max(ratios) == 0:
        raise ValueError("scenario has no positive demand/supply ratio")
    return max(ratios)


def _apply_availability_pressure(dataset: BenchmarkDataset) -> float:
    scale = min(1.0, _peak_demand_supply_ratio(dataset))
    for person in dataset.people:
        person.availability = [value * scale for value in person.availability]
    return scale


def _apply_budget_pressure(dataset: BenchmarkDataset) -> None:
    for project in dataset.projects:
        project.monthly_budget_exact *= _BUDGET_PRESSURE_FACTOR
        project.monthly_budget = float(project.monthly_budget_exact)


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
        positive=ReviewSection(items=positive, text=_template_text(positive, True)),
        negative=ReviewSection(items=negative, text=_template_text(negative, False)),
    )


def _apply_dense_collaboration(dataset: BenchmarkDataset, seed: int) -> int:
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
            reviews.append(_review_from_items(reviewer_id, reviewee_id, review_items, rng))
    dataset.reviews = reviews
    return target


def _metrics(
    dataset: BenchmarkDataset,
    C: np.ndarray,
    reward_pairs: tuple[tuple[int, int], ...],
    penalty_pairs: tuple[tuple[int, int], ...],
    model_pairs: tuple[tuple[int, int], ...],
    budget_before: list[Decimal],
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
        "budget_before": [_decimal_text(value) for value in budget_before],
        "budget_after": [
            _decimal_text(project.monthly_budget_exact) for project in dataset.projects
        ],
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


def _model_payload(snapshot: MaterializedBenchmarkInput) -> dict[str, Any]:
    graph = snapshot.graph
    return {
        "params": _params_payload(snapshot.params),
        "person_order": [person.id for person in graph.people],
        "project_order": [project.id for project in graph.projects],
        "skill_coefficients": _matrix_integrity_payload(snapshot.S),
        "synergy_coefficients": _matrix_integrity_payload(snapshot.C),
        "people_constraints": [
            {
                "id": person.id,
                "grade": person.grade.value,
                "monthly_rate": person.monthly_rate,
                "availability": [
                    _decimal_text(value) for value in person.availability
                ],
            }
            for person in graph.people
        ],
        "project_constraints": [
            {
                "id": project.id,
                "months": project.months,
                "grade_headcount": [
                    [grade.value, count]
                    for grade, count in project.grade_headcount.items()
                ],
                "monthly_budget": _decimal_text(project.monthly_budget),
                "monthly_budget_exact": _decimal_text(project.monthly_budget_exact),
            }
            for project in graph.projects
        ],
        "cowork_months": _matrix_payload(graph.cowork_months.toarray()),
        "reward_pairs": [list(pair) for pair in snapshot.reward_pairs],
        "penalty_pairs": [list(pair) for pair in snapshot.penalty_pairs],
        "model_pairs": [list(pair) for pair in snapshot.model_pairs],
        "variable_bounds": {
            "z": [0, 1, "binary"],
            "a": ["0", "1", "continuous"],
            "y": ["0", "1", "continuous"],
            "slack": ["0", None, "continuous"],
        },
    }


def _compute_hashes(snapshot: MaterializedBenchmarkInput) -> SnapshotHashes:
    components = {
        "dataset_sha256": _sha256(_dataset_payload(snapshot.dataset)),
        "parsed_reviews_sha256": _sha256(_parsed_payload(snapshot.parsed_reviews)),
        "skill_sha256": _sha256(_matrix_integrity_payload(snapshot.S)),
        "synergy_sha256": _sha256(_matrix_integrity_payload(snapshot.C)),
        "pair_scopes_sha256": _sha256(
            {
                "reward_pairs": snapshot.reward_pairs,
                "penalty_pairs": snapshot.penalty_pairs,
                "model_pairs": snapshot.model_pairs,
            }
        ),
        "metrics_sha256": _sha256(snapshot.metrics),
        "model_sha256": _sha256(_model_payload(snapshot)),
    }
    components["snapshot_sha256"] = _sha256(
        {
            "n_people": snapshot.n_people,
            "n_projects": snapshot.n_projects,
            "seed": snapshot.seed,
            "scenario": snapshot.scenario,
            "component_hashes": components,
        }
    )
    return SnapshotHashes(**components)


def build_snapshot(
    n_people: int,
    n_projects: int,
    seed: int,
    scenario: str,
) -> FrozenBenchmarkInput:
    """Build and seal one scenario without mutating cached base objects."""

    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    cached_dataset, _, _ = datasets.build_scale(n_people, n_projects, seed)
    dataset = _clone_dataset(cached_dataset)
    budget_before = [project.monthly_budget_exact for project in dataset.projects]
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
    materialized = MaterializedBenchmarkInput(
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
        metrics=_metrics(
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
        ),
    )
    return FrozenBenchmarkInput(_canonical_json(_content_payload(materialized)))
