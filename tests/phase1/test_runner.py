import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict

import pytest

from experiments.phase1.checkpoint import create_or_load_run
from experiments.phase1.runner import run_case_subprocess, run_schedule
from experiments.phase1.schedule import SweepCase


def case(case_id="case-1", slot=1):
    return SweepCase(1, case_id, "test", "input", "cbc", 1, slot)


def command(source):
    return lambda case, attempt_dir, deadline: [sys.executable, "-c", source]


def test_timeout_kills_only_launched_group_and_reaps_child(tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
    try:
        result = run_case_subprocess(case(), tmp_path, 0.1, command(
            "import os,time; print(os.getpid(),os.getpgrp(),flush=True); time.sleep(20)"
        ))
        assert result.status == "DEADLINE_EXCEEDED"
        assert result.pid == result.pgid
        assert result.returncode < 0
        with pytest.raises(ProcessLookupError):
            os.kill(result.pid, 0)
        assert unrelated.poll() is None
        assert result.elapsed_seconds < 2
        assert (tmp_path / "cases/case-1/attempt-001/stdout.log").read_text().strip()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_timeout_kills_descendant_even_when_it_ignores_term(tmp_path):
    result = run_case_subprocess(case(), tmp_path, 0.2, command(
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(20)']); "
        "print(child.pid,flush=True); "
        "time.sleep(20)"
    ))
    assert result.status == "DEADLINE_EXCEEDED"
    # The supervisor records both signals to the session it just launched.
    assert result.signals_sent == [signal.SIGTERM, signal.SIGKILL]
    descendant = int((tmp_path / "cases/case-1/attempt-001/stdout.log").read_text())
    for _ in range(50):
        try:
            os.kill(descendant, 0)
        except ProcessLookupError:
            break
        time.sleep(.01)
    else:
        pytest.fail("test-launched descendant remains alive")


def test_completed_case_ids_are_skipped_and_max_cases_keeps_manifest(tmp_path):
    schedule = (case(), case("case-2"))
    manifest = {"schedule": [asdict(row) for row in schedule]}
    first = run_schedule(schedule, tmp_path, manifest, 10, command("print('ok')"), max_cases=1)
    assert set(first.cases) == {"case-1"}
    second = run_schedule(schedule, tmp_path, manifest, 10, command("print('ok')"), max_cases=1)
    assert set(second.cases) == {"case-1", "case-2"}
    assert second.cases["case-1"]["attempt"] == 1
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest


def test_insufficient_budget_starts_no_child(tmp_path):
    state = run_schedule((case(slot=2),), tmp_path, {}, 1, command("raise AssertionError('started')"))
    assert not state.cases
    assert not (tmp_path / "cases").exists()


def test_command_preparation_time_is_inside_deadline(tmp_path):
    def slow_command(*args):
        time.sleep(0.15)
        return [sys.executable, "-c", "raise AssertionError('started')"]
    result = run_case_subprocess(case(), tmp_path, 0.1, slow_command)
    assert result.status == "DEADLINE_EXCEEDED"
    assert result.pid is None


def test_nonzero_child_exit_is_failed_and_logs_are_saved(tmp_path):
    result = run_case_subprocess(case(), tmp_path, 1, command("import sys; print('bad',file=sys.stderr); sys.exit(3)"))
    assert result.status == "FAILED"
    assert result.returncode == 3
    assert "bad" in (tmp_path / "cases/case-1/attempt-001/stderr.log").read_text()


def test_short_heartbeat_persists_running_reservation(tmp_path):
    state = run_schedule((case(),), tmp_path, {}, 2,
                         command("import time; time.sleep(.12)"), heartbeat_seconds=0.03)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(event["event"] == "HEARTBEAT" for event in events)
    assert state.active_seconds >= 0.12


def test_real_oracle_worker_produces_validated_evidence(tmp_path):
    from experiments.phase1.runner import worker_command
    oracle = SweepCase(1, "tiny-cbc", "oracle", "oracle-one_slot", "cbc", 1, 30, oracle_name="one_slot")
    result = run_case_subprocess(oracle, tmp_path, 30, worker_command)
    assert result.status == "DONE", result
    assert result.payload["validation"]["valid"] is True
    assert result.payload["oracle_parity"] is True
    assert result.payload["objective"] == pytest.approx(0.9)
    assert result.payload["quality_status"] == "BOUND_UNKNOWN"


def test_materialized_input_is_verified_immediately_before_problem(tmp_path):
    from experiments.phase1.scenarios import build_snapshot, SnapshotIntegrityError
    from experiments.phase1.worker import problem_from_materialized
    snapshot = build_snapshot(6, 2, 42, "baseline").materialize()
    problem = problem_from_materialized(snapshot)
    assert problem.graph is snapshot.graph
    assert problem.S is snapshot.S
    snapshot.S.setflags(write=True)
    snapshot.S[0, 0] += 1
    with pytest.raises(SnapshotIntegrityError):
        problem_from_materialized(snapshot)


def test_freezer_writes_unique_inputs_and_reuses_matching_manifest(tmp_path):
    from experiments.phase1.runner import freeze_scheduled_inputs
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 30, 6, 2, 42, "baseline")
    manifest = {"schedule": [asdict(item)]}
    frozen = freeze_scheduled_inputs((item, item), tmp_path, manifest, time.monotonic() + 10)
    path = tmp_path / "inputs/small.json"
    original = path.stat().st_mtime_ns
    assert len(frozen["inputs"]) == 1
    assert len(frozen["inputs"]["small"]["snapshot_sha256"]) == 64
    assert freeze_scheduled_inputs((item,), tmp_path, frozen, time.monotonic() + 10) == frozen
    assert path.stat().st_mtime_ns == original


