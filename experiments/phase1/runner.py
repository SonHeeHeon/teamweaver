"""Serial, bounded subprocess supervisor for the frozen Phase 1 schedule."""

from __future__ import annotations

import argparse
import errno
from dataclasses import asdict
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import time

from experiments.phase1.checkpoint import (
    CaseResult, CheckpointCorrupt, ManifestMismatch, append_event, atomic_write_json, attempt_directory, create_or_load_run,
    heartbeat, record_terminal_case, reserve_case, safe_component, save_checkpoint, utc_now,
)


THREAD_ENV = {name: "1" for name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS",
)}


def freeze_scheduled_inputs(schedule, run_dir, manifest, deadline):
    """Freeze unique non-oracle inputs, or validate an existing frozen manifest.

    Run this in the bounded preparation child. A manifest with an `inputs` key is
    sealed: no missing artifact may be silently regenerated on resume.
    """
    from experiments.phase1.scenarios import build_snapshot, FrozenBenchmarkInput
    run_dir = Path(run_dir)
    inputs = dict(manifest.get("inputs", {}))
    sealed = "inputs" in manifest
    seen = {}
    for case in schedule:
        if case.stage == "oracle":
            continue
        if time.monotonic() >= deadline:
            raise TimeoutError("input preparation deadline exceeded")
        safe_component(case.input_id)
        identity = (case.n_people, case.n_projects, case.seed, case.scenario)
        if case.input_id in seen:
            if seen[case.input_id] != identity:
                raise ManifestMismatch("one input ID refers to different scenario identities")
            continue
        seen[case.input_id] = identity
        path = run_dir / "inputs" / f"{case.input_id}.json"
        if sealed:
            try:
                expected = inputs[case.input_id]
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != expected["file_sha256"]:
                    raise ValueError("frozen input file checksum changed")
                frozen = FrozenBenchmarkInput.from_json(raw, expected["snapshot_sha256"])
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                raise ManifestMismatch(f"frozen input mismatch: {case.input_id}") from exc
        else:
            frozen = build_snapshot(*identity)
            frozen.materialize().verify_integrity()
            if time.monotonic() >= deadline:
                raise TimeoutError("input preparation deadline exceeded")
            atomic_write_json(path, json.loads(frozen.to_json()))
            inputs[case.input_id] = {**frozen.hashes.as_dict(),
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if (frozen.n_people, frozen.n_projects, frozen.seed, frozen.scenario) != identity:
            raise ManifestMismatch(f"frozen input identity mismatch: {case.input_id}")
    if time.monotonic() >= deadline:
        raise TimeoutError("input preparation deadline exceeded")
    return {**manifest, "inputs": inputs}


def prepare_new_run(schedule, run_dir, manifest, max_active_seconds):
    """Durably reserve preparation before launching any generation work.

    An interrupted initial preparation retains its entire budget and fails closed;
    it cannot manufacture a fresh budget by regenerating missing inputs.
    """
    from experiments.phase1.schedule import SweepCase
    if not math.isfinite(max_active_seconds) or not 0 < max_active_seconds <= 86400:
        raise ValueError("max_active_seconds must be in (0, 86400]")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = run_dir / "preparation.json"
    with (run_dir / ".preparation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another process owns input preparation") from exc
        if ledger_path.exists():
            raise CheckpointCorrupt("preparation already reserved; use the existing manifest or a new run ID")
        started = time.monotonic()
        reservation = {"status": "RUNNING", "reserved_seconds": max_active_seconds,
                       "started_at": utc_now()}
        atomic_write_json(ledger_path, reservation)
        prep_case = SweepCase(0, "input-preparation", "preparation", "inputs", "none", 1,
                              max_active_seconds)
        def factory(case, attempt_dir, deadline):
            atomic_write_json(attempt_dir / "freeze-request.json", {
                "schedule": [asdict(item) for item in schedule], "manifest": manifest,
                "run_dir": str(run_dir)})
            return [sys.executable, "-m", "experiments.phase1.worker", "--freeze-request",
                    str(attempt_dir / "freeze-request.json"), "--deadline", str(deadline)]
        result = run_case_subprocess(prep_case, run_dir / "preparation", max_active_seconds,
            factory, heartbeat_callback=lambda elapsed: atomic_write_json(
                ledger_path, {**reservation, "elapsed_seconds": elapsed}))
        elapsed = time.monotonic() - started
        if result.status != "DONE" or "manifest" not in result.payload:
            atomic_write_json(ledger_path, {**reservation, "status": result.status,
                "active_seconds": max(max_active_seconds, elapsed), "error": result.error})
            raise CheckpointCorrupt("bounded input preparation did not complete; full reservation retained")
        frozen_manifest = {**result.payload["manifest"], "preparation_active_seconds": elapsed}
        atomic_write_json(run_dir / "manifest.json", frozen_manifest)
        atomic_write_json(ledger_path, {**reservation, "status": "DONE", "active_seconds": elapsed})
        return frozen_manifest


def _verify_input_files(run_dir, manifest):
    for input_id, expected in manifest.get("inputs", {}).items():
        try:
            path = Path(run_dir) / "inputs" / f"{safe_component(input_id)}.json"
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected["file_sha256"]:
                raise ValueError("file hash changed")
        except (OSError, ValueError, KeyError) as exc:
            raise ManifestMismatch(f"frozen input mismatch: {input_id}") from exc


def worker_command(case, attempt_dir: Path, deadline: float) -> list[str]:
    atomic_write_json(attempt_dir / "case.json", asdict(case))
    return [sys.executable, "-m", "experiments.phase1.worker", "--case-file",
            str(attempt_dir / "case.json"), "--deadline", str(deadline)]


def sample_owned_rss(pgid: int) -> int:
    """Read RSS for the session/group launched by this supervisor, in bytes.

    BSD ps -g selects a process group; GNU ps selects a session for numeric -g.
    The worker starts a new session with SID == PGID. We additionally filter
    every returned row by PGID and never send any signal based on ps output.
    """
    sampled = subprocess.run(
        ["ps", "-o", "pid=,pgid=,rss=", "-g", str(pgid)],
        capture_output=True, text=True, timeout=0.2, check=False)
    if sampled.returncode != 0:
        raise OSError(f"RSS sampling failed: {sampled.stderr.strip()}")
    values = []
    for line in sampled.stdout.splitlines():
        pid, group, rss_kib = (int(value) for value in line.split())
        if group == pgid:
            if rss_kib < 0:
                raise ValueError("negative RSS sample")
            values.append(rss_kib * 1024)
    if not values:
        raise OSError("RSS sample contained no owned process-group members")
    return sum(values)


def _signal_owned_group(process: subprocess.Popen, sig: int, sent: list[int]) -> None:
    # process is the Popen instance created in this invocation, never a restored PID.
    if process.returncode is not None:
        return  # Reaping releases the numeric PID/PGID; it is no longer authority.
    try:
        os.killpg(process.pid, sig)
        sent.append(int(sig))
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin reports EPERM for a group containing only unreaped zombies.
        # Confirm that exact state; a denied/failed inspection stays an error.
        if sys.platform != "darwin" or not _owned_child_exited(process):
            raise
        inspected = subprocess.run(
            ["ps", "-o", "pid=,pgid=,stat=", "-g", str(process.pid)],
            capture_output=True, text=True, timeout=0.2, check=True)
        members = [line.split() for line in inspected.stdout.splitlines()]
        owned = [row for row in members if len(row) == 3 and int(row[1]) == process.pid]
        if not owned or not all(row[2].startswith("Z") for row in owned):
            raise


def _stop_owned_group(process: subprocess.Popen, sent: list[int]) -> None:
    _signal_owned_group(process, signal.SIGTERM, sent)
    time.sleep(0.05)
    # Always escalate for descendants, even if their session leader has exited.
    _signal_owned_group(process, signal.SIGKILL, sent)
    process.wait()


def _owned_child_exited(process: subprocess.Popen) -> bool:
    """Observe exit without reaping: reserve the leader's identity until cleanup."""
    if hasattr(os, "waitid"):
        return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    # macOS exposes kqueue but Python does not expose waitid there.
    queue = select.kqueue()
    try:
        event = select.kevent(process.pid, filter=select.KQ_FILTER_PROC,
                             flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                             fflags=select.KQ_NOTE_EXIT)
        try:
            events = queue.control([event], 1, 0)
        except ProcessLookupError as exc:
            if exc.errno != errno.ESRCH:
                raise
            # An already-exited direct child is still ours and remains unreaped.
            return True
        exited = False
        for observed in events:
            owned = observed.ident == process.pid and observed.filter == select.KQ_FILTER_PROC
            if observed.flags & select.KQ_EV_ERROR:
                if observed.data == errno.ESRCH and owned:
                    exited = True
                elif observed.data:
                    raise OSError(observed.data, os.strerror(observed.data))
                # A zero-data EV_ERROR is an acknowledgement, not exit evidence.
            elif owned and observed.fflags & select.KQ_NOTE_EXIT:
                exited = True
        return exited
    finally:
        queue.close()


def run_case_subprocess(case, run_dir, deadline_seconds, command_factory,
                        *, attempt=1, heartbeat_callback=None,
                        heartbeat_seconds=15.0, rss_sampler=sample_owned_rss) -> CaseResult:
    if not math.isfinite(deadline_seconds) or deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be finite and positive")
    if not 0 < heartbeat_seconds <= 15:
        raise ValueError("heartbeat_seconds must be in (0, 15]")
    started = time.monotonic()
    deadline = started + deadline_seconds
    result = CaseResult(case.case_id, "FAILED", attempt=attempt,
                        deadline_seconds=deadline_seconds, started_at=utc_now())
    attempt_dir = attempt_directory(run_dir, case.case_id, attempt)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    if (attempt_dir / "result.json").exists():
        raise ValueError("attempt already has a result")
    process = None
    next_heartbeat = started + heartbeat_seconds
    next_rss_sample = started
    try:
        command = command_factory(case, attempt_dir, deadline)
        if time.monotonic() >= deadline:
            result.status = "DEADLINE_EXCEEDED"
        else:
            with (attempt_dir / "stdout.log").open("wb") as stdout, (attempt_dir / "stderr.log").open("wb") as stderr:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                           start_new_session=True,
                                           env={**os.environ, **THREAD_ENV})
                result.pid = result.pgid = process.pid
                while True:
                    now = time.monotonic()
                    if now >= deadline:
                        result.status = "DEADLINE_EXCEEDED"
                        cleanup_started = time.monotonic()
                        _stop_owned_group(process, result.signals_sent)
                        result.cleanup_seconds = time.monotonic() - cleanup_started
                        break
                    if _owned_child_exited(process):
                        # A successful leader cannot leave work running in its session.
                        _signal_owned_group(process, signal.SIGKILL, result.signals_sent)
                        result.status = "DONE" if process.wait() == 0 else "FAILED"
                        break
                    if now >= next_rss_sample:
                        rss = None
                        try:
                            rss = rss_sampler(process.pid)
                            if type(rss) is not int or rss < 0:
                                raise ValueError("invalid RSS sample")
                            result.peak_rss_bytes = max(result.peak_rss_bytes or 0, rss)
                            result.rss_sample_count += 1
                            result.rss_sample_status = "PARTIAL" if result.rss_sample_errors else "OBSERVED"
                        except (OSError, ValueError, subprocess.SubprocessError) as exc:
                            rss = None
                            result.rss_sample_errors.append(f"{type(exc).__name__}: {exc}")
                            result.rss_sample_status = "PARTIAL" if result.rss_sample_count else "UNAVAILABLE"
                        if rss is not None and rss > result.rss_limit_bytes:
                            result.status = "MEMORY_LIMIT_EXCEEDED"
                            cleanup_started = time.monotonic()
                            _stop_owned_group(process, result.signals_sent)
                            result.cleanup_seconds = time.monotonic() - cleanup_started
                            break
                        next_rss_sample = now + result.rss_sample_interval_seconds
                    if heartbeat_callback is not None and now >= next_heartbeat:
                        heartbeat_callback(now - started)
                        next_heartbeat = now + heartbeat_seconds
                    time.sleep(min(0.02, max(0, deadline - time.monotonic()),
                                   max(0, next_heartbeat - time.monotonic())
                                   if heartbeat_callback else 0.02))
                result.returncode = process.wait()
        payload_path = attempt_dir / "worker-result.json"
        if result.status == "DONE" and payload_path.exists():
            result.payload = json.loads(payload_path.read_text())
            result.status = result.payload.get("status", "DONE")
        elif result.status == "DONE" and command_factory is worker_command:
            result.status = "FAILED"
            result.error = "worker exited without its durable result"
    except BaseException as exc:
        if process is not None:
            cleanup_started = time.monotonic()
            _stop_owned_group(process, result.signals_sent)
            result.cleanup_seconds += time.monotonic() - cleanup_started
            result.returncode = process.returncode
        if not isinstance(exc, Exception):
            raise
        result.status = "FAILED"
        result.error = f"{type(exc).__name__}: {exc}"
    result.finished_at = utc_now()
    result.elapsed_seconds = time.monotonic() - started
    if result.elapsed_seconds >= deadline_seconds and result.status == "DONE":
        result.status = "DEADLINE_EXCEEDED"
    if result.status != "DONE":
        result.payload["quality_pass"] = False
    atomic_write_json(attempt_dir / "result.json", asdict(result))
    # Persistence belongs to the deadline as well as computation and child startup.
    result.elapsed_seconds = time.monotonic() - started
    if result.elapsed_seconds >= deadline_seconds and result.status == "DONE":
        result.status = "DEADLINE_EXCEEDED"
        result.payload["quality_pass"] = False
    atomic_write_json(attempt_dir / "result.json", asdict(result))
    # The last successful write can itself cross the deadline. Once downgraded,
    # the corrective write is terminal failure and cannot manufacture success.
    elapsed = time.monotonic() - started
    if elapsed >= deadline_seconds and result.status == "DONE":
        result.elapsed_seconds = elapsed
        result.status = "DEADLINE_EXCEEDED"
        result.payload["quality_pass"] = False
        atomic_write_json(attempt_dir / "result.json", asdict(result))
    return result


