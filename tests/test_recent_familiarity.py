"""Over-familiar pairs counted in a recent window (user decision 2026-10-06: last 36 months, 12+ months together).
Collaboration (C) keeps the whole lookback; only the penalty pairs use the window."""
import datetime as dt
import tempfile
from pathlib import Path

import pytest

from core.domain.models import CoworkRecord


def _org(n=100, seed=7):
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    from core.ingest.org_profile import generate_org_bundle
    bundle, report = load_bundle(generate_org_bundle(Path(tempfile.mkdtemp()) / "b", n, seed=seed))
    ds, parsed = to_dataset(bundle, report)
    return bundle, ds, MemoryGraph.build(ds, parsed)


def test_months_ago_counts_back_from_the_plan_start():
    from core.ingest.convert import _coworks
    first = dt.date(2026, 10, 1)
    rows = [{"project_code": "P", "person_id": p, "start_date": dt.date(2026, 7, 1), "end_date": dt.date(2026, 9, 30)}
            for p in ("A", "B")]
    (rec,) = _coworks(rows, first - dt.timedelta(days=1))
    assert rec.co_months == 3 and rec.months_ago == [1, 2, 3]        # Sep = 1 month before the plan


def test_cowork_record_rejects_inconsistent_months():
    with pytest.raises(ValueError):
        CoworkRecord(a_id="A", b_id="B", co_months=2, project_count=1, months_ago=[1])
    with pytest.raises(ValueError):
        CoworkRecord(a_id="A", b_id="B", co_months=1, project_count=1, months_ago=[0])
    assert CoworkRecord(a_id="A", b_id="B", co_months=1, project_count=1).months_ago is None


def test_window_counts_only_recent_months_and_keeps_collaboration_on_the_whole_history():
    _, ds, g = _org()
    full = g.cowork_within(None)
    recent = g.cowork_within(36)
    assert full is g.cowork_months                                     # C uses the whole lookback
    assert (recent <= full).toarray().all() and recent.sum() < full.sum()
    (i, j), ago = next(iter(g.cowork_months_ago.items()))
    assert recent[i, j] == sum(1 for a in ago if a <= 36) == recent[j, i]


def test_fixture_style_records_without_months_fall_back_to_totals():
    from core.graph.memory_graph import MemoryGraph
    from core.datagen.fixtures_io import load_fixtures
    from core.config import FIXTURES_DIR
    ds, parsed = load_fixtures(FIXTURES_DIR)
    g = MemoryGraph.build(ds, parsed)
    assert g.cowork_months_ago == {} and g.cowork_within(36) is g.cowork_months


def test_service_validator_and_measurement_agree_on_the_decided_rule():
    from core.optimize.milp import MilpParams, _overfamiliar_pairs
    from core.optimize.validation import _independent_penalty_pairs
    from rehearsal.rule_compare import familiar_pairs
    bundle, _, g = _org()
    params = MilpParams(clique_threshold_months=12, clique_window_months=36)
    service = {tuple(sorted(p)) for p in _overfamiliar_pairs(g, 12, 36)}
    assert service == set(_independent_penalty_pairs(g, params)) == familiar_pairs(bundle, g, 36, 12)
    assert len(service) < len(_overfamiliar_pairs(g, 6))               # far fewer than the old whole-history rule


def test_settings_default_to_the_decision_and_old_files_keep_their_meaning(tmp_path):
    import json
    from api.settings import PlacementSettings, SettingsStore
    s = PlacementSettings()
    assert (s.clique_window_months, s.clique_threshold_months) == (36, 12)
    assert s.to_milp_params().clique_window_months == 36
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"settings": {"clique_threshold_months": 6}, "updated_at": "t"}), "utf-8")
    old = SettingsStore(path).current().settings
    assert old.clique_window_months is None and old.clique_threshold_months == 6     # "6 months in the whole history"


def test_settings_reject_a_threshold_longer_than_the_window():
    from api.settings import PlacementSettings
    with pytest.raises(ValueError):
        PlacementSettings(clique_window_months=6, clique_threshold_months=12)
    assert PlacementSettings(clique_window_months=None, clique_threshold_months=24).clique_window_months is None


def test_cowork_record_rejects_duplicate_months():
    with pytest.raises(ValueError):
        CoworkRecord(a_id="A", b_id="B", co_months=2, project_count=1, months_ago=[1, 1, 2])


def test_rule_compare_patch_accepts_the_service_call_signature():
    """Review SHOULD: the measurement swapped in 2-argument functions; the service now passes the window too."""
    import core.evaluate.plan_eval as plan_eval
    import core.optimize.milp as milp
    import core.optimize.validation as validation
    from rehearsal.rule_compare import _use_rule
    originals = (milp._overfamiliar_pairs, plan_eval._overfamiliar_pairs, validation._independent_penalty_pairs)
    try:
        _use_rule({(0, 1)})
        assert milp._overfamiliar_pairs(None, 12, 36) == {(0, 1)} == plan_eval._overfamiliar_pairs(None, 12)
    finally:
        milp._overfamiliar_pairs, plan_eval._overfamiliar_pairs, validation._independent_penalty_pairs = originals
