from dataclasses import asdict, replace
import json
import time
from types import SimpleNamespace
import pytest
import pulp
from core.optimize.numerics import NumericalPolicy, assess_candidate
from experiments.phase1 import solvers, worker, report, runner
from experiments.phase1.checkpoint import fingerprint, ManifestMismatch
from experiments.phase1.types import BenchmarkProblem, SolverOptions
from tests.test_numerical_refinement import _fixture
from tests.phase1.test_solvers import _FiniteCandidateSolver, _all_terms_problem, _valid_all_terms_values


def _assessment(valid=False):
    g,S,C,params,raw = _fixture(.35 if valid else .3500000025)
    return assess_candidate(g,S,C,params,raw,native_capture=raw,policy=NumericalPolicy())


def test_solve_error_keeps_optional_assessment():
    assessment = _assessment()
    error = solvers.SolverSolveError("rejected",assessment.validation_candidate.evidence,assessment=assessment)
    assert error.assessment is assessment


def test_diagnostic_keeps_native_and_validation_candidates_distinct(monkeypatch):
    values = _valid_all_terms_values()
    values["a_0_0"] = 1.0000005
    fake = _FiniteCandidateSolver(status=pulp.LpStatusOptimal,values=values)
    monkeypatch.setattr(solvers,"_solver_for",lambda *_:fake)
    assert hasattr(solvers,"solve_case_diagnostic"), "native diagnostic API missing"
    result = solvers.solve_case_diagnostic(_all_terms_problem(),"cbc",SolverOptions(),
        numerical_policy=NumericalPolicy(),deadline=None)
    assert result.native_capture.a[(0,0)] == 1.0000005
    assert result.validation_candidate.a[(0,0)] == 1.
    assert result.native_validation.valid is False
    assert result.initial_validation.valid is True


def test_rejected_candidate_is_persisted_and_hash_bound(tmp_path):
    assert hasattr(worker,"persist_candidate_evidence"), "failure persistence missing"
    records = worker.persist_candidate_evidence(tmp_path,_assessment())
    assert "native-candidate.json" in records
    assert "raw-solution.json" not in records
    for name,record in records.items():
        assert record["sha256"] == fingerprint(json.loads((tmp_path/name).read_text()))


def test_only_accepted_solution_gets_raw_solution_file(tmp_path):
    assert hasattr(worker,"persist_candidate_evidence"), "accepted evidence API missing"
    records = worker.persist_candidate_evidence(tmp_path,_assessment(True))
    assert "raw-solution.json" in records


def test_nonfinite_candidate_has_valid_tagged_json(tmp_path):
    assessment = _assessment()
    raw = replace(assessment.validation_candidate,a={(0,0):float("nan"),(1,0):0.})
    assessment = replace(assessment,native_capture=raw,validation_candidate=raw)
    assert hasattr(worker,"persist_candidate_evidence")
    worker.persist_candidate_evidence(tmp_path,assessment)
    content = json.loads((tmp_path/"native-candidate.json").read_text())
    assert content["a"][0]["value"] == {"invalid_numeric":"nan"}


def test_sidecar_tamper_is_rejected(tmp_path):
    from tests.phase1.test_phase1_report import _write_run
    _write_run(tmp_path)
    path = tmp_path/"cases/bound-unknown/attempt-001/result.json"
    sidecar = path.with_name("native-candidate.json")
    sidecar.write_text("{}")
    result = json.loads(path.read_text())
    result["payload"]["artifacts"] = {"native-candidate.json":{"path":"native-candidate.json","sha256":fingerprint({})}}
    result["payload"]["schema_version"] = 2
    path.write_text(json.dumps(result))
    checkpoint = json.loads((tmp_path/"checkpoint.json").read_text())
    checkpoint["cases"]["bound-unknown"]["result_sha256"] = fingerprint(result)
    checkpoint.pop("checksum_sha256")
    checkpoint["checksum_sha256"] = fingerprint(checkpoint)
    (tmp_path/"checkpoint.json").write_text(json.dumps(checkpoint))
    sidecar.write_text('{"changed":true}')
    with pytest.raises(Exception,match="sidecar"):
        report._load_run(tmp_path)


def test_evidence_write_error_never_becomes_done(tmp_path,monkeypatch):
    assert hasattr(worker,"persist_candidate_evidence")
    monkeypatch.setattr(worker,"atomic_write_json",lambda *a,**kw: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError,match="disk full"):
        worker.persist_candidate_evidence(tmp_path,_assessment(True))


def test_manifest_binds_all_policy_fields():
    manifest = runner.build_manifest(())
    assert manifest["numerical_policy"] == asdict(NumericalPolicy(enabled=True))
    assert manifest["numerical_policy_sha256"] == fingerprint(manifest["numerical_policy"])


def test_policy_change_refuses_runtime():
    manifest = runner.build_manifest(())
    assert "numerical_policy" in manifest
    manifest["numerical_policy"]["enabled"] = False
    with pytest.raises(ManifestMismatch,match="policy"):
        worker._verify_runtime(manifest)


def test_legacy_missing_policy_is_not_new_default():
    with pytest.raises(ManifestMismatch,match="policy"):
        worker._verify_runtime({})