@pytest.mark.parametrize("corruption", ["missing", "changed", "manifest"])
def test_freezer_rejects_missing_or_mismatched_frozen_input(tmp_path, corruption):
    from experiments.phase1.checkpoint import ManifestMismatch
    from experiments.phase1.runner import freeze_scheduled_inputs
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 30, 6, 2, 42, "baseline")
    frozen = freeze_scheduled_inputs((item,), tmp_path, {}, time.monotonic() + 10)
    path = tmp_path / "inputs/small.json"
    if corruption == "missing":
        path.unlink()
    elif corruption == "changed":
        path.write_text("{}")
    else:
        frozen["inputs"]["small"]["snapshot_sha256"] = "0" * 64
    with pytest.raises(ManifestMismatch):
        freeze_scheduled_inputs((item,), tmp_path, frozen, time.monotonic() + 10)


def test_preparation_is_budgeted_and_child_is_bounded(tmp_path):
    from experiments.phase1.runner import prepare_new_run
    from experiments.phase1.checkpoint import ManifestMismatch
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 30, 6, 2, 42, "baseline")
    manifest = prepare_new_run((item,), tmp_path, {"schedule": [asdict(item)]}, 10)
    state = create_or_load_run(tmp_path, manifest)
    assert 0 < state.active_seconds < 10
    assert "small" in manifest["inputs"]
    assert json.loads((tmp_path / "preparation.json").read_text())["status"] == "DONE"


def test_interrupted_preparation_cannot_reset_budget(tmp_path):
    from experiments.phase1.runner import prepare_new_run
    from experiments.phase1.checkpoint import CheckpointCorrupt
    (tmp_path / "preparation.json").write_text(json.dumps({"status": "RUNNING", "reserved_seconds": 10}))
    with pytest.raises(CheckpointCorrupt):
        prepare_new_run((), tmp_path, {}, 10)


def test_freezer_expired_deadline_writes_no_inputs(tmp_path):
    from experiments.phase1.runner import freeze_scheduled_inputs
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 30, 6, 2, 42, "baseline")
    with pytest.raises(TimeoutError):
        freeze_scheduled_inputs((item,), tmp_path, {}, time.monotonic() - 1)
    assert not (tmp_path / "inputs").exists()


