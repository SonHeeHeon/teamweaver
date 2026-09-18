# Phase 0 Model Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that the current CBC MILP implements its stated mathematics on small exact cases, independently validates returned solutions, and emits checkpointed JSON plus a beginner-readable HTML report without making business-outcome claims.

**Architecture:** Keep `solve_milp` backward compatible while adding a diagnostic path that preserves raw PuLP values. Validate those values without reading PuLP constraints, compare tiny cases against an independent enumerate-plus-`linprog` oracle, then orchestrate a bounded Phase 0 suite and render its recorded JSON.

**Tech Stack:** Python 3.12, PuLP/CBC, NumPy, SciPy `linprog`, Pydantic, pytest, self-contained HTML.

**Spec:** `docs/superpowers/specs/2026-09-18-phase0-model-validation-design.md`

## Global Constraints

- Do not claim that synthetic results predict customer or project outcomes.
- Keep `solve_milp(graph, S, C, params, extra_constraints=None) -> PlanAssignment` compatible.
- Compare exact-oracle and CBC objectives with absolute tolerance `1e-6`.
- Limit oracle inputs to at most 12 `person × project` binary decisions.
- Limit the CBC compatibility smoke to 50 people, 10 projects, seed 42, and 60 solver seconds.
- Limit the complete Phase 0 run to 240 wall-clock seconds.
- Checkpoint after every case using same-directory temporary write plus `os.replace`.
- Do not install a solver or call an external API in this plan.

---

### Task 1: Preserve a raw diagnostic MILP solution

**Files:**
- Create: `core/optimize/audit_types.py`
- Modify: `core/optimize/milp.py`
- Create: `tests/phase0/factories.py`
- Test: `tests/test_milp_diagnostics.py`

**Interfaces:**
- Produces: `RawMilpSolution`, `solve_milp_diagnostic(...) -> RawMilpSolution`.
- Preserves: `solve_milp(...) -> PlanAssignment` by returning `.plan` from the diagnostic result.

- [ ] **Step 1: Write the failing diagnostic-contract test**

```python
from tests.phase0.factories import one_project_fixture

def test_diagnostic_solution_preserves_raw_variables_and_public_plan():
    graph, S, C = one_project_fixture()
    raw = solve_milp_diagnostic(graph, S, C, MilpParams(time_limit=30))
    assert raw.status == "Optimal"
    assert raw.plan.entries
    assert raw.objective == pytest.approx(raw.plan.objective)
    assert set(raw.a) == {(0, 0), (1, 0)}
    assert raw.variable_count > 0
    assert raw.constraint_count > 0
```

Create `tests/phase0/factories.py::one_project_fixture()` with two `Grade.MID`
people (`monthly_rate=1000`, six months of `availability=1.0`), one month-0
project requiring one `Grade.MID` person with budget 5000, `S=[[0.9], [0.4]]`,
zero `C`, and a minimal graph whose `cowork_months` is a 2×2 zero matrix.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/test_milp_diagnostics.py::test_diagnostic_solution_preserves_raw_variables_and_public_plan -v`

Expected: FAIL because `core.optimize.audit_types` or `solve_milp_diagnostic` does not exist.

- [ ] **Step 3: Add the immutable raw result type**

```python
@dataclass(frozen=True)
class RawMilpSolution:
    plan: PlanAssignment
    status: str
    objective: float
    z: dict[tuple[int, int], float]
    a: dict[tuple[int, int], float]
    y: dict[tuple[int, int, int], float]
    slack: dict[tuple[int, Grade], float]
    reward_pairs: tuple[tuple[int, int], ...]
    penalty_pairs: tuple[tuple[int, int], ...]
    variable_count: int
    constraint_count: int
```

- [ ] **Step 4: Refactor the existing solve once, then snapshot values**

Move the existing body to `solve_milp_diagnostic`, snapshot every PuLP variable after the incumbent check, build the unchanged `PlanAssignment`, and implement:

```python
def solve_milp(graph, S, C, params, extra_constraints=None) -> PlanAssignment:
    return solve_milp_diagnostic(
        graph, S, C, params, extra_constraints=extra_constraints
    ).plan
```

- [ ] **Step 5: Run focused and existing MILP tests**

Run: `uv run pytest tests/test_milp_diagnostics.py tests/test_milp.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the diagnostic contract**

