from collections import Counter
from dataclasses import FrozenInstanceError

import pytest

from experiments.phase1.schedule import SOLVERS, build_schedule
from experiments.phase1.scenarios import SCENARIOS


def test_schedule_has_the_pre_registered_slot_counts_and_total_budget():
    schedule = build_schedule()
    counts = Counter((case.stage, case.slot_seconds) for case in schedule)

    assert isinstance(schedule, tuple)
    assert counts == {
        ("oracle", 30): 21,
        ("compatibility", 60): 3,
        ("pilot", 120): 9,
        ("primary", 240): 288,
        ("confirmation", 240): 36,
    }
    assert len(schedule) == 357
    assert sum(case.slot_seconds for case in schedule) == 79_650


def test_primary_schedule_freezes_exactly_forty_eight_inputs_and_three_seeds():
    primary = [case for case in build_schedule() if case.stage == "primary"]
    input_keys = {
        (case.n_people, case.n_projects, case.seed, case.scenario)
        for case in primary
    }

    assert {(case.n_people, case.n_projects) for case in primary} == {
        (50, 10),
        (100, 20),
        (200, 40),
        (300, 60),
    }
    assert {case.scenario for case in primary} == set(SCENARIOS)
    assert {case.seed for case in primary} == {100, 101, 102}
    assert {case.repeat for case in primary} == {1, 2}
    assert len(input_keys) == 48
    assert all(
        sum(case.input_id == input_id for case in primary) == 6
        for input_id in {case.input_id for case in primary}
    )


def test_each_equal_primary_input_repeat_block_is_contiguous_and_cycles_solver_order():
    primary = [case for case in build_schedule() if case.stage == "primary"]
    expected_orders = (
        ("cbc", "highs", "scip"),
        ("highs", "scip", "cbc"),
        ("scip", "cbc", "highs"),
    )

    blocks = [primary[index : index + 3] for index in range(0, len(primary), 3)]
    assert len(blocks) == 96
    for index, block in enumerate(blocks):
        assert len({(case.input_id, case.repeat) for case in block}) == 1
        assert tuple(case.solver_name for case in block) == expected_orders[index % 3]


def test_confirmation_is_the_third_repeat_of_every_300_person_primary_input():
    confirmation = [
        case for case in build_schedule() if case.stage == "confirmation"
    ]

    assert len(confirmation) == 36
    assert {case.repeat for case in confirmation} == {3}
    assert {(case.n_people, case.n_projects) for case in confirmation} == {(300, 60)}
    assert {
        (case.seed, case.scenario) for case in confirmation
    } == {
        (seed, scenario)
        for seed in (100, 101, 102)
        for scenario in SCENARIOS
    }


def test_case_ids_and_schedule_are_deterministic_and_immutable():
    first = build_schedule()
    second = build_schedule()

    assert first == second
    assert SOLVERS == ("cbc", "highs", "scip")
    assert len({case.case_id for case in first}) == 357
    assert [case.ordinal for case in first] == list(range(1, 358))
    with pytest.raises(FrozenInstanceError):
        first[0].slot_seconds = 999
