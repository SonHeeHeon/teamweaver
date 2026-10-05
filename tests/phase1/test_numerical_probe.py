import importlib
import importlib.util
from dataclasses import replace
from experiments.phase1.types import BenchmarkProblem
from core.optimize.milp import MilpParams
from tests.phase0.factories import budget_shortfall_fixture


def _module():
    assert importlib.util.find_spec("experiments.phase1.numerical_probe") is not None, "diagnostic probe is missing"
    return importlib.import_module("experiments.phase1.numerical_probe")


def _problem():
    graph, skill, synergy = budget_shortfall_fixture()
    return BenchmarkProblem(graph, skill, synergy, MilpParams(pair_keep_ratio=0, time_limit=5))


def test_probe_records_absolute_and_normalized_residuals():
    result = _module().probe_case(_problem(), source_identity="test", strategies=("A", "B"))
    assert result["native_validation"]["valid"] is True
    assert result["budget_residuals"][0]["absolute"] <= 1e-6
    assert result["budget_residuals"][0]["normalized"] <= 1e-7
    assert result["costs"]["new_license_cost"] == 0


def test_probe_does_not_change_frozen_inputs():
    problem = _problem()
    before = [p.model_dump() for p in problem.graph.people]
    result = _module().probe_case(problem, source_identity="test", strategies=("A",))
    assert [p.model_dump() for p in problem.graph.people] == before
    assert len(result["input_sha256"]) == 64
    assert result["source_identity"] == "test"


def test_probe_records_failure_not_success_when_candidate_missing(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "capture_cbc", lambda p: (None, {"reason": "missing values"}))
    result = module.probe_case(_problem(), source_identity="test", strategies=("A",))
    assert result["status"] == "NO_CANDIDATE"
    assert result["native_validation"] is None
    assert result["strategies"] == {}


def test_probe_compares_policies_without_reclassifying_old_run():
    result = _module().probe_case(_problem(), source_identity="test", strategies=("A", "B"))
    assert result["business_validity"] == "NOT_CALIBRATED"
    assert result["strategies"]["A"]["production_policy"] is False
    assert result["strategies"]["B"]["status"] in {"NOT_SUPPORTED", "SUPPORTED_NOT_SELECTED"}
    assert "historical_success" not in result


def test_output_format_candidate_is_actually_measured():
    result = _module().probe_case(_problem(), source_identity="test", strategies=("B",))
    assert result["strategies"]["B"]["tested_output_format"] == 6
    assert "validation" in result["strategies"]["B"]
    assert result["strategies"]["B"]["option_acknowledged"] is True
    assert len(result["strategies"]["B"]["solution_sha256"]) == 64