def test_orphan_resume_pauses_without_signaling_or_starting_next_case(tmp_path, monkeypatch):
    from experiments.phase1.checkpoint import reserve_case
    state = create_or_load_run(tmp_path, {})
    reserve_case(state, case())
    def forbidden(*args):
        pytest.fail("resume must not signal a saved process or launch the next case")
    monkeypatch.setattr(os, "killpg", forbidden)
    resumed = run_schedule((case(), case("case-2")), tmp_path, {}, 10, forbidden)
    assert resumed.status == "PAUSED_ORPHANED"
    assert resumed.cases["case-1"]["status"] == "ORPHANED"
    assert "case-2" not in resumed.cases


def test_resume_keeps_integrity_pause(tmp_path):
    from experiments.phase1.checkpoint import save_checkpoint
    state = create_or_load_run(tmp_path, {})
    state.status = "PAUSED_INTEGRITY"
    save_checkpoint(state)
    resumed = run_schedule((case(),), tmp_path, {}, 10, command("raise AssertionError('started')"))
    assert resumed.status == "PAUSED_INTEGRITY"
    assert not resumed.cases


def test_schedule_must_match_manifest(tmp_path):
    from experiments.phase1.checkpoint import ManifestMismatch
    with pytest.raises(ManifestMismatch):
        run_schedule((case(),), tmp_path, {"schedule": [asdict(case("different"))]},
                     10, command("print('must not start')"))


def test_schedule_skips_solver_after_unavailable_oracle(tmp_path):
    oracle = SweepCase(1, "oracle-cbc", "oracle", "oracle-one_slot", "cbc", 1, 1, oracle_name="one_slot")
    larger = SweepCase(2, "larger-cbc", "compatibility", "larger", "cbc", 1, 1, 6, 2, 42, "baseline")
    def unavailable(case, attempt_dir, deadline):
        if case.stage != "oracle":
            pytest.fail("must not start an unavailable solver on larger input")
        return [sys.executable, "-c", "import pathlib,json; pathlib.Path(" + repr(str(attempt_dir / "worker-result.json")) + ").write_text(json.dumps({'status':'UNAVAILABLE'}))"]
    state = run_schedule((oracle, larger), tmp_path, {}, 10, unavailable)
    assert state.cases["larger-cbc"]["status"] == "SKIPPED_DEPENDENCY"


def test_late_result_cannot_keep_quality_pass(tmp_path):
    def factory(case, attempt_dir, deadline):
        return [sys.executable, "-c", "import pathlib,json,time; pathlib.Path(" + repr(str(attempt_dir / "worker-result.json")) + ").write_text(json.dumps({'status':'DONE','quality_pass':True})); time.sleep(1)"]
    result = run_case_subprocess(case(), tmp_path, .1, factory)
    assert result.status == "DEADLINE_EXCEEDED"
    assert result.payload.get("quality_pass", False) is False


def test_corrupt_resume_input_is_rejected_before_skipping_completed_case(tmp_path):
    from experiments.phase1.runner import freeze_scheduled_inputs
    from experiments.phase1.checkpoint import ManifestMismatch
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 1, 6, 2, 42, "baseline")
    manifest = freeze_scheduled_inputs((item,), tmp_path, {}, time.monotonic() + 10)
    run_schedule((item,), tmp_path, manifest, 10, command("print('ok')"))
    (tmp_path / "inputs/small.json").write_text("{}")
    with pytest.raises(ManifestMismatch):
        run_schedule((item,), tmp_path, manifest, 10, command("print('must not start')"))


def test_frozen_nonoracle_worker_uses_matching_input(tmp_path):
    from experiments.phase1.runner import prepare_new_run, worker_command
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 30, 6, 2, 42, "baseline")
    manifest = prepare_new_run((item,), tmp_path, {}, 10)
    result = run_case_subprocess(item, tmp_path, 30, worker_command)
    assert result.status == "DONE", result
    assert result.payload["hashes"] == {key: value for key, value in manifest["inputs"]["small"].items() if key != "file_sha256"}
    assert result.payload["validation"]["valid"]


