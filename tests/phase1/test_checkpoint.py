import json

import pytest

from experiments.phase1.checkpoint import (
    CaseResult,
    CheckpointCorrupt,
    ManifestMismatch,
    atomic_write_json,
    create_or_load_run,
    heartbeat,
    record_terminal_case,
    reserve_case,
)
from experiments.phase1.schedule import SweepCase


def case(case_id="case-1", slot=1):
    return SweepCase(1, case_id, "test", "input", "cbc", 1, slot)


def test_terminal_result_survives_reload(tmp_path):
    state = create_or_load_run(tmp_path, {"schedule": ["case-1"]})
    reserve_case(state, case())
    record_terminal_case(state, CaseResult("case-1", "DONE", elapsed_seconds=0.2))
    loaded = create_or_load_run(tmp_path, {"schedule": ["case-1"]})
    assert loaded.cases["case-1"]["status"] == "DONE"
    assert loaded.active_seconds == pytest.approx(0.2)
    result = json.loads((tmp_path / "cases/case-1/attempt-001/result.json").read_text())
    assert result["status"] == "DONE"


def test_manifest_mismatch_is_rejected_before_resume_changes(tmp_path):
    state = create_or_load_run(tmp_path, {"model": "a"})
    reserve_case(state, case())
    original = (tmp_path / "checkpoint.json").read_bytes()
    with pytest.raises(ManifestMismatch):
        create_or_load_run(tmp_path, {"model": "b"})
    assert (tmp_path / "checkpoint.json").read_bytes() == original


def test_running_reservation_is_fully_charged_once_and_orphaned(tmp_path):
    state = create_or_load_run(tmp_path, {})
    reserve_case(state, case(slot=30))
    heartbeat(state, "case-1", elapsed_seconds=0.01)
    loaded = create_or_load_run(tmp_path, {})
    assert loaded.active_seconds == 30
    assert loaded.cases["case-1"]["status"] == "ORPHANED"
    assert create_or_load_run(tmp_path, {}).active_seconds == 30


def test_checksum_rejects_changed_active_budget(tmp_path):
    create_or_load_run(tmp_path, {})
    path = tmp_path / "checkpoint.json"
    content = json.loads(path.read_text())
    content["active_seconds"] = -10
    path.write_text(json.dumps(content))
    with pytest.raises(CheckpointCorrupt):
        create_or_load_run(tmp_path, {})


def test_failed_replace_keeps_previous_json_and_removes_temp(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    atomic_write_json(path, {"old": True})
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr("experiments.phase1.checkpoint.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}
    assert list(tmp_path.iterdir()) == [path]


def test_partial_event_tail_is_preserved_and_new_events_remain_parseable(tmp_path):
    state = create_or_load_run(tmp_path, {})
    with (tmp_path / "events.jsonl").open("ab") as stream:
        stream.write(b'{"unfinished":')
    loaded = create_or_load_run(tmp_path, {})
    reserve_case(loaded, case())
    assert (tmp_path / "events.partial").read_bytes() == b'{"unfinished":'
    assert all(json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines())


def test_missing_or_changed_terminal_result_fails_closed(tmp_path):
    state = create_or_load_run(tmp_path, {})
    reserve_case(state, case())
    record_terminal_case(state, CaseResult("case-1", "DONE", elapsed_seconds=0.2))
    (tmp_path / "cases/case-1/attempt-001/result.json").write_text('{"status":"DONE"}')
    with pytest.raises(CheckpointCorrupt):
        create_or_load_run(tmp_path, {})


def test_unclosed_preparation_cannot_be_refunded_by_a_partial_manifest(tmp_path):
    atomic_write_json(tmp_path / "manifest.json", {"preparation_active_seconds": .1})
    atomic_write_json(tmp_path / "preparation.json", {"status": "RUNNING", "reserved_seconds": 30})
    with pytest.raises(CheckpointCorrupt):
        create_or_load_run(tmp_path, {"preparation_active_seconds": .1})