```bash
git add core/optimize/audit_types.py core/optimize/milp.py tests/phase0/factories.py tests/test_milp_diagnostics.py
git commit -m "feat(optimize): expose raw MILP diagnostics"
```

### Task 2: Independently recompute the objective and every constraint

**Files:**
- Create: `core/optimize/validation.py`
- Modify: `core/optimize/audit_types.py`
- Test: `tests/test_solution_validation.py`

**Interfaces:**
- Consumes: `RawMilpSolution`, `MemoryGraph`, `S`, `C`, `MilpParams`.
- Produces: `validate_raw_solution(...) -> ValidationReport` and `ObjectiveBreakdown`.

- [ ] **Step 1: Write hand-derived failing objective tests**

```python
from tests.phase0.factories import all_terms_fixture

def test_recomputed_objective_uses_all_four_terms():
    graph, S, C, params, raw_solution = all_terms_fixture()
    result = validate_raw_solution(graph, S, C, params, raw_solution)
    assert result.objective.skill == pytest.approx(1.7)
    assert result.objective.synergy == pytest.approx(0.12)
    assert result.objective.overfamiliarity == pytest.approx(-0.2)
    assert result.objective.unfilled == pytest.approx(-100.0)
    assert result.objective.total == pytest.approx(-98.38)
```

`all_terms_fixture()` uses two assigned people at allocation 1.0, project
headcount 3, `S=[[0.9], [0.8]]`, `C[0,1]=0.4`, `lam=0.3`, `mu=0.2`, one
over-familiar pair, and slack 1. Add separate copied raw dictionaries whose
single changed literal creates availability, budget, grade count, `a/z`, or
`y` violations; assert each expected issue code rather than asserting on a
mock.

- [ ] **Step 2: Run validation tests and verify RED**

Run: `uv run pytest tests/test_solution_validation.py -v`

Expected: FAIL because `validate_raw_solution` does not exist.

- [ ] **Step 3: Implement structured audit results**

```python
@dataclass(frozen=True)
class ValidationIssue:
    code: str
    location: str
    actual: float
    limit: float
    error: float

@dataclass(frozen=True)
class ObjectiveBreakdown:
    skill: float
    synergy: float
    overfamiliarity: float
    unfilled: float
    total: float

@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    issues: tuple[ValidationIssue, ...]
    objective: ObjectiveBreakdown
    solver_objective_error: float
```

- [ ] **Step 4: Implement validation without reading the PuLP model**

Iterate domain objects and raw dictionaries to verify `a/z`, monthly availability, grade counts plus slack, budgets, pair linearization, objective components, and plan extraction. Mark the report invalid if any error exceeds `tol=1e-6`.

- [ ] **Step 5: Run validation and MILP regression tests**

Run: `uv run pytest tests/test_solution_validation.py tests/test_milp_diagnostics.py tests/test_milp.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the independent validator**

```bash
git add core/optimize/audit_types.py core/optimize/validation.py tests/test_solution_validation.py
git commit -m "feat(optimize): independently validate MILP solutions"
```

### Task 3: Build the tiny exact oracle

**Files:**
- Create: `experiments/phase0/oracle.py`
- Create: `experiments/phase0/__init__.py`
- Test: `tests/phase0/test_oracle.py`

**Interfaces:**
- Consumes: base TeamWeaver model without `extra_constraints`.
- Produces: `solve_tiny_oracle(graph, S, C, params) -> OracleResult`.

- [ ] **Step 1: Write failing literal and CBC-parity tests**

```python
from tests.phase0.factories import budget_shortfall_fixture, one_project_fixture

def test_oracle_selects_the_literal_best_person():
    graph, S, C = one_slot_fixture(scores=(0.9, 0.4))
    result = solve_tiny_oracle(graph, S, C, MilpParams())
    assert result.objective == pytest.approx(0.9)
    assert result.z == {(0, 0): 1, (1, 0): 0}

def test_oracle_matches_cbc_on_budget_shortfall_case():
    graph, S, C = budget_shortfall_fixture()
    oracle = solve_tiny_oracle(graph, S, C, MilpParams())
    cbc = solve_milp_diagnostic(graph, S, C, MilpParams(time_limit=30))
    assert cbc.objective == pytest.approx(oracle.objective, abs=1e-6)
