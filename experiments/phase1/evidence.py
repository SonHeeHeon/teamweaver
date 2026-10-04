"""Conservative, immutable interpretation of legacy and current result evidence."""
from collections import Counter
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CaseAssessment:
    status: str
    stage: str
    solver: str
    validation_state: str
    failure_category: str | None
    issue_codes: tuple[str, ...]
    error_messages: tuple[str, ...]
    evidence_source: str
    raw_candidate_available: bool


def classify_case(case: dict, result: dict | None) -> CaseAssessment:
    result = result or {}
    payload = result.get("payload") or {}
    status = result.get("status", payload.get("status", case.get("status", "NOT_RECORDED")))
    validation = payload.get("validation") or {}
    issues = validation.get("issues") or []
    structured_codes = tuple(sorted({str(i["code"]) for i in issues if isinstance(i, dict) and "code" in i}))
    termination = str((payload.get("evidence") or {}).get("termination_reason", ""))
    token = re.search(r"(?:^|;\s*)independent_validation_failed:([a-z_]+(?:,[a-z_]+)*)$", termination)
    legacy_codes = tuple(sorted(set(token.group(1).split(",")))) if token else ()
    valid = validation.get("valid")
    state = "PASS" if valid is True else "FAIL" if valid is False or legacy_codes else "UNKNOWN"
    source = "STRUCTURED" if isinstance(valid, bool) else "LEGACY_TERMINATION" if legacy_codes else "UNKNOWN"
    category = None
    if (valid is True and (legacy_codes or issues)) or (
            legacy_codes and structured_codes and structured_codes != legacy_codes):
        state, category = "UNKNOWN", "EVIDENCE_CONFLICT"
    elif state == "FAIL":
        category = "VALIDATION_REJECTED"
    else:
        category = {"INPUT_MISMATCH": "INPUT_ERROR", "ORACLE_MISMATCH": "ORACLE_MISMATCH",
                    "NO_VALID_INCUMBENT": "NO_INCUMBENT", "DEADLINE_EXCEEDED": "DEADLINE",
                    "UNAVAILABLE": "UNAVAILABLE", "ORPHANED_ACKNOWLEDGED": "ORCHESTRATION_INTERRUPTED",
                    "EVIDENCE_WRITE_ERROR": "EVIDENCE_WRITE_ERROR"}.get(status)
        if status not in {"DONE", "RUNNING"} and category is None:
            category = "UNKNOWN_FAILURE"
        if status in {"INPUT_MISMATCH", "UNAVAILABLE", "RUNNING", "NOT_RECORDED"} and state == "UNKNOWN":
            state = "NOT_RUN"
    errors = tuple(dict.fromkeys(str(value) for value in (
        payload.get("error"), result.get("error"), *(str(i) for i in issues)) if value))
    return CaseAssessment(status, case.get("stage", payload.get("stage", "UNKNOWN")),
                          case.get("solver_name", payload.get("solver_name", "UNKNOWN")),
                          state, category, structured_codes or legacy_codes, errors, source,
                          bool(case.get("raw_candidate_available") or payload.get("artifacts", {}).get("native-candidate.json")))


def assess_record(item: dict) -> CaseAssessment:
    case = {**item["checkpoint"].get("case", {}), "status": item["checkpoint"].get("status"),
            "raw_candidate_available": item.get("raw_candidate_available", False)}
    return classify_case(case, item.get("result"))


def summarize_stages(manifest: dict, rows: list[dict]) -> dict:
    summary = {}

    def bucket(case):
        solver = case.get("solver_name", "UNKNOWN")
        stage = case.get("stage", "UNKNOWN")
        stage = "core" if stage in {"primary", "confirmation"} else stage
        return summary.setdefault(solver, {}).setdefault(stage, {
            "planned": 0, "recorded": 0, "done": 0, "validated": 0, "quality_pass": 0,
            "validation_failures": 0, "failure_categories": Counter(), "issue_codes": Counter(),
            "native_strict_pass":0,"normalization_pass":0,"refined_pass":0,"pipeline_pass":0})
        # Legacy runs have no native-vs-normalized evidence: unknown, not pass.

    for case in manifest.get("schedule", []):
        bucket(case)["planned"] += 1
    for item in rows:
        case = item["checkpoint"].get("case", {})
        assessment = assess_record(item)
        values = bucket(case)
        payload = (item.get("result") or {}).get("payload",{})
        native = (payload.get("native_validation") or {}).get("valid")
        initial = (payload.get("initial_validation") or {}).get("valid")
        accepted = assessment.status == "DONE" and assessment.validation_state == "PASS"
        values["native_strict_pass"] += int(native is True)
        values["normalization_pass"] += int(native is False and initial is True)
        values["refined_pass"] += int(accepted and payload.get("refinement",{}).get("attempted") is True)
        values["pipeline_pass"] += int(accepted)
        values["recorded"] += 1
        values["done"] += int(assessment.status == "DONE")
        values["validated"] += int(assessment.validation_state == "PASS")
        values["validation_failures"] += int(assessment.validation_state == "FAIL")
        values["quality_pass"] += int(assessment.status == "DONE" and assessment.validation_state == "PASS"
                                     and (item["result"] or {}).get("payload", {}).get("quality_pass") is True)
        if assessment.failure_category:
            values["failure_categories"][assessment.failure_category] += 1
        values["issue_codes"].update(assessment.issue_codes)
    return summary