def test_persistence_time_is_inside_deadline(tmp_path, monkeypatch):
    import experiments.phase1.runner as runner
    original = runner.atomic_write_json
    def slow(path, data):
        if path.name == "result.json":
            time.sleep(.12)
        original(path, data)
    monkeypatch.setattr(runner, "atomic_write_json", slow)
    result = run_case_subprocess(case(), tmp_path, .1, command("print('ok')"))
    assert result.status == "DEADLINE_EXCEEDED"


def test_startup_time_is_charged_before_budget_decision(tmp_path):
    state = run_schedule((case(slot=1),), tmp_path, {}, 2,
                         command("raise AssertionError('must not start')"),
                         startup_active_seconds=1.5)
    assert state.active_seconds >= 1.5
    assert not state.cases


def test_over_limit_rss_sample_kills_only_owned_group(tmp_path):
    result = run_case_subprocess(case(), tmp_path, 2,
        command("import time; time.sleep(20)"), rss_sampler=lambda pgid: 13 * 1024**3)
    assert result.status == "MEMORY_LIMIT_EXCEEDED"
    assert result.peak_rss_bytes == 13 * 1024**3
    assert result.rss_sample_status == "OBSERVED"
    assert result.rss_sample_count == 1
    assert result.returncode < 0
    with pytest.raises(ProcessLookupError):
        os.kill(result.pid, 0)


def test_rss_sampling_error_is_explicit_and_not_a_zero_sample(tmp_path):
    def unavailable(pgid):
        raise OSError("simulated sampling denied")
    result = run_case_subprocess(case(), tmp_path, 1,
        command("import time; time.sleep(.05)"), rss_sampler=unavailable)
    assert result.status == "DONE"
    assert result.rss_sample_status == "UNAVAILABLE"
    assert result.peak_rss_bytes is None
    assert result.rss_sample_count == 0
    assert "simulated sampling denied" in result.rss_sample_errors[0]


def test_rss_sampler_sums_only_requested_process_group(monkeypatch):
    from experiments.phase1.runner import sample_owned_rss
    from types import SimpleNamespace
    def ps(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="101 101 1024\n102 101 2048\n999 999 9000000\n", stderr="")
    monkeypatch.setattr(subprocess, "run", ps)
    assert sample_owned_rss(101) == 3 * 1024**2


@pytest.mark.parametrize("invalid", [True, None, -1, 1.5])
def test_invalid_rss_is_unavailable_not_a_valid_observation(tmp_path, invalid):
    result = run_case_subprocess(case(), tmp_path, 1,
        command("import time; time.sleep(.05)"), rss_sampler=lambda pgid: invalid)
    assert result.rss_sample_status == "UNAVAILABLE"
    assert result.peak_rss_bytes is None
    assert result.rss_sample_count == 0
    assert result.rss_sample_errors


@pytest.mark.parametrize("error_first", [False, True])
def test_mixed_rss_samples_remain_partial_and_persist_errors(tmp_path, error_first):
    samples = iter([OSError("sampling denied"), 4096] if error_first
                   else [4096, OSError("sampling denied")])
    def sampler(pgid):
        value = next(samples, 4096)
        if isinstance(value, Exception):
            raise value
        return value
    result = run_case_subprocess(case(), tmp_path, 2,
        command("import time; time.sleep(.65)"), rss_sampler=sampler)
    stored = json.loads((tmp_path / "cases/case-1/attempt-001/result.json").read_text())
    assert result.status == "DONE"
    assert stored["rss_sample_status"] == "PARTIAL"
    assert stored["peak_rss_bytes"] == 4096
    assert stored["rss_sample_count"] >= 1
    assert "sampling denied" in stored["rss_sample_errors"][0]


