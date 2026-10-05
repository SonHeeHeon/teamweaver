"""Evidence must not infer a pass from native status or a missing error."""
import pytest
from experiments.phase1 import report


def _result(status="NO_VALID_INCUMBENT", **payload):
    return {"status": status, "error": None, "payload": payload}


def test_payload_error_and_exact_legacy_token_are_classified():
    item = report.classify_case({"stage": "primary", "solver_name": "cbc"}, _result(
        error="invalid incumbent (budget)",
        evidence={"termination_reason": "Optimal; independent_validation_failed:budget"}))
    assert item.validation_state == "FAIL"
    assert item.issue_codes == ("budget",)
    assert item.error_messages == ("invalid incumbent (budget)",)
    assert item.evidence_source == "LEGACY_TERMINATION"


def test_no_incumbent_without_validation_is_unknown():
    item = report.classify_case({}, _result())
    assert item.validation_state == "UNKNOWN"
    assert item.failure_category == "NO_INCUMBENT"


def test_optimal_without_validation_is_not_pass():
    item = report.classify_case({}, _result("DONE", evidence={"native_status": "Optimal"}))
    assert item.validation_state == "UNKNOWN"


def test_conflicting_evidence_is_not_success():
    item = report.classify_case({}, _result("DONE", validation={"valid": True, "issues": []},
        evidence={"termination_reason": "Optimal; independent_validation_failed:budget"}))
    assert item.validation_state == "UNKNOWN"
    assert item.failure_category == "EVIDENCE_CONFLICT"


@pytest.mark.parametrize("status,category", [("INPUT_MISMATCH", "INPUT_ERROR"),
                                              ("ORACLE_MISMATCH", "ORACLE_MISMATCH")])
def test_input_and_oracle_failures_are_separate(status, category):
    item = report.classify_case({}, _result(status))
    assert item.failure_category == category
    assert item.validation_state != "FAIL"


@pytest.mark.parametrize("text", ["budget problem", "prefix independent_validation_failed:budget",
                                  "Optimal; independent_validation_failed:budget nonsense"])
def test_arbitrary_error_text_is_not_validation_proof(text):
    item = report.classify_case({}, _result(error=text, evidence={"termination_reason": text}))
    assert item.validation_state == "UNKNOWN"


def test_stage_denominators_do_not_pool_preflight():
    scheduled = [{"case_id": str(i), "stage": stage, "solver_name": "cbc"}
                 for i, stage in enumerate(["primary", "confirmation", "pilot", "compatibility", "oracle"])]
    rows = [{"checkpoint": {"case": scheduled[0], "status": "DONE"},
             "result": _result("DONE", validation={"valid": True, "issues": []}, quality_pass=True)}]
    summary = report.summarize_stages({"schedule": scheduled}, rows)
    assert summary["cbc"]["core"]["planned"] == 2
    assert summary["cbc"]["core"]["recorded"] == 1
    assert summary["cbc"]["core"]["validated"] == 1
    assert summary["cbc"]["core"]["quality_pass"] == 1
    assert summary["cbc"]["pilot"]["planned"] == 1


def test_error_text_is_html_escaped():
    html = report._case_table([{"case_id": "c", "checkpoint": {"case": {}, "status": "NO_VALID_INCUMBENT"},
                               "result": _result(error="<script>payload error</script>")}])
    assert "&lt;script&gt;payload error&lt;/script&gt;" in html
    assert "<script>payload error</script>" not in html


def test_partial_structured_issues_conflicting_with_legacy_are_unknown():
    item = report.classify_case({}, _result(validation={"issues": [{"code": "budget"}]},
        evidence={"termination_reason": "Optimal; independent_validation_failed:binary_domain"}))
    assert item.failure_category == "EVIDENCE_CONFLICT"
    assert item.validation_state == "UNKNOWN"


def test_running_without_result_is_not_a_failure():
    summary = report.summarize_stages({"schedule": []}, [{"checkpoint": {
        "status": "RUNNING", "case": {"solver_name": "cbc", "stage": "primary"}}, "result": None}])
    assert summary["cbc"]["core"]["failure_categories"] == {}


def test_legacy_raw_presence_is_visible(tmp_path):
    from tests.phase1.test_phase1_report import _write_run
    _write_run(tmp_path)
    (tmp_path / "cases" / "bound-unknown" / "attempt-001" / "raw-solution.json").write_text("{}")
    _, _, rows = report._load_run(tmp_path)
    item = next(r for r in rows if r["case_id"] == "bound-unknown")
    assert item["raw_candidate_available"] is True