def run_schedule(schedule, run_dir, manifest, max_active_seconds,
                 command_factory=worker_command, *, max_cases=None, heartbeat_seconds=15.0,
                 startup_active_seconds=0.0):
    if not math.isfinite(max_active_seconds) or not 0 < max_active_seconds <= 86400:
        raise ValueError("max_active_seconds must be in (0, 86400]")
    if max_cases is not None and max_cases < 0:
        raise ValueError("max_cases must be nonnegative")
    if not math.isfinite(startup_active_seconds) or startup_active_seconds < 0:
        raise ValueError("startup_active_seconds must be finite and nonnegative")
    schedule = tuple(schedule)
    if "schedule" in manifest and manifest["schedule"] != [asdict(case) for case in schedule]:
        raise ManifestMismatch("execution schedule differs from the manifest")
    if len({case.case_id for case in schedule}) != len(schedule):
        raise ValueError("schedule contains duplicate case IDs")
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Holding this descriptor across the entire schedule prevents a second supervisor
    # from orphaning a still-running reservation owned by the first supervisor.
    with (run_dir / ".supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another supervisor owns this run") from exc
        marked = time.monotonic()
        _verify_input_files(run_dir, manifest)
        state = create_or_load_run(run_dir, manifest)
        state.active_seconds += startup_active_seconds
        if state.recovered_orphans or any(row["status"] == "ORPHANED" for row in state.cases.values()):
            # A stale PID is insufficient authority to kill a former supervisor's
            # child. Pause rather than overlap it with another solver process.
            state.status = "PAUSED_ORPHANED"
        if state.status.startswith("PAUSED_"):
            state.active_seconds += time.monotonic() - marked
            save_checkpoint(state)
            return state
        count = 0
        for case in schedule:
            if case.case_id in state.cases:
                continue
            if max_cases is not None and count >= max_cases:
                break
            now = time.monotonic()
            state.active_seconds += now - marked
            marked = now
            if state.active_seconds + case.slot_seconds > max_active_seconds:
                break
            reserve_case(state, case)
            # Reservation persistence is supervisor overhead, not free active time.
            launch_started = time.monotonic()
            state.active_seconds += launch_started - marked
            if state.active_seconds > max_active_seconds:
                # This invocation still owns the lock and has not spawned anything.
                # Release only this known-unstarted reservation; crash recovery must
                # continue charging all unknown reservations in full.
                state.active_seconds -= case.slot_seconds
                del state.cases[case.case_id]
                save_checkpoint(state)
                append_event(state, "RESERVATION_RELEASED", case_id=case.case_id,
                             reason="insufficient full-slot budget after reservation persistence")
                marked = launch_started
                break
            failed_dependency = any(
                row["case"]["solver_name"] == case.solver_name
                and row["case"]["stage"] == "oracle"
                and row["status"] not in {"RUNNING", "DONE"}
                for row in state.cases.values())
            if failed_dependency:
                result = CaseResult(case.case_id, "SKIPPED_DEPENDENCY",
                                    error="solver has a failed or unavailable oracle case")
            else:
                result = run_case_subprocess(case, run_dir, case.slot_seconds, command_factory,
                    heartbeat_callback=lambda elapsed, case_id=case.case_id: heartbeat(state, case_id, elapsed),
                    heartbeat_seconds=heartbeat_seconds)
            measured = time.monotonic() - launch_started
            result.elapsed_seconds = max(result.elapsed_seconds, measured)
            if result.elapsed_seconds >= case.slot_seconds and result.status == "DONE":
                result.status = "DEADLINE_EXCEEDED"
                result.payload["quality_pass"] = False
            record_terminal_case(state, result, started_monotonic=launch_started)
            # Terminal persistence is now in the case ledger; only its final
            # accounting-write tail remains supervisor overhead.
            marked = launch_started + result.elapsed_seconds
            count += 1
            if result.status in {"INPUT_MISMATCH", "VALIDATION_ERROR", "ORACLE_MISMATCH"}:
                state.status = "PAUSED_INTEGRITY"
                break
            if result.status == "MEMORY_LIMIT_EXCEEDED":
                state.status = "PAUSED_RESOURCE"
                break
        state.active_seconds += time.monotonic() - marked
        if not state.status.startswith("PAUSED_"):
            state.status = "COMPLETE" if all(case.case_id in state.cases for case in schedule) else "PARTIAL"
        save_checkpoint(state)
        append_event(state, "STOPPED", status=state.status, active_seconds=state.active_seconds)
        return state