```

Extend `tests/phase0/factories.py` with `budget_shortfall_fixture()`: two
`Grade.SENIOR` people at monthly rate 1000, one project requiring two seniors
with budget 350, `S=[[0.9], [0.7]]`, zero `C`, and six months of full
availability. These literals make two minimum allocations cost 400, so a
correct solution must leave one slot unfilled.

- [ ] **Step 2: Run oracle tests and verify RED**

Run: `uv run pytest tests/phase0/test_oracle.py -v`

Expected: FAIL because `experiments.phase0.oracle` does not exist.

- [ ] **Step 3: Implement independent pair selection and enumeration**

Reject `n_people * n_projects > 12`. Enumerate bitmasks, reject grade overfill, derive nonnegative slack and binary pair products, and use `scipy.optimize.linprog(method="highs")` only for `a`. Do not import `pruned_pairs`.

- [ ] **Step 4: Add generated tiny-seed parity cases**

Use deterministic 3-person/1-project and 4-person/2-project fixtures with hand-bounded sizes. Assert objective parity and independent validation for seeds 7, 11, and 19.

- [ ] **Step 5: Run oracle, validation, and MILP tests**

Run: `uv run pytest tests/phase0/test_oracle.py tests/test_solution_validation.py tests/test_milp_diagnostics.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the oracle**

```bash
git add experiments/phase0 tests/phase0/test_oracle.py
git commit -m "feat(experiments): add exact tiny MILP oracle"
```

### Task 4: Add atomic checkpoints and the bounded Phase 0 runner

**Files:**
- Modify: `experiments/bench/harness.py`
- Create: `experiments/bench/phase0_model.py`
- Test: `tests/test_harness.py`
- Test: `tests/phase0/test_phase0_runner.py`

**Interfaces:**
- Produces: `phase0_model.run(...) -> dict` and `phase0_model.main()`.
- Persists: `experiments/results/phase0_model_validation.json` after each completed case.

- [ ] **Step 1: Write a failing atomic-replace test**

```python
def test_save_result_uses_same_directory_atomic_replace(tmp_path, monkeypatch):
    replace_calls = []
    monkeypatch.setattr(harness, "RESULTS_DIR", tmp_path)
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda src, dst: (replace_calls.append((src, dst)), real_replace(src, dst))[1])
    harness.save_result("phase0", {"case": 1})
    assert len(replace_calls) == 1
    assert Path(replace_calls[0][0]).parent == tmp_path
    assert Path(replace_calls[0][1]) == tmp_path / "phase0.json"
```

- [ ] **Step 2: Run the harness test and verify RED**

Run: `uv run pytest tests/test_harness.py::test_save_result_uses_same_directory_atomic_replace -v`

Expected: FAIL because `Path.write_text` is used directly and `os.replace` is never called.

- [ ] **Step 3: Make `save_result` atomic while preserving prior-run backups**

Serialize first, write and `fsync` a named temporary file in `RESULTS_DIR`, then call `preserve_existing(path)` and `os.replace(temp_path, path)` in a `finally` block that removes a surviving temporary file.

- [ ] **Step 4: Write failing runner-behavior tests**

```python
def test_runner_records_truth_boundaries_and_checkpoints():
    checkpoints = []
    graph, S, C = one_project_fixture()
    result = phase0_model.run(
        oracle_cases=(phase0_model.OracleCase("one-slot", graph, S, C, MilpParams()),),
        run_smoke=False,
        on_case_done=lambda partial: checkpoints.append(partial["completed_cases"]),
    )
    assert result["business_validity"] == "NOT_CALIBRATED"
    assert result["calculation_status"] == "PASS"
    assert checkpoints == [1]
```

Add tests for continuing after one failed case, monotonic budget/availability results, objective component fields, and explicit synthetic data-source labels.

- [ ] **Step 5: Run runner tests and verify RED**

Run: `uv run pytest tests/phase0/test_phase0_runner.py -v`

Expected: FAIL because `phase0_model` does not exist.

- [ ] **Step 6: Implement the bounded runner**

Run oracle parity cases, monotonicity cases, a small pair-cap comparison, and optional 50×10 CBC smoke. Measure build/solve/validate/total times with `time.perf_counter`, enforce a 240-second outer deadline between cases, and collect failures without converting them to passes.

