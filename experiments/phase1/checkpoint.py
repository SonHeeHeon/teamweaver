"""Durable single-supervisor ledger. Unclosed reservations are never refunded."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any


class ManifestMismatch(RuntimeError):
    pass


class CheckpointCorrupt(RuntimeError):
    pass


def _restore_required_pause(state) -> None:
    statuses = {row["status"] for row in state.cases.values()}
    if statuses & {"INPUT_MISMATCH", "VALIDATION_ERROR", "ORACLE_MISMATCH"}:
        state.status = "PAUSED_INTEGRITY"
    elif "MEMORY_LIMIT_EXCEEDED" in statuses:
        state.status = "PAUSED_RESOURCE"
    elif "ORPHANED" in statuses:
        state.status = "PAUSED_ORPHANED"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def safe_component(value: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value or "\\" in value:
        raise ValueError("identifier must be one path component")
    return value


@dataclass
class CaseResult:
    case_id: str
    status: str
    attempt: int = 1
    elapsed_seconds: float = 0.0
    deadline_seconds: float = 0.0
    cleanup_seconds: float = 0.0
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    pid: int | None = None
    pgid: int | None = None
    signals_sent: list[int] = field(default_factory=list)
    peak_rss_bytes: int | None = None
    rss_sample_status: str = "NOT_SAMPLED"
    rss_sample_count: int = 0
    rss_sample_errors: list[str] = field(default_factory=list)
    rss_limit_bytes: int = 12 * 1024**3
    rss_sample_interval_seconds: float = 0.5
    rss_caveat: str = "Best-effort process-group RSS sampling is not a hard OS memory cap."
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    schema_version: int = 1


@dataclass
class RunState:
    run_dir: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    active_seconds: float = 0.0
    cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    status: str = "PARTIAL"
    recovered_orphans: list[str] = field(default_factory=list)
    supervisor_active: bool = False
    last_reserved_case: str | None = None


def save_checkpoint(state: RunState) -> None:
    state.updated_at = utc_now()
    payload = {"schema_version": 1, "manifest_sha256": state.manifest_sha256,
               "active_seconds": state.active_seconds, "cases": state.cases,
               "created_at": state.created_at, "updated_at": state.updated_at,
               "status": state.status, "supervisor_active": state.supervisor_active,
               "last_reserved_case": state.last_reserved_case}
    payload["checksum_sha256"] = fingerprint(payload)
    atomic_write_json(state.run_dir / "checkpoint.json", payload)


def append_event(state: RunState, event: str, **fields: Any) -> None:
    row = {"event": event, "at": utc_now(), **fields}
    with (state.run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _repair_event_tail(run_dir: Path) -> None:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        boundary = data.rfind(b"\n") + 1
        with (run_dir / "events.partial").open("ab") as stream:
            stream.write(data[boundary:])
            stream.flush()
            os.fsync(stream.fileno())
        with path.open("r+b") as stream:
            stream.truncate(boundary)
            stream.flush()
            os.fsync(stream.fileno())
    for line in path.read_text().splitlines():
        try:
            json.loads(line)
        except ValueError as exc:
            raise CheckpointCorrupt("event log contains an invalid complete line") from exc


def read_bound_checkpoint(run_dir: Path, manifest: dict[str, Any]) -> dict:
    """Read-only identity check usable by a worker without recovering reservations."""
    try:
        data = json.loads((Path(run_dir) / "checkpoint.json").read_text())
        checksum = data.pop("checksum_sha256")
        if data["schema_version"] != 1 or checksum != fingerprint(data):
            raise CheckpointCorrupt("checkpoint checksum or schema mismatch")
        if data["manifest_sha256"] != fingerprint(manifest):
            raise ManifestMismatch("checkpoint manifest fingerprint mismatch")
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CheckpointCorrupt("checkpoint is missing or malformed") from exc


def create_or_load_run(run_dir: Path, manifest: dict[str, Any]) -> RunState:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    digest = fingerprint(manifest)
    manifest_path = run_dir / "manifest.json"
    checkpoint_path = run_dir / "checkpoint.json"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text())
        except ValueError as exc:
            raise ManifestMismatch("stored manifest is invalid JSON") from exc
        if fingerprint(existing) != digest:
            raise ManifestMismatch("run manifest fingerprint changed; use a new run ID")
    elif checkpoint_path.exists():
        raise CheckpointCorrupt("checkpoint has no manifest")
    else:
        atomic_write_json(manifest_path, manifest)
    if not checkpoint_path.exists():
        preparation_path = run_dir / "preparation.json"
        if preparation_path.exists():
            try:
                preparation = json.loads(preparation_path.read_text())
                if preparation["status"] != "DONE":
                    raise CheckpointCorrupt("input preparation has an unclosed full-budget reservation")
                if preparation["active_seconds"] != manifest["preparation_active_seconds"]:
                    raise CheckpointCorrupt("input preparation ledger differs from manifest")
            except (ValueError, KeyError) as exc:
                raise CheckpointCorrupt("input preparation ledger is malformed") from exc
        if (run_dir / "cases").exists():
            raise CheckpointCorrupt("case artifacts exist without a checkpoint")
        state = RunState(run_dir, manifest, digest,
                         active_seconds=float(manifest.get("preparation_active_seconds", 0.0)))
        save_checkpoint(state)
        append_event(state, "CREATED", manifest_sha256=digest)
        return state
    try:
        data = read_bound_checkpoint(run_dir, manifest)
        active = data["active_seconds"]
        if not math.isfinite(active) or active < 0:
            raise CheckpointCorrupt("invalid active-time ledger")
        state = RunState(run_dir, manifest, digest, active, data["cases"],
                         data["created_at"], data["updated_at"], data["status"])
        state.supervisor_active = data.get("supervisor_active", False)
        state.last_reserved_case = data.get("last_reserved_case")
        if type(state.supervisor_active) is not bool or (
                state.last_reserved_case is not None and (
                    not isinstance(state.last_reserved_case, str)
                    or state.last_reserved_case not in state.cases)):
            raise CheckpointCorrupt("invalid supervisor transaction marker")
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckpointCorrupt("checkpoint is malformed") from exc
    _repair_event_tail(run_dir)
    if state.supervisor_active and state.last_reserved_case is not None:
        case_id = state.last_reserved_case
        row = state.cases[case_id]
        if row["status"] == "DONE":
            # This invocation never durably acknowledged its final success with
            # a subsequent reservation or clean stop. Its persistence tail may
            # have crossed the deadline. Restore the full charge before replacing
            # the artifact; a crash during recovery then leaves a RUNNING row,
            # which does not require a terminal hash and is safe to recover again.
            # Do this before terminal validation: an accounting result replace
            # may have committed while its matching checkpoint write did not.
            state.active_seconds += max(row["reserved_seconds"], row["elapsed_seconds"]) - row["elapsed_seconds"]
            state.cases[case_id] = {**row, "status": "RUNNING"}
            save_checkpoint(state)
    for case_id, row in state.cases.items():
        if row["status"] != "RUNNING":
            try:
                terminal = json.loads(attempt_directory(run_dir, case_id, row["attempt"]).joinpath("result.json").read_text())
                if fingerprint(terminal) != row["result_sha256"]:
                    raise ValueError("terminal result checksum mismatch")
            except (OSError, ValueError, KeyError) as exc:
                raise CheckpointCorrupt(f"terminal result missing or changed: {case_id}") from exc
    for case_id, row in list(state.cases.items()):
        if row["status"] == "RUNNING":
            # No stored PID is ever signaled. The full slot was charged before spawn.
            result = CaseResult(case_id, "ORPHANED", attempt=row["attempt"],
                                elapsed_seconds=row["reserved_seconds"],
                                deadline_seconds=row["reserved_seconds"],
                                started_at=row["started_at"], finished_at=utc_now(),
                                payload={"quality_pass": False},
                                error="unclosed or unacknowledged reservation; full slot retained")
            record_terminal_case(state, result)
            state.recovered_orphans.append(case_id)
    _restore_required_pause(state)
    return state


def attempt_directory(run_dir: Path, case_id: str, attempt: int) -> Path:
    return Path(run_dir) / "cases" / safe_component(case_id) / f"attempt-{attempt:03d}"


def reserve_case(state: RunState, case, *, slot_seconds: float | None = None) -> int:
    if case.case_id in state.cases:
        raise ValueError("case already has a recorded attempt")
    slot = float(case.slot_seconds if slot_seconds is None else slot_seconds)
    if not math.isfinite(slot) or slot <= 0:
        raise ValueError("slot_seconds must be finite and positive")
    safe_component(case.case_id)
    state.active_seconds += slot
    state.cases[case.case_id] = {"status": "RUNNING", "attempt": 1,
        "reserved_seconds": slot, "started_at": utc_now(), "elapsed_seconds": 0.0,
        "case": asdict(case)}
    # The pointer and reservation share a checkpoint: earlier successes become
    # acknowledged only once the next reservation is durable.
    state.last_reserved_case = case.case_id
    save_checkpoint(state)
    append_event(state, "RESERVED", case_id=case.case_id, slot_seconds=slot, attempt=1)
    return 1


def heartbeat(state: RunState, case_id: str, elapsed_seconds: float) -> None:
    state.cases[case_id]["elapsed_seconds"] = elapsed_seconds
    save_checkpoint(state)
    append_event(state, "HEARTBEAT", case_id=case_id, elapsed_seconds=elapsed_seconds)


def record_terminal_case(state: RunState, result: CaseResult, *, started_monotonic=None) -> RunState:
    if result.status in {"PENDING", "RUNNING"}:
        raise ValueError("a terminal result is required")
    if not math.isfinite(result.elapsed_seconds) or result.elapsed_seconds < 0:
        raise ValueError("invalid elapsed_seconds")
    row = state.cases[result.case_id]
    if row["status"] != "RUNNING" or row["attempt"] != result.attempt:
        raise ValueError("result must close its matching running reservation exactly once")
    path = attempt_directory(state.run_dir, result.case_id, result.attempt) / "result.json"
    accounted = row["reserved_seconds"]

    def persist(event):
        nonlocal accounted
        payload = asdict(result)
        atomic_write_json(path, payload)
        state.active_seconds += result.elapsed_seconds - accounted
        accounted = result.elapsed_seconds
        state.cases[result.case_id] = {**row, "status": result.status,
            "elapsed_seconds": result.elapsed_seconds, "result_path": str(path.relative_to(state.run_dir)),
            "result_sha256": fingerprint(payload)}
        _restore_required_pause(state)
        save_checkpoint(state)
        append_event(state, event, case_id=result.case_id, status=result.status,
                     attempt=result.attempt, elapsed_seconds=result.elapsed_seconds)

    def measure():
        result.elapsed_seconds = max(result.elapsed_seconds, time.monotonic() - started_monotonic)
        if result.elapsed_seconds >= row["reserved_seconds"] and result.status == "DONE":
            result.status = "DEADLINE_EXCEEDED"
        if result.status != "DONE":
            result.payload["quality_pass"] = False
        result.finished_at = utc_now()

    persist("TERMINAL")
    if started_monotonic is not None:
        measure()
        persist("TERMINAL_ACCOUNTED")
        # Accounting persistence can itself consume the remaining slot. A final
        # correction is always non-success, so it cannot reopen the deadline.
        # Its own I/O tail is charged by the scheduler, not recursively rewritten.
        if time.monotonic() - started_monotonic >= row["reserved_seconds"]:
            measure()
            persist("TERMINAL_ACCOUNTED")
    return state
