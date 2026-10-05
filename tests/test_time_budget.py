"""Recommended time limits by size (core/optimize/time_budget.py)."""
import json

from core.optimize.time_budget import TimeBudget, recommend, recommend_from_sweeps


def test_recommend_uses_the_next_larger_measured_size_with_margin():
    table = {100: 60, 200: 240, 300: 600}
    assert recommend(80, table) == TimeBudget(90, 360, True, recommend(80, table).basis)
    assert recommend(150, table).per_solve_s == 360
    assert recommend(300, table).per_solve_s == 900 and recommend(300, table).measured


def test_beyond_the_measured_range_is_a_floor_not_a_promise():
    b = recommend(500, {100: 60, 300: 600})
    assert b.per_solve_s == 900 and b.measured is False and "범위 밖" in b.basis


def test_unmeasured_sizes_are_skipped_and_empty_falls_back():
    assert recommend(150, {100: 60, 200: None, 300: 600}).per_solve_s == 900
    b = recommend(150, {100: None})
    assert b.measured is False and b.per_solve_s == 120


def test_recommend_from_sweeps_takes_the_shortest_limit_within_tolerance(tmp_path):
    d = tmp_path / "n100"
    d.mkdir()
    runs = [{"solver": "highs", "time_limit": 30, "objective": 27.7},
            {"solver": "highs", "time_limit": 60, "objective": 41.5},
            {"solver": "highs", "time_limit": 120, "objective": 41.7},
            {"solver": "cbc", "time_limit": 30, "objective": None}]
    (d / "sweep.json").write_text(json.dumps({"size": 100, "best_objective": 41.7, "runs": runs}))
    assert recommend_from_sweeps(tmp_path) == {100: 60}
    assert recommend_from_sweeps(tmp_path, solver="cbc") == {100: None}


def test_the_measured_table_gives_the_rehearsal_recommendations():
    """2026-10-05 rehearsal: 15 / 30 / 120 s measured -> 30 / 60 / 180 s recommended per solve."""
    assert [recommend(n).per_solve_s for n in (100, 200, 300)] == [30, 60, 180]
    assert recommend(250).per_solve_s == 180 and recommend(400).measured is False
