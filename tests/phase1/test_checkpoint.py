import json
from types import SimpleNamespace

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


@pytest.mark.parametrize("accounted_elapsed,artifact_status", [
    (.25, "DONE"), (1.25, "DEADLINE_EXCEEDED"),
])
@pytest.mark.parametrize("recovery_crash", [None, "before_replace", "after_replace"])
def test_unacknowledged_accounting_result_recovers_before_hash_validation(
        tmp_path, monkeypatch, accounted_elapsed, artifact_status, recovery_crash):
    import experiments.phase1.checkpoint as checkpoint
    from experiments.phase1.runner import run_schedule

    manifest = {"preparation_active_seconds": .5}
    state = create_or_load_run(tmp_path, manifest)
    state.supervisor_active = True
    reserve_case(state, case())
    original_write = checkpoint.atomic_write_json
    result_writes = 0

    def crash_after_accounting_result(path, data):
        nonlocal result_writes
        original_write(path, data)
        if path.name == "result.json":
            result_writes += 1
            if result_writes == 2:
                raise KeyboardInterrupt("accounting result replaced before checkpoint")

    # Control only the accounting measurement; all persistence remains real.
    monkeypatch.setattr(checkpoint, "time", SimpleNamespace(monotonic=lambda: accounted_elapsed))
    monkeypatch.setattr(checkpoint, "atomic_write_json", crash_after_accounting_result)
    with pytest.raises(KeyboardInterrupt, match="accounting result replaced"):
        record_terminal_case(state, CaseResult("case-1", "DONE", elapsed_seconds=.125,
            payload={"quality_pass": True}), started_monotonic=0)
    before = checkpoint.read_bound_checkpoint(tmp_path, manifest)
    result_path = tmp_path / "cases/case-1/attempt-001/result.json"
    replaced = json.loads(result_path.read_text())
    assert before["supervisor_active"] is True
    assert before["last_reserved_case"] == "case-1"
    assert before["cases"]["case-1"]["status"] == "DONE"
    assert before["active_seconds"] == .625
    assert replaced["status"] == artifact_status
    assert replaced["elapsed_seconds"] == accounted_elapsed
    assert before["cases"]["case-1"]["result_sha256"] != checkpoint.fingerprint(replaced)

    def inspect_or_interrupt_recovery(path, data):
        if path.name == "result.json" and data["status"] == "ORPHANED":
            durable = checkpoint.read_bound_checkpoint(tmp_path, manifest)
            assert durable["cases"]["case-1"]["status"] == "RUNNING"
            assert durable["active_seconds"] == 1.5
            if recovery_crash == "before_replace":
                raise KeyboardInterrupt("orphan recovery before replace")
            original_write(path, data)
            if recovery_crash == "after_replace":
                raise KeyboardInterrupt("orphan recovery after replace")
        else:
            original_write(path, data)

    monkeypatch.setattr(checkpoint, "atomic_write_json", inspect_or_interrupt_recovery)
    if recovery_crash:
        with pytest.raises(KeyboardInterrupt, match="orphan recovery"):
            create_or_load_run(tmp_path, manifest)
        interrupted = checkpoint.read_bound_checkpoint(tmp_path, manifest)
        assert interrupted["cases"]["case-1"]["status"] == "RUNNING"
        assert interrupted["active_seconds"] == 1.5
        monkeypatch.setattr(checkpoint, "atomic_write_json", original_write)
    recovered = create_or_load_run(tmp_path, manifest)
    assert recovered.status == "PAUSED_ORPHANED"
    assert recovered.active_seconds == 1.5
    stored = json.loads(result_path.read_text())
    durable = checkpoint.read_bound_checkpoint(tmp_path, manifest)
    assert stored["status"] == "ORPHANED"
    assert stored["elapsed_seconds"] == 1
    assert stored["payload"]["quality_pass"] is False
    assert durable["status"] == "PAUSED_ORPHANED"
    assert durable["cases"] == recovered.cases
    assert durable["cases"]["case-1"]["result_sha256"] == checkpoint.fingerprint(stored)
    assert create_or_load_run(tmp_path, manifest).active_seconds == 1.5

    def forbidden(*args):
        pytest.fail("orphan recovery started the next case")

    resumed = run_schedule((case(), case("case-2")), tmp_path, manifest, 10, forbidden)
    assert resumed.status == "PAUSED_ORPHANED"
    assert list(resumed.cases) == ["case-1"]


def test_unacknowledged_success_does_not_bypass_earlier_terminal_hash(tmp_path):
    state = create_or_load_run(tmp_path, {})
    state.supervisor_active = True
    reserve_case(state, case("earlier"))
    record_terminal_case(state, CaseResult("earlier", "DONE", elapsed_seconds=.25))
    reserve_case(state, case())
    record_terminal_case(state, CaseResult("case-1", "DONE", elapsed_seconds=.125))
    earlier_path = tmp_path / "cases/earlier/attempt-001/result.json"
    earlier = json.loads(earlier_path.read_text())
    earlier["elapsed_seconds"] = .5
    atomic_write_json(earlier_path, earlier)
    with pytest.raises(CheckpointCorrupt, match="terminal result missing or changed: earlier"):
        create_or_load_run(tmp_path, {})
    # Recovery must still reject the acknowledged corruption on every retry.
    with pytest.raises(CheckpointCorrupt, match="terminal result missing or changed: earlier"):
        create_or_load_run(tmp_path, {})
    assert json.loads(earlier_path.read_text()) == earlier


def test_unclosed_preparation_cannot_be_refunded_by_a_partial_manifest(tmp_path):
    atomic_write_json(tmp_path / "manifest.json", {"preparation_active_seconds": .1})
    atomic_write_json(tmp_path / "preparation.json", {"status": "RUNNING", "reserved_seconds": 30})
    with pytest.raises(CheckpointCorrupt):
        create_or_load_run(tmp_path, {"preparation_active_seconds": .1})
