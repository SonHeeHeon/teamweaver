"""Bounded diagnostic capture. Never mutates historical sweep evidence."""
import argparse
from dataclasses import asdict
from importlib.metadata import version
import math
from pathlib import Path
import subprocess
import tempfile
import time

import pulp
from core.optimize.audit_types import RawMilpSolution
from core.optimize.candidate import rebuild_plan
from core.optimize.types import PlanAssignment
from core.optimize.validation import validate_raw_solution
from experiments.phase1.checkpoint import atomic_write_json, fingerprint
from experiments.phase1.solvers import _build_model, _PulpFactory, _native_evidence
from experiments.phase1.types import BenchmarkProblem, SolverOptions


def budget_residuals(problem, raw):
    rows = []
    for j, project in enumerate(problem.graph.projects):
        terms = [p.monthly_rate * raw.a[(i, j)] for i, p in enumerate(problem.graph.people)]
        cost = sum(terms)
        absolute = cost - project.monthly_budget
        scale = max(1., abs(project.monthly_budget), sum(abs(x) for x in terms))
        rows.append({"project_id": project.id, "budget": project.monthly_budget,
                     "cost": cost, "absolute": absolute, "normalized": max(0., absolute) / scale})
    return rows


def capture_cbc(problem, *, output_format=None):
    built = _build_model(problem, _PulpFactory())
    options = SolverOptions(time_limit_seconds=problem.params.time_limit, relative_gap=problem.params.gap)
    with tempfile.TemporaryDirectory(prefix="teamweaver-c1-probe-") as directory:
        solver = pulp.PULP_CBC_CMD(msg=False, threads=1, timeLimit=options.time_limit_seconds,
                                  gapRel=options.relative_gap, logPath=str(Path(directory) / "cbc.log"),
                                  options=[] if output_format is None else [f"outputFormat {output_format}"])
        solver.tmpDir = directory
        # Diagnostic-only retention; TemporaryDirectory owns cleanup, not the solver.
        solver.delete_tmp_files = lambda *paths: None
        start = time.monotonic()
        built.problem.solve(solver)
        elapsed = time.monotonic() - start
        maps = {name: {key: variable.value() for key, variable in getattr(built, name).items()}
                for name in ("z", "a", "y", "slack")}
        values = tuple(v for mapping in maps.values() for v in mapping.values())
        evidence, available = _native_evidence("cbc", built, options, values)
        objective = pulp.value(built.problem.objective)
        files = {p.suffix: p.read_text(errors="replace") for p in Path(directory).iterdir()
                 if p.suffix in {".sol", ".log"}}
        help_text = subprocess.run([solver.path, "-?"], capture_output=True, text=True, timeout=5).stdout
        diagnostic = {"solver_path": solver.path, "solve_seconds": elapsed, "pulp_version": version("pulp"),
                      "cbc_solution_text": files.get(".sol", ""), "cbc_log": files.get(".log", ""),
                      "output_format_option_available": "output" in help_text.lower() and "format" in help_text.lower(),
                      "help_sha256": fingerprint(help_text), "options": asdict(options)}
        if not available or objective is None or not math.isfinite(objective):
            return None, diagnostic
        raw = RawMilpSolution(plan=PlanAssignment(entries=[], objective=objective, unfilled=[], violations=[], label="A"),
                              status=pulp.LpStatus[built.problem.status], objective=objective,
                              reward_pairs=built.reward_pairs, penalty_pairs=built.penalty_pairs,
                              variable_count=len(built.problem.variables()), constraint_count=len(built.problem.constraints),
                              evidence=evidence, **maps)
        return rebuild_plan(problem.graph, problem.params, raw), diagnostic


