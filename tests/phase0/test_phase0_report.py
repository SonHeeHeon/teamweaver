from experiments.phase0.report import render_report


def _sample_result():
    return {
        "phase": "phase0_model_validation",
        "calculation_status": "PASS",
        "business_validity": "NOT_CALIBRATED",
        "elapsed_seconds": 12.5,
        "data_boundary": "All current inputs are synthetic; no project outcome is inferred.",
        "solver": "CBC",
        "available_pulp_solvers": ["PULP_CBC_CMD"],
        "cases": [
            {
                "name": "one_slot",
                "passed": True,
                "cbc_objective": 0.9,
                "oracle_objective": 0.9,
                "objective_difference": 0.0,
                "validation_valid": True,
                "validation_issues": [],
                "objective_components": {"skill": 0.9, "synergy": 0.0, "overfamiliarity": 0.0, "unfilled": 0.0, "total": 0.9},
                "timings_seconds": {"total": 0.1},
            }
        ],
        "invariants": [{"name": "budget_increase", "base_objective": -99.685, "raised_objective": 1.6, "passed": True}],
        "pair_cap": {"full_reward_pairs": 190, "capped_reward_pairs": 5, "objective_delta": 0.02, "relative_loss_pct": 0.3, "passed": True},
        "smoke": {"name": "cbc_50x10_seed42", "people": 50, "projects": 10, "status": "Optimal", "objective": 40.0, "validation_valid": True, "timings_seconds": {"total": 1.2}, "passed": True},
        "failures": [],
    }


def test_report_separates_calculation_pass_from_business_not_calibrated(tmp_path):
    output = tmp_path / "phase0.html"

    render_report(_sample_result(), output)

    html = output.read_text("utf-8")
    assert "계산 검증" in html and "PASS" in html
    assert "사업 성과 검증" in html and "NOT_CALIBRATED" in html
    assert "실제 성과를 검증한 결과가 아닙니다" in html
    assert "Content-Security-Policy" in html
    assert "http://" not in html and "https://" not in html


def test_report_escapes_failure_text_instead_of_injecting_markup(tmp_path):
    result = _sample_result()
    result["failures"] = [{"name": "bad", "reason": "<script>alert('x')</script>"}]
    output = tmp_path / "phase0.html"

    render_report(result, output)

    html = output.read_text("utf-8")
    assert "&lt;script&gt;" in html
    assert "<script>alert" not in html


def test_report_uses_a_compact_visible_label_for_partial_pruning_case(tmp_path):
    result = _sample_result()
    result["cases"][0]["name"] = "generated_seed_11_partial_pruning"
    output = tmp_path / "phase0.html"

    render_report(result, output)

    html = output.read_text("utf-8")
    assert '>s11 partial<' in html
    assert 'title="generated_seed_11_partial_pruning"' in html


def test_report_preserves_significant_zeroes_in_integer_metrics(tmp_path):
    output = tmp_path / "phase0.html"

    render_report(_sample_result(), output)

    html = output.read_text("utf-8")
    assert ">190<" in html
    assert ">50 × 10<" in html