def test_policy_change_refuses_resume(tmp_path):
    from experiments.phase1.checkpoint import create_or_load_run
    manifest = runner.numerical_policy_metadata()
    create_or_load_run(tmp_path,manifest)
    changed = runner.numerical_policy_metadata()
    changed["numerical_policy"]["enabled"] = False
    changed["numerical_policy_sha256"] = fingerprint(changed["numerical_policy"])
    with pytest.raises(ManifestMismatch):
        create_or_load_run(tmp_path,changed)


def test_worker_main_marks_evidence_write_failure_not_done(tmp_path,monkeypatch):
    import sys
    from experiments.phase1.schedule import SweepCase
    case = SweepCase(1,"tiny","oracle","one","cbc",1,30,oracle_name="one_slot")
    path = tmp_path/"case.json"
    path.write_text(json.dumps(asdict(case)))
    monkeypatch.setattr(sys,"argv",["worker","--case-file",str(path),"--deadline",str(time.monotonic()+30)])
    monkeypatch.setattr(worker,"execute_case",lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    worker.main()
    payload = json.loads((tmp_path/"worker-result.json").read_text())
    assert payload["status"] == "EVIDENCE_WRITE_ERROR"
    assert payload["quality_pass"] is False


def test_oracle_payload_v2_and_refinement_inside_existing_guard(tmp_path,monkeypatch):
    from experiments.phase1.checkpoint import create_or_load_run
    from experiments.phase1.schedule import SweepCase
    attempt = tmp_path/"cases/tiny/attempt-001"
    create_or_load_run(tmp_path,runner.numerical_policy_metadata())
    attempt.mkdir(parents=True)
    original = solvers.solve_case_diagnostic
    captured = {}
    def diagnostic(problem,name,options,**kwargs):
        captured.update(options=options,**kwargs)
        return original(problem,name,options,**kwargs)
    monkeypatch.setattr(solvers,"solve_case_diagnostic",diagnostic)
    deadline = time.monotonic()+30
    case = SweepCase(1,"tiny","oracle","one","cbc",1,30,oracle_name="one_slot")
    payload = worker.execute_case(case,attempt,deadline)
    assert captured["deadline"] == deadline-8
    assert captured["options"].time_limit_seconds <= 20
    assert payload["schema_version"] == 2


def test_unrecorded_bucket_has_all_pipeline_denominators():
    values = report.summarize_stages({"schedule":[{"stage":"primary","solver_name":"cbc"}]},[])["cbc"]["core"]
    assert values["native_strict_pass"] == values["pipeline_pass"] == 0


@pytest.mark.parametrize("policy",[{}, {"enabled":True}])
def test_partial_policy_is_not_bound_to_implicit_defaults(policy):
    with pytest.raises(ManifestMismatch,match="policy"):
        worker._verify_runtime({"numerical_policy":policy,"numerical_policy_sha256":fingerprint(policy)})


@pytest.mark.parametrize("rejected",[False,True])
def test_worker_records_final_failure_and_rejected_timings(tmp_path,monkeypatch,rejected):
    from experiments.phase1.checkpoint import create_or_load_run
    from experiments.phase1.schedule import SweepCase
    from core.optimize import validation
    create_or_load_run(tmp_path,runner.numerical_policy_metadata())
    attempt = tmp_path/"cases/tiny/attempt-001"
    attempt.mkdir(parents=True)
    assessment = _assessment(not rejected)
    monkeypatch.setattr(solvers,"solve_case_diagnostic",lambda *a,**kw:assessment)
    if not rejected:
        monkeypatch.setattr(validation,"validate_raw_solution",lambda *a: _assessment(False).initial_validation)
    payload = worker.execute_case(SweepCase(1,"tiny","oracle","one","cbc",1,30,oracle_name="one_slot"),
                                  attempt,time.monotonic()+30)
    assert payload["pipeline_pass"] is False
    assert payload["validation"]["valid"] is False
    assert payload["timings_seconds"]["solve_and_extract"] >= 0
    assert payload["timings_seconds"]["refinement"] >= 0


@pytest.mark.parametrize("bound,refined,want", [(None,False,"BOUND_UNKNOWN"),(-101.,False,"BOUND_INVALID"),
                                                (-99.685,True,"QUALITY_PASS")])
def test_quality_uses_final_objective_and_does_not_promote_refinement(bound,refined,want):
    assessment = _assessment(True)
    raw = replace(assessment.accepted,evidence=replace(assessment.accepted.evidence,best_bound=bound))
    assert hasattr(worker,"quality_metrics")
    quality = worker.quality_metrics(raw,assessment.final_validation,refined=refined)
    assert quality["quality_status"] == want
    assert quality["proven_optimal"] is False


def test_native_invalid_normalized_valid_is_not_counted_as_native_pass():
    rows = [{"checkpoint":{"case":{"stage":"primary","solver_name":"cbc"},"status":"DONE"},
             "result":{"status":"DONE","payload":{"validation":{"valid":True,"issues":[]},
                 "native_validation":{"valid":False},"initial_validation":{"valid":True},
                 "pipeline_pass":True,"refinement":{"attempted":False}}}}]
    result = report.summarize_stages({"schedule":[]},rows)["cbc"]["core"]
    assert result["native_strict_pass"] == 0
    assert result["normalization_pass"] == 1
    assert result["pipeline_pass"] == 1