def probe_case(problem, *, source_identity, strategies=("A", "B")):
    identity = {"people": [p.model_dump(mode="json") for p in problem.graph.people],
                "projects": [p.model_dump(mode="json") for p in problem.graph.projects],
                "S": problem.S.tolist(), "C": problem.C.tolist(), "params": problem.params.model_dump()}
    raw, diagnostic = capture_cbc(problem)
    result = {"schema_version": 1, "source_identity": source_identity, "input_sha256": fingerprint(identity),
              "business_validity": "NOT_CALIBRATED", "diagnostic": diagnostic,
              "costs": {"new_license_cost": 0, "new_dependencies": 0,
                        "maintenance": "refinement and evidence code; measured extra compute separately"},
              "strategies": {}, "native_validation": None,
              "status": "NO_CANDIDATE" if raw is None else "CAPTURED"}
    if raw is None:
        return result
    result["native_validation"] = asdict(validate_raw_solution(problem.graph, problem.S, problem.C, problem.params, raw))
    result["budget_residuals"] = budget_residuals(problem, raw)
    result["native_values"] = {name: [{"key": [str(k) for k in key], "value": value} for key, value in getattr(raw, name).items()]
                               for name in ("z", "a", "y", "slack")}
    if "A" in strategies:
        result["strategies"]["A"] = {"production_policy": False, "diagnostic_tol": 1e-4,
            "valid": validate_raw_solution(problem.graph, problem.S, problem.C, problem.params, raw, tol=1e-4).valid,
            "objective_delta": 0, "allocation_delta": 0, "risk": "accepts unchanged overspent candidate"}
    if "B" in strategies:
        variant, variant_diagnostic = capture_cbc(problem,output_format=6)
        checked = validate_raw_solution(problem.graph,problem.S,problem.C,problem.params,variant) if variant else None
        acknowledged = "outputFormat was changed from 2 to 6" in variant_diagnostic["cbc_log"]
        result["strategies"]["B"] = {"status": "SUPPORTED_NOT_SELECTED" if acknowledged else "NOT_SUPPORTED", "tested_output_format":6,
            "option_acknowledged":acknowledged,"cbc_log":variant_diagnostic["cbc_log"],
            "log_sha256":fingerprint(variant_diagnostic["cbc_log"]),
            "solution_sha256":fingerprint(variant_diagnostic["cbc_solution_text"]),
            "validation":asdict(checked) if checked else None,
            "max_budget_residual":max(r["absolute"] for r in budget_residuals(problem,variant)) if variant else None,
            "solve_seconds":variant_diagnostic["solve_seconds"],
            "reason":"outputFormat support comes from actual native acknowledgement; effectiveness from measured validation",
            "production_policy":False}
    if "C" in strategies:
        from core.optimize.numerics import NumericalPolicy, assess_candidate
        assessment = assess_candidate(problem.graph, problem.S, problem.C, problem.params, raw,
                                      native_capture=raw, policy=NumericalPolicy(enabled=True))
        result["strategies"]["C"] = {"accepted": assessment.accepted is not None,
                                     "refinement": asdict(assessment.refinement),
                                     "final_validation": asdict(assessment.final_validation) if assessment.final_validation else None}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--include-refinement", action="store_true")
    args = parser.parse_args()
    from core.datagen.generator import generate_dataset
    from core.datagen.parse_reviews import parse_reviews_rule_based
    from core.datagen.fixtures_io import load_fixtures
    from core.config import FIXTURES_DIR
    from core.graph.memory_graph import MemoryGraph
    from core.optimize.milp import MilpParams
    from core.scoring.engine import ScoringEngine
    dataset = generate_dataset(25, 5, seed=2)
    graph = MemoryGraph.build(dataset, parse_reviews_rule_based(dataset.reviews))
    fixture, parsed = load_fixtures(FIXTURES_DIR)
    demo = MemoryGraph.build(fixture, parsed)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    cases = []
    for name, graph, params in [("seed2-25-5", graph, MilpParams(time_limit=60)),
                                 ("default-fixture-plan-a", demo, MilpParams(gap=.01))]:
        engine = ScoringEngine(graph)
        case = probe_case(BenchmarkProblem(graph, engine.skill_matrix({}), engine.synergy_matrix(), params),
                          source_identity=source, strategies=("A", "B", "C") if args.include_refinement else ("A", "B"))
        case["case_name"] = name
        cases.append(case)
    atomic_write_json(args.output, {"cases": cases, "business_validity": "NOT_CALIBRATED"})
    print(args.output)


if __name__ == "__main__":
    main()
