"""One cold-process benchmark attempt; the parent owns the hard deadline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import time

from experiments.phase1.checkpoint import (
    CheckpointCorrupt, ManifestMismatch, atomic_write_json, read_bound_checkpoint, safe_component,
    fingerprint,
)


def _verify_runtime(manifest):
    from core.optimize.numerics import NumericalPolicy
    policy = manifest.get("numerical_policy")
    if (not isinstance(policy,dict) or set(policy) != set(asdict(NumericalPolicy()))
            or fingerprint(policy) != manifest.get("numerical_policy_sha256")):
        raise ManifestMismatch("numerical policy missing or changed")
    try:
        NumericalPolicy(**policy)
    except (ValueError,TypeError) as exc:
        raise ManifestMismatch("unsupported numerical policy") from exc
    import importlib.metadata
    root = Path(__file__).resolve().parents[2]
    for name, expected in manifest.get("source_sha256", {}).items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ManifestMismatch(f"runtime source changed: {name}")
    if "dependency_lock_sha256" in manifest:
        if hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest() != manifest["dependency_lock_sha256"]:
            raise ManifestMismatch("runtime dependency lock changed")
    for name, expected in manifest.get("dependency_versions", {}).items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            raise ManifestMismatch(f"runtime dependency version changed: {name}")


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


def _safe_numeric_json(value):
    if isinstance(value,float) and not math.isfinite(value):
        return {"invalid_numeric":str(value)}
    if isinstance(value,dict): return {str(k):_safe_numeric_json(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)): return [_safe_numeric_json(v) for v in value]
    return value


def _assessment_fields(assessment):
    return {"native_validation":asdict(assessment.native_validation) if assessment.native_validation else None,
            "initial_validation":asdict(assessment.initial_validation),
            "final_validation":asdict(assessment.final_validation) if assessment.final_validation else None,
            "validation":asdict(assessment.final_validation if assessment.accepted else assessment.initial_validation),
            "refinement":asdict(assessment.refinement),"pipeline_pass":assessment.accepted is not None}


def persist_candidate_evidence(attempt_dir,assessment,*,native_fragment=None):
    artifacts = {}
    def write(name,data):
        data = _safe_numeric_json(data)
        atomic_write_json(Path(attempt_dir)/name,data)
        artifacts[name] = {"path":name,"sha256":fingerprint(data)}
    if assessment is not None:
        if assessment.native_capture is not None:
            write("native-candidate.json",_json_solution(assessment.native_capture))
        write("validation-candidate.json",_json_solution(assessment.validation_candidate))
        write("candidate-assessment.json",_assessment_fields(assessment))
        if assessment.accepted is not None:
            write("raw-solution.json",_json_solution(assessment.accepted))
    elif native_fragment is not None:
        write("native-candidate.json",{**{name:[{"key":list(key),"value":value} for key,value in native_fragment.get(name,{}).items()]
                for name in ("z","a","y","slack")},"objective":native_fragment.get("objective"),"extractable":False})
    return artifacts


def quality_metrics(raw,validation,*,refined=False):
    upper,lower = raw.evidence.best_bound,raw.objective
    gap,status = None,"BOUND_UNKNOWN"
    tolerance = 1e-6+1e-8*max(1,abs(lower))
    if upper is not None:
        if not math.isfinite(upper) or upper < lower-tolerance:
            status = "BOUND_INVALID"
        else:
            gap = max(0.,upper-lower)/max(1.,abs(upper),abs(lower))
            status = "QUALITY_PASS" if validation.valid and gap <= .05 else "QUALITY_FAIL"
    return {"quality_status":status,"normalized_gap":gap,
            "quality_pass":status == "QUALITY_PASS",
            "proven_optimal":bool(not refined and validation.valid and upper is not None
                and status != "BOUND_INVALID" and abs(upper-lower) <= tolerance
                and raw.evidence.native_status.lower() == "optimal")}


def execute_case(case, attempt_dir: Path, deadline: float) -> dict:
    # Heavy imports, reading, graph/scoring reconstruction, validation and persistence
    # all happen under the absolute parent monotonic deadline.
    run_dir = attempt_dir.parents[2]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    read_bound_checkpoint(run_dir, manifest)
    _verify_runtime(manifest)
    from core.optimize.validation import validate_raw_solution
    from experiments.phase1.scenarios import FrozenBenchmarkInput
    from experiments.phase1.solvers import solve_case_diagnostic
    from core.optimize.numerics import NumericalPolicy
    from experiments.phase1.types import BenchmarkProblem, SolverOptions
    start = time.monotonic()
    hashes = None
    if case.stage == "oracle":
        from experiments.bench.phase0_model import default_oracle_cases
        fixture = next(item for item in default_oracle_cases() if item.name == case.oracle_name)
        graph, skill, synergy, params = fixture.build()
        problem = BenchmarkProblem(graph, skill, synergy, params)
    else:
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
    assessment = solve_case_diagnostic(problem, case.solver_name, SolverOptions(time_limit_seconds=remaining),
        numerical_policy=NumericalPolicy(**manifest["numerical_policy"]),deadline=deadline-8.)
    solve_seconds = time.monotonic() - solve_started
    artifacts = persist_candidate_evidence(attempt_dir,assessment)
    fields = _safe_numeric_json(_assessment_fields(assessment))
    timings = {"input":input_seconds,"solve_and_extract":solve_seconds,
               "refinement":assessment.refinement.elapsed_seconds if assessment.refinement.attempted else 0.,
               "native_solve_extract_validate":max(0.,solve_seconds-assessment.refinement.elapsed_seconds)}
    if assessment.accepted is None:
        return {"schema_version":2,"status":"NO_VALID_INCUMBENT","quality_pass":False,
                "evidence":asdict(assessment.validation_candidate.evidence),"artifacts":artifacts,
                "error":assessment.refinement.reason,"business_validity":"NOT_CALIBRATED",
                "timings_seconds":{**timings,"validate_and_oracle":0.},**fields}
    raw = assessment.accepted
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
    quality = quality_metrics(raw,validation,refined=assessment.refinement.attempted)
    status = "DONE"
    if not validation.valid:
        status = "VALIDATION_ERROR"
    elif parity is False:
        status = "ORACLE_MISMATCH"
    fields.update(validation=asdict(validation), final_validation=asdict(validation),pipeline_pass=status == "DONE")
    payload = {"schema_version": 2, "case_id": case.case_id, "status": status,
               "solver_name": case.solver_name, "input_id": case.input_id,
               "objective": lower, "best_bound": upper, **quality,
               "quality_pass": quality["quality_pass"] and status == "DONE",
               "oracle_objective": oracle_objective, "oracle_parity": parity,
               "validation": asdict(validation), "evidence": asdict(raw.evidence),
               "hashes": hashes, "variable_count": raw.variable_count,
               "constraint_count": raw.constraint_count,
               "reward_pair_count": len(raw.reward_pairs), "penalty_pair_count": len(raw.penalty_pairs),
               "timings_seconds": {**timings,
                                    "validate_and_oracle": time.monotonic() - validation_started},
               "business_validity": "NOT_CALIBRATED","artifacts":artifacts,**fields}
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
        try:
            artifacts = persist_candidate_evidence(attempt_dir,exc.assessment,native_fragment=exc.native_fragment)
            payload = {"status":"NO_VALID_INCUMBENT","evidence":asdict(exc.evidence),"error":str(exc),"artifacts":artifacts}
            if exc.assessment is not None: payload.update(_safe_numeric_json(_assessment_fields(exc.assessment)))
        except OSError as write_error:
            payload = {"status":"EVIDENCE_WRITE_ERROR","error":str(write_error),"quality_pass":False}
    except (CheckpointCorrupt, ManifestMismatch, SnapshotIntegrityError, KeyError, FileNotFoundError) as exc:
        payload = {"status": "INPUT_MISMATCH", "error": f"{type(exc).__name__}: {exc}"}
    except TimeoutError as exc:
        payload = {"status": "DEADLINE_EXCEEDED", "error": str(exc)}
    except OSError as exc:
        payload = {"status":"EVIDENCE_WRITE_ERROR","error":str(exc),"quality_pass":False}
    except Exception as exc:
        payload = {"status": "VALIDATION_ERROR", "error": f"{type(exc).__name__}: {exc}"}
    payload.update({"case_id": case.case_id, "schema_version": 2,
                    "solver_name": case.solver_name, "input_id": case.input_id,
                    "stage": case.stage, "repeat": case.repeat})
    atomic_write_json(attempt_dir / "worker-result.json", payload)


if __name__ == "__main__":
    main()
