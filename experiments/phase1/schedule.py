"""The immutable, pre-registered execution schedule for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass
import random

from experiments.phase1.scenarios import SCENARIOS


SOLVERS = ("cbc", "highs", "scip")
SCALES = ((50, 10), (100, 20), (200, 40), (300, 60))
PRIMARY_SEEDS = (100, 101, 102)
ORACLE_INPUTS = (
    "one_slot",
    "budget_shortfall",
    "all_objective_terms",
    "generated_seed_7",
    "generated_seed_11",
    "generated_seed_19",
    "generated_seed_11_partial_pruning",
)
_SOLVER_ORDERS = (
    SOLVERS,
    ("highs", "scip", "cbc"),
    ("scip", "cbc", "highs"),
)


@dataclass(frozen=True)
class SweepCase:
    ordinal: int
    case_id: str
    stage: str
    input_id: str
    solver_name: str
    repeat: int
    slot_seconds: int
    n_people: int | None = None
    n_projects: int | None = None
    seed: int | None = None
    scenario: str | None = None
    oracle_name: str | None = None


def _input_id(n_people: int, n_projects: int, seed: int, scenario: str) -> str:
    return f"n{n_people}-p{n_projects}-seed{seed}-{scenario}"


def _shuffled_inputs(n_people: int, n_projects: int):
    blocks = [(seed, scenario) for scenario in SCENARIOS for seed in PRIMARY_SEEDS]
    # A local, scale-specific RNG freezes order without touching global random state.
    random.Random(20260921 + n_people * 1000 + n_projects).shuffle(blocks)
    return blocks


def build_schedule() -> tuple[SweepCase, ...]:
    rows: list[dict] = []

    def add_solver_block(
        *,
        stage: str,
        input_id: str,
        repeat: int,
        slot_seconds: int,
        block_index: int,
        n_people: int | None = None,
        n_projects: int | None = None,
        seed: int | None = None,
        scenario: str | None = None,
        oracle_name: str | None = None,
    ) -> None:
        for solver_name in _SOLVER_ORDERS[block_index % len(_SOLVER_ORDERS)]:
            rows.append(
                {
                    "stage": stage,
                    "input_id": input_id,
                    "solver_name": solver_name,
                    "repeat": repeat,
                    "slot_seconds": slot_seconds,
                    "n_people": n_people,
                    "n_projects": n_projects,
                    "seed": seed,
                    "scenario": scenario,
                    "oracle_name": oracle_name,
                }
            )

    for block_index, oracle_name in enumerate(ORACLE_INPUTS):
        add_solver_block(
            stage="oracle",
            input_id=f"oracle-{oracle_name}",
            repeat=1,
            slot_seconds=30,
            block_index=block_index,
            oracle_name=oracle_name,
        )

    add_solver_block(
        stage="compatibility",
        input_id=_input_id(50, 10, 42, "baseline"),
        repeat=1,
        slot_seconds=60,
        block_index=0,
        n_people=50,
        n_projects=10,
        seed=42,
        scenario="baseline",
    )

    for block_index, (n_people, n_projects) in enumerate(SCALES[1:]):
        add_solver_block(
            stage="pilot",
            input_id=_input_id(n_people, n_projects, 42, "baseline"),
            repeat=1,
            slot_seconds=120,
            block_index=block_index,
            n_people=n_people,
            n_projects=n_projects,
            seed=42,
            scenario="baseline",
        )

    primary_block = 0
    for n_people, n_projects in SCALES:
        for seed, scenario in _shuffled_inputs(n_people, n_projects):
            input_id = _input_id(n_people, n_projects, seed, scenario)
            for repeat in (1, 2):
                add_solver_block(
                    stage="primary",
                    input_id=input_id,
                    repeat=repeat,
                    slot_seconds=240,
                    block_index=primary_block,
                    n_people=n_people,
                    n_projects=n_projects,
                    seed=seed,
                    scenario=scenario,
                )
                primary_block += 1

    confirmation_block = 0
    for seed, scenario in _shuffled_inputs(300, 60):
        add_solver_block(
            stage="confirmation",
            input_id=_input_id(300, 60, seed, scenario),
            repeat=3,
            slot_seconds=240,
            block_index=confirmation_block,
            n_people=300,
            n_projects=60,
            seed=seed,
            scenario=scenario,
        )
        confirmation_block += 1

    schedule = []
    for ordinal, row in enumerate(rows, start=1):
        case_slug = (
            f"{ordinal:03d}-{row['stage']}-{row['input_id']}-"
            f"r{row['repeat']}-{row['solver_name']}"
        )
        schedule.append(SweepCase(ordinal=ordinal, case_id=case_slug, **row))
    return tuple(schedule)