def test_reservation_overhead_cannot_shorten_a_registered_slot(tmp_path, monkeypatch):
    import experiments.phase1.runner as runner
    original = runner.reserve_case
    def slow_reserve(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(.2)
        return result
    monkeypatch.setattr(runner, "reserve_case", slow_reserve)
    def forbidden(*args):
        pytest.fail("no shortened slot may start after reservation consumed the margin")
    state = run_schedule((case(slot=1),), tmp_path, {}, 1.1, forbidden)
    assert not state.cases
    assert state.active_seconds >= .2


def test_worker_rejects_changed_file_even_if_snapshot_content_is_unchanged(tmp_path):
    from experiments.phase1.runner import prepare_new_run, worker_command
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 30, 6, 2, 42, "baseline")
    prepare_new_run((item,), tmp_path, {}, 10)
    path = tmp_path / "inputs/small.json"
    path.write_bytes(path.read_bytes() + b"\n")
    result = run_case_subprocess(item, tmp_path, 30, worker_command)
    assert result.status == "INPUT_MISMATCH"
    assert not (tmp_path / "cases/small-cbc/attempt-001/raw-solution.json").exists()


def test_manifest_records_source_revision_and_dependency_versions():
    import importlib.metadata
    from experiments.phase1.runner import build_manifest
    manifest = build_manifest(())
    assert manifest["source_commit"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert manifest["dependency_versions"]["numpy"] == importlib.metadata.version("numpy")
    assert manifest["dependency_versions"]["pulp"] == importlib.metadata.version("pulp")


def test_second_supervisor_cannot_orphan_active_reservation(tmp_path):
    script = (
        "import fcntl,pathlib,time; "
        "from experiments.phase1.checkpoint import create_or_load_run,reserve_case; "
        "from experiments.phase1.schedule import SweepCase; "
        f"path=pathlib.Path({str(tmp_path)!r}); "
        "lock=(path/'.supervisor.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX); "
        "state=create_or_load_run(path,{}); "
        "reserve_case(state,SweepCase(1,'case-1','test','input','cbc',1,1)); "
        "print('ready',flush=True); time.sleep(20)"
    )
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(RuntimeError, match="another supervisor"):
            run_schedule((case(),), tmp_path, {}, 10, command("print('must not start')"))
        checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
        assert checkpoint["cases"]["case-1"]["status"] == "RUNNING"
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close()


def test_memory_limit_pauses_schedule_and_resume_without_starting_more_work(tmp_path, monkeypatch):
    import experiments.phase1.runner as runner
    original = runner.run_case_subprocess
    def over_limit(*args, **kwargs):
        return original(*args, **kwargs, rss_sampler=lambda pgid: 13 * 1024**3)
    monkeypatch.setattr(runner, "run_case_subprocess", over_limit)
    schedule = (case(), case("case-2"))
    state = run_schedule(schedule, tmp_path, {}, 10, command("import time; time.sleep(20)"))
    assert state.status == "PAUSED_RESOURCE"
    assert list(state.cases) == ["case-1"]
    assert state.cases["case-1"]["status"] == "MEMORY_LIMIT_EXCEEDED"
    resumed = run_schedule(schedule, tmp_path, {}, 10, command("raise AssertionError('must not start')"))
    assert resumed.status == "PAUSED_RESOURCE"
    assert list(resumed.cases) == ["case-1"]


def test_worker_rejects_snapshot_for_different_case_identity(tmp_path):
    from dataclasses import replace
    from experiments.phase1.runner import prepare_new_run, worker_command
    item = SweepCase(1, "small-cbc", "primary", "small", "cbc", 1, 30, 6, 2, 42, "baseline")
    prepare_new_run((item,), tmp_path, {}, 10)
    result = run_case_subprocess(replace(item, seed=99), tmp_path, 30, worker_command)
    assert result.status == "INPUT_MISMATCH"
    assert not (tmp_path / "cases/small-cbc/attempt-001/raw-solution.json").exists()
