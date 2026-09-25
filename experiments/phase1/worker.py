"""One cold-process benchmark attempt; the parent owns the hard deadline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import time

from experiments.phase1.checkpoint import ManifestMismatch, atomic_write_json, safe_component


def problem_from_materialized(materialized):
    from experiments.phase1.types import BenchmarkProblem
    materialized.verify_integrity()
    return BenchmarkProblem(graph=materialized.graph, S=materialized.S,
                            C=materialized.C, params=materialized.params)


def _json_solution(raw) -> dict:
    return {"status": raw.status, "objective": raw.objective,
            "plan": raw.plan.model_dump(mode="json"),
            "evidence": asdict(raw.evidence),
            "reward_pairs": raw.reward_pairs, "penalty_pairs": raw.penalty_pairs,
            "variable_count": raw.variable_count, "constraint_count": raw.constraint_count,
            **{name: [{"key": list(key), "value": value} for key, value in getattr(raw, name).items()]
               for name in ("z", "a", "y", "slack")}}


def execute_case(case, attempt_dir: Path, deadline: float) -> dict:
    # Heavy imports, reading, graph/scoring reconstruction, validation and persistence
    # all happen under the absolute parent monotonic deadline.
    from core.optimize.validation import validate_raw_solution
    from experiments.phase1.scenarios import FrozenBenchmarkInput
    from experiments.phase1.solvers import solve_case
    from experiments.phase1.types import BenchmarkProblem, SolverOptions
    start = time.monotonic()
    hashes = None
    if case.stage == "oracle":
        from experiments.bench.phase0_model import default_oracle_cases
        fixture = next(item for item in default_oracle_cases() if item.name == case.oracle_name)
        graph, skill, synergy, params = fixture.build()
        problem = BenchmarkProblem(graph, skill, synergy, params)
    else:
        run_dir = attempt_dir.parents[2]
        manifest = json.loads((run_dir / "manifest.json").read_text())
        expected = manifest["inputs"][case.input_id]
        serialized = (run_dir / "inputs" / f"{safe_component(case.input_id)}.json").read_bytes()
        if hashlib.sha256(serialized).hexdigest() != expected["file_sha256"]:
            raise ManifestMismatch("frozen input file checksum changed before solve")
        frozen = FrozenBenchmarkInput.from_json(serialized, expected["snapshot_sha256"])
        if (frozen.n_people, frozen.n_projects, frozen.seed, frozen.scenario) != (
                case.n_people, case.n_projects, case.seed, case.scenario):
            raise ManifestMismatch("frozen input identity differs from scheduled case")
        materialized = frozen.materialize()
        hashes = materialized.hashes.as_dict()
        problem = problem_from_materialized(materialized)
    input_seconds = time.monotonic() - start
    remaining = deadline - time.monotonic() - 10.0
    if remaining <= 0:
        raise TimeoutError("no solver time remains after validation/persistence guard")
    solve_started = time.monotonic()
    raw = solve_case(problem, case.solver_name, SolverOptions(time_limit_seconds=remaining))
    solve_seconds = time.monotonic() - solve_started
    validation_started = time.monotonic()
    validation = validate_raw_solution(problem.graph, problem.S, problem.C, problem.params, raw)
    oracle_objective = None
    parity = None
    if case.stage == "oracle":
        from experiments.phase0.oracle import solve_tiny_oracle
        oracle = solve_tiny_oracle(problem.graph, problem.S, problem.C, problem.params,
                                   deadline=deadline - 1.0)
        oracle_objective = oracle.objective
        parity = abs(raw.objective - oracle.objective) <= 1e-6
    upper = raw.evidence.best_bound
    lower = raw.objective
    gap = None
    quality_status = "BOUND_UNKNOWN"
    tolerance = 1e-6 + 1e-8 * max(1, abs(lower))
    if upper is not None:
        if not math.isfinite(upper) or upper < lower - tolerance:
            quality_status = "BOUND_INVALID"
        else:
            gap = max(0.0, upper - lower) / max(1.0, abs(upper), abs(lower))
            quality_status = "QUALITY_PASS" if validation.valid and gap <= 0.05 else "QUALITY_FAIL"
    status = "DONE"
    if not validation.valid:
        status = "VALIDATION_ERROR"
    elif parity is False:
        status = "ORACLE_MISMATCH"
    payload = {"schema_version": 1, "case_id": case.case_id, "status": status,
               "solver_name": case.solver_name, "input_id": case.input_id,
               "objective": lower, "best_bound": upper, "normalized_gap": gap,
               "quality_status": quality_status,
               "quality_pass": quality_status == "QUALITY_PASS" and status == "DONE",
               "proven_optimal": bool(validation.valid and upper is not None
                    and quality_status != "BOUND_INVALID" and abs(upper - lower) <= tolerance
                    and raw.evidence.native_status.lower() == "optimal"),
               "oracle_objective": oracle_objective, "oracle_parity": parity,
               "validation": asdict(validation), "evidence": asdict(raw.evidence),
               "hashes": hashes, "variable_count": raw.variable_count,
               "constraint_count": raw.constraint_count,
               "reward_pair_count": len(raw.reward_pairs), "penalty_pair_count": len(raw.penalty_pairs),
               "timings_seconds": {"input": input_seconds, "solve_and_extract": solve_seconds,
                                    "validate_and_oracle": time.monotonic() - validation_started},
               "business_validity": "NOT_CALIBRATED"}
    atomic_write_json(attempt_dir / "raw-solution.json", _json_solution(raw))
    if time.monotonic() >= deadline:
        raise TimeoutError("deadline exceeded during validation or persistence")
    return payload


def main() -> None:
    from experiments.phase1.scenarios import SnapshotIntegrityError
    from experiments.phase1.schedule import SweepCase
    from experiments.phase1.solvers import SolverSolveError, SolverUnavailableError
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--case-file", type=Path)
    mode.add_argument("--freeze-request", type=Path)
    parser.add_argument("--deadline", type=float, required=True)
    args = parser.parse_args()
    if args.freeze_request is not None:
        from experiments.phase1.runner import freeze_scheduled_inputs
        request = json.loads(args.freeze_request.read_text())
        manifest = freeze_scheduled_inputs(
            tuple(SweepCase(**row) for row in request["schedule"]),
            Path(request["run_dir"]), request["manifest"], args.deadline)
        atomic_write_json(args.freeze_request.parent / "worker-result.json",
                          {"status": "DONE", "manifest": manifest})
        return
    case = SweepCase(**json.loads(args.case_file.read_text()))
    attempt_dir = args.case_file.parent
    try:
        payload = execute_case(case, attempt_dir, args.deadline)
    except SolverUnavailableError as exc:
        payload = {"status": "UNAVAILABLE", "availability": asdict(exc.availability), "error": str(exc)}
    except SolverSolveError as exc:
        payload = {"status": "NO_VALID_INCUMBENT", "evidence": asdict(exc.evidence), "error": str(exc)}
    except (ManifestMismatch, SnapshotIntegrityError, KeyError, FileNotFoundError) as exc:
        payload = {"status": "INPUT_MISMATCH", "error": f"{type(exc).__name__}: {exc}"}
    except TimeoutError as exc:
        payload = {"status": "DEADLINE_EXCEEDED", "error": str(exc)}
    except Exception as exc:
        payload = {"status": "VALIDATION_ERROR", "error": f"{type(exc).__name__}: {exc}"}
    payload.update({"case_id": case.case_id, "schema_version": 1,
                    "solver_name": case.solver_name, "input_id": case.input_id,
                    "stage": case.stage, "repeat": case.repeat})
    atomic_write_json(attempt_dir / "worker-result.json", payload)


if __name__ == "__main__":
    main()