def build_manifest(schedule) -> dict:
    import importlib.metadata
    import platform
    from experiments.phase1.solvers import available_solvers
    root = Path(__file__).resolve().parents[2]
    sources = [*sorted((root / "core").rglob("*.py")),
               *sorted((root / "experiments/phase1").glob("*.py")),
               root / "experiments/bench/phase0_model.py",
               root / "experiments/phase0/oracle.py", root / "experiments/bench/datasets.py"]
    source_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
        capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    dependencies = {}
    for name in ("numpy", "scipy", "pulp", "pydantic", "highspy", "pyscipopt", "tiktoken"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    return {"schema_version": 1, "schedule": [asdict(case) for case in schedule],
            "source_commit": source_commit, "dependency_versions": dependencies,
            "dependency_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
            "solvers": {name: asdict(record) for name, record in available_solvers().items()},
            "options": {"threads": 1, "relative_gap": 0.0, "solver_guard_seconds": 10},
            "thread_environment": THREAD_ENV, "python": platform.python_version(),
            "platform": platform.platform(), "machine": platform.machine(),
            "source_sha256": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sources}, "business_validity": "NOT_CALIBRATED"}


def main() -> None:
    startup_started = time.monotonic()
    from experiments.phase1.schedule import build_schedule
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-active-seconds", type=float, required=True)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.max_active_seconds) or not 0 < args.max_active_seconds <= 86400:
        parser.error("--max-active-seconds must be in (0, 86400]")
    if args.max_cases is not None and args.max_cases < 0:
        parser.error("--max-cases must be nonnegative")
    run_dir = Path("experiments/results/phase1") / safe_component(args.run_id)
    if (run_dir / "checkpoint.json").exists() and not args.resume:
        parser.error("run already exists; pass --resume or choose a new run ID")
    schedule = build_schedule()
    manifest = build_manifest(schedule)
    prepared_seconds = 0.0
    if (run_dir / "manifest.json").exists():
        existing = json.loads((run_dir / "manifest.json").read_text())
        manifest.update({name: existing[name] for name in ("inputs", "preparation_active_seconds")})
    else:
        manifest = prepare_new_run(schedule, run_dir, manifest, args.max_active_seconds)
        prepared_seconds = manifest["preparation_active_seconds"]
    state = run_schedule(schedule, run_dir, manifest, args.max_active_seconds,
                         max_cases=args.max_cases,
                         startup_active_seconds=max(0.0, time.monotonic() - startup_started - prepared_seconds))
    print(json.dumps({"run_id": args.run_id, "status": state.status,
                      "active_seconds": state.active_seconds, "cases": len(state.cases)}))


if __name__ == "__main__":
    main()
