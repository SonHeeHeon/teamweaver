"""실험 공통: 입력 데이터, 서비스와 같은 설정, 같은 채점기(현행 MILP 목적·제약 평가기)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from api.settings import PlacementSettings
from core.config import FIXTURES_DIR
from core.datagen.fixtures_io import load_fixtures
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based
from core.evaluate.plan_eval import evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams
from core.optimize.types import AssignEntry
from core.scoring.engine import ScoringEngine


@dataclass
class Instance:
    name: str
    graph: MemoryGraph
    S: np.ndarray
    C: np.ndarray
    params: MilpParams


def service_params() -> MilpParams:
    """서비스 기본 배치 설정(관리자 설정 기본값: 최소 투입률 30% 등)."""
    return PlacementSettings().to_milp_params()


def fixture_instance() -> Instance:
    ds, parsed = load_fixtures(FIXTURES_DIR)
    g = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(g)
    return Instance("fixture-100x20", g, eng.skill_matrix({}), eng.synergy_matrix(), service_params())


def synthetic_instance(n: int, j: int, seed: int) -> Instance:
    ds = generate_dataset(n, j, seed=seed)
    g = MemoryGraph.build(ds, parse_reviews_rule_based(ds.reviews))
    eng = ScoringEngine(g)
    return Instance(f"synthetic-{n}x{j}-seed{seed}", g, eng.skill_matrix({}), eng.synergy_matrix(),
                    service_params())


def score_plan(inst: Instance, entries: list[AssignEntry]) -> dict:
    """모든 방법을 같은 잣대로: 현행 MILP 목적 4항과 제약 위반·미충원(core/evaluate/plan_eval)."""
    ev = evaluate_plan(inst.graph, inst.S, inst.C, inst.params, entries)
    return {"objective": ev.objective.total, "skill": ev.objective.skill, "synergy": ev.objective.synergy,
            "unfilled_people": sum(s.missing for s in ev.shortfalls),
            "violations": len(ev.violations), "violation_codes": sorted({v.code for v in ev.violations}),
            "assignments": len(entries)}
