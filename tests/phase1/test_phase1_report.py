import json

import pytest

from experiments.phase1.checkpoint import CheckpointCorrupt
from experiments.phase1.checkpoint import fingerprint
from experiments.phase1.report import render_report


def _write_run(tmp_path):
    manifest = {
        "schema_version": 1,
        "source_commit": "abc123",
        "dependency_lock_sha256": "lock-456",
        "dependency_versions": {"pulp": "3.3.2", "highspy": "1.13.0"},
        "solvers": {
            "cbc": {"state": "AVAILABLE", "import_name": "pulp.PULP_CBC_CMD",
                    "version": "PuLP 3.3.2 bundled CBC", "error": None},
            "highs": {"state": "AVAILABLE", "import_name": "highspy",
                      "version": "1.13.0", "error": None},
            "scip": {"state": "UNAVAILABLE", "import_name": "pyscipopt",
                     "version": None, "error": "not installed"},
        },
        "options": {"threads": 1, "relative_gap": 0.0, "solver_guard_seconds": 10},
        "business_validity": "NOT_CALIBRATED",
        "schedule": [
            {"case_id": "quality-<script>alert(1)", "stage": "primary",
             "input_id": "input-a", "solver_name": "cbc", "repeat": 1,
             "slot_seconds": 240},
            {"case_id": "bound-unknown", "stage": "primary", "input_id": "input-a",
             "solver_name": "highs", "repeat": 1, "slot_seconds": 240},
            {"case_id": "solver-unavailable", "stage": "oracle", "input_id": "oracle-a",
             "solver_name": "scip", "repeat": 1, "slot_seconds": 30},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    results = {
        "quality-<script>alert(1)": {
            "status": "DONE", "elapsed_seconds": 12.5,
            "payload": {"status": "DONE", "objective": 110.0, "best_bound": 112.0,
                        "normalized_gap": 0.017857, "quality_status": "QUALITY_PASS",
                        "quality_pass": True, "proven_optimal": False,
                        "validation": {"valid": True, "issues": []},
                        "evidence": {"native_status": "Time limit reached"}},
        },
        "bound-unknown": {
            "status": "DONE", "elapsed_seconds": 20.0,
            "payload": {"status": "DONE", "objective": 90.0, "best_bound": None,
                        "normalized_gap": None, "quality_status": "BOUND_UNKNOWN",
                        "quality_pass": False, "proven_optimal": False,
                        "validation": {"valid": True, "issues": []},
                        "evidence": {"native_status": "Time limit reached"}},
        },
        "solver-unavailable": {
            "status": "UNAVAILABLE", "elapsed_seconds": 0.2,
            "payload": {"status": "UNAVAILABLE", "error": "backend missing",
                        "quality_pass": False},
        },
    }
    cases = {}
    schedule_by_id = {row["case_id"]: row for row in manifest["schedule"]}
    for case_id, result in results.items():
        result_path = tmp_path / "cases" / case_id / "attempt-001" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text(json.dumps(result))
        cases[case_id] = {
            "status": result["status"], "attempt": 1, "reserved_seconds": 240,
            "elapsed_seconds": result["elapsed_seconds"], "started_at": "2026-09-30T00:00:00Z",
            "case": schedule_by_id[case_id],
            "result_path": str(result_path.relative_to(tmp_path)),
            "result_sha256": fingerprint(result),
        }
    checkpoint = {
        "schema_version": 1, "manifest_sha256": fingerprint(manifest),
        "active_seconds": 32.7, "cases": cases,
        "created_at": "2026-09-30T00:00:00Z", "updated_at": "2026-09-30T00:01:00Z",
        "status": "PARTIAL", "supervisor_active": False, "last_reserved_case": None,
    }
    checkpoint["checksum_sha256"] = fingerprint(checkpoint)
    (tmp_path / "checkpoint.json").write_text(json.dumps(checkpoint))


def test_report_exposes_distinct_evidence_and_escapes_untrusted_case_id(tmp_path):
    _write_run(tmp_path)
    output = tmp_path / "report.html"

    returned = render_report(tmp_path, output)

    html = output.read_text()
    assert returned == output
    assert "DESIGNED / NOT_RUN" not in html
    assert "PARTIAL" in html
    assert "QUALITY_PASS" in html
    assert "BOUND_UNKNOWN" in html
    assert "UNAVAILABLE" in html
    assert "quality-&lt;script&gt;alert(1)" in html
    assert "quality-<script>alert(1)" not in html
    assert "NOT_CALIBRATED" in html
    assert "32.700" in html
    assert "abc123" in html and "lock-456" in html
    assert "PuLP 3.3.2 bundled CBC" in html and "1.13.0" in html
    assert "threads" in html and "relative_gap" in html
    assert "110.000000" in html and "112.000000" in html and "1.7857%" in html
    assert "Content-Security-Policy" in html


def test_report_rejects_a_terminal_result_changed_after_checkpoint(tmp_path):
    _write_run(tmp_path)
    result_path = (tmp_path / "cases" / "bound-unknown" / "attempt-001" / "result.json")
    changed = json.loads(result_path.read_text())
    changed["payload"]["objective"] = 999999.0
    result_path.write_text(json.dumps(changed))

    with pytest.raises(CheckpointCorrupt, match="terminal result missing or changed: bound-unknown"):
        render_report(tmp_path, tmp_path / "report.html")
