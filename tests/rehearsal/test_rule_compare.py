"""rehearsal/rule_compare.py: the 'current' rule reproduces the service's over-familiar pairs exactly, and the
report renders error rows (2026-10-06, familiarity rule comparison for the final report)."""
import tempfile
from pathlib import Path


def test_current_rule_equals_the_service_pairs_and_candidates_are_subsets_in_time():
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.ingest.org_profile import generate_org_bundle
    from core.optimize.milp import _overfamiliar_pairs
    from rehearsal.rule_compare import RULES, familiar_pairs
    bundle, report = load_bundle(generate_org_bundle(Path(tempfile.mkdtemp()) / "b", 100, seed=7))
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    current = familiar_pairs(bundle, graph, *RULES["current"][1:])
    assert current == {tuple(sorted(p)) for p in _overfamiliar_pairs(graph, 6)}
    y5 = familiar_pairs(bundle, graph, *RULES["y5m12"][1:])
    y3 = familiar_pairs(bundle, graph, *RULES["y3m12"][1:])
    assert y3 <= y5 <= current and len(y3) < len(current)        # shorter window, higher bar -> fewer pairs


def test_report_renders_rows_with_and_without_a_plan():
    from rehearsal.rule_compare import RULES, render
    ok = {"size": 100, "rule": "y3m12", "seeds": 1, "time_limit": 30, "pairs": 94, "elapsed_s": 30.4,
          "termination": "time_limit_incumbent", "accepted": True, "objective_own_rule": 57.8, "gap_target": 0.01, "best_bound": 64.2, "gap": 0.11,
          "unfilled_seats": 0, "skill": 40.1, "synergy": 18.0, "overfamiliarity": -0.4, "seats": 120,
          "avg_seat_fit": 0.61, "pairs_together": 500, "together_familiar": {"current": 80, "y5m12": 10, "y3m12": 2},
          "strict_total": 41.2, "strict_overfamiliarity": -16.0}
    bad = {"size": 200, "rule": "current", "seeds": 4, "time_limit": 60, "pairs": 9794, "elapsed_s": 61.0,
           "error": "MILP found no incumbent"}
    page = render({"env": {"commit": "abc"}, "started_at": "t0", "finished_at": "t1", "seed": 2026,
                   "rules": {k: list(v) for k, v in RULES.items()}, "runs": [ok, bad]})
    assert "후보 B: 최근 3년 중 12개월 이상" in page and "해 없음: MILP found no incumbent" in page
    assert "80 / 10 / 2 (전체 500쌍)" in page