- [ ] **Step 7: Run Phase 0 and harness tests**

Run: `uv run pytest tests/test_harness.py tests/phase0/test_phase0_runner.py -v`

Expected: PASS.

- [ ] **Step 8: Commit runner and checkpointing**

```bash
git add experiments/bench/harness.py experiments/bench/phase0_model.py tests/test_harness.py tests/phase0/test_phase0_runner.py
git commit -m "feat(experiments): checkpoint Phase 0 model validation"
```

### Task 5: Render a self-contained, trust-bounded HTML report

**Files:**
- Create: `experiments/phase0/report.py`
- Test: `tests/phase0/test_phase0_report.py`
- Generate: `outputs/phase0-model-validation.html`

**Interfaces:**
- Consumes: the saved Phase 0 result document.
- Produces: UTF-8 self-contained HTML with CSP and no external resources.

- [ ] **Step 1: Write the failing report contract test**

```python
def test_report_separates_calculation_pass_from_business_not_calibrated(tmp_path):
    out = tmp_path / "report.html"
    render_report(sample_result(), out)
    html = out.read_text("utf-8")
    assert "계산 검증" in html and "PASS" in html
    assert "사업 성과 검증" in html and "NOT_CALIBRATED" in html
    assert "실제 성과를 검증한 결과가 아닙니다" in html
    assert "https://" not in html and "http://" not in html
```

- [ ] **Step 2: Run the report test and verify RED**

Run: `uv run pytest tests/phase0/test_phase0_report.py -v`

Expected: FAIL because `experiments.phase0.report` does not exist.

- [ ] **Step 3: Implement literal data-flow visuals and tables**

Render the path `작은 문제 → CBC/정답기 → 독립 검증 → 판정`, objective components, oracle differences, constraint findings, timings, pair-cap loss, environment, and the synthetic-data warning. Escape all dynamic text with `html.escape`.

- [ ] **Step 4: Run report tests and the bundled HTML checker**

Run: `uv run pytest tests/phase0/test_phase0_report.py -v`

Run: `python3 /Users/honey/.codex/plugins/cache/openai-curated-remote/codex-eli5/0.2.6/skills/eli5/scripts/check_html.py outputs/phase0-model-validation.html --max-words 3200`

Expected: both commands PASS after generating the report from a test fixture.

- [ ] **Step 5: Commit the renderer**

```bash
git add experiments/phase0/report.py tests/phase0/test_phase0_report.py
git commit -m "feat(experiments): render Phase 0 validation report"
```

### Task 6: Execute the real bounded suite and record evidence

**Files:**
- Generate: `experiments/results/phase0_model_validation.json`
- Generate: `outputs/phase0-model-validation.html`
- Modify: `README.md`

**Interfaces:**
- Uses: `uv run python -m experiments.bench.phase0_model`.
- Verifies: all Phase 0 gates from the spec against newly generated evidence.

- [ ] **Step 1: Run all non-slow tests before the experiment**

Run: `uv run pytest -v`

Expected: PASS with zero failures.

- [ ] **Step 2: Execute the bounded Phase 0 suite**

Run: `uv run python -m experiments.bench.phase0_model`

Expected: exit 0 only when calculation status is `PASS`; JSON is checkpointed after every case and total wall time is no more than 240 seconds.

- [ ] **Step 3: Render the recorded result**

Run: `uv run python -m experiments.phase0.report experiments/results/phase0_model_validation.json outputs/phase0-model-validation.html`

Expected: output file exists and contains both `PASS` calculation status and `NOT_CALIBRATED` business status when the run passes.

- [ ] **Step 4: Document the reproducible commands**

Add a README section that names the two commands, the 240-second bound, and the statement that Phase 0 does not validate real project outcomes.

- [ ] **Step 5: Run final verification**

Run: `uv run pytest -v`

Run: `python3 /Users/honey/.codex/plugins/cache/openai-curated-remote/codex-eli5/0.2.6/skills/eli5/scripts/check_html.py outputs/phase0-model-validation.html --max-words 3200`

Run: `git diff --check`

Expected: all commands exit 0.

- [ ] **Step 6: Commit the measured evidence**

```bash
git add README.md experiments/results/phase0_model_validation.json outputs/phase0-model-validation.html
git commit -m "docs(experiments): record Phase 0 validation evidence"
```
