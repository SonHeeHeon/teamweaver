# Phase 1 Long Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable, independently validated CBC/HiGHS/SCIP benchmark runner, then start the pre-registered synthetic long sweep only after its short end-to-end gate passes.

**Architecture:** Keep TeamWeaver's current MILP semantics as the source of truth, but make the raw-solution contract reject missing/non-finite data instead of silently treating it as zero. A Phase 1 experiment package owns deterministic synthetic scenario snapshots, per-solver adapters, a one-case subprocess worker, an atomic checkpoint/manifest, and report generation. The supervisor is the only process that schedules cases and owns the 24-hour active-time ledger.

**Tech Stack:** Python 3.12, NumPy, PuLP 3.x/CBC, highspy, PySCIPOpt/SCIP, pytest, stdlib JSON/subprocess/hashlib.

**Spec:** `docs/superpowers/specs/2026-09-21-phase1-long-sweep-design.md`

## Global Constraints

- Do not change the Plan A objective, `lam=0.3`, `mu=0.2`, `min_alloc=0.2`, `clique_threshold_months=6`, `slack_penalty=100`, `pair_keep_ratio=0.15`, or `max_pairs=5000` for this benchmark.
- Compare one cold subprocess at a time and request one solver thread. Record unsupported thread options rather than silently ignoring them.
- A benchmark case has a 240-second end-to-end limit, including model build, solve, extraction, independent validation, and atomic result persistence. The child solve limit is the remaining slot time minus 10 seconds.
- Total active sweep budget is 86,400 seconds. The pre-registered schedule is 357 slots / 79,650 slot-seconds and leaves 6,750 seconds reserve. Do not add cases when a case completes early.
- Persist native termination reason, incumbent existence, incumbent objective, valid upper bound when available, version, and exact solver options. Do not equate a generic PuLP status string with a proof of optimality.
- Reject `None`, NaN, Infinity, unexpected keys, missing expected keys, fractional/negative/out-of-range binary values, and invalid y/slack values. Never substitute a missing result with zero.
- A quality pass requires independent validity, stored before deadline, finite L/U with U >= L within tolerance, and `max(0, U-L)/max(1, abs(U), abs(L)) <= 0.05`. Missing U is `BOUND_UNKNOWN`, not success.
- Use only synthetic, rule-parsed data. Every output must keep business validity `NOT_CALIBRATED` and must not claim customer/project outcome improvement.
- Keep input order, input hash, S/C hash, pair set hash, model settings, solver options, source commit, and dependency versions in the manifest. A mismatch starts a new run; it never resumes an old run.
- Save each result atomically, heartbeat at most every 15 seconds, charge unfinished/orphan work conservatively, and never terminate a process that the supervisor did not start.
- Do not start the 24-hour sweep until the focused tests, all existing non-slow tests, installation/availability check, small-oracle parity, and one 50/10 case for every available solver pass. If a solver is unavailable, record `UNAVAILABLE`; do not describe the run as a three-solver comparison.

---

## File structure

- `core/optimize/audit_types.py`: raw solver evidence and strict validation issue data.
- `core/optimize/milp.py`: CBC diagnostic evidence without changing the public `solve_milp` response.
- `core/optimize/validation.py`: strict, solver-independent raw solution validation.
- `experiments/phase1/types.py`: immutable sweep input, evidence, case, and result serialization types.
- `experiments/phase1/scenarios.py`: clone-only deterministic four-scenario input generation and canonical SHA-256 fingerprints.
- `experiments/phase1/solvers.py`: CBC/HiGHS/SCIP adapters over one common model contract.
- `experiments/phase1/checkpoint.py`: manifest, atomic result writes, and resume/active-time accounting.
- `experiments/phase1/worker.py`: one owned case process; writes raw solver log and one result.
- `experiments/phase1/runner.py`: deterministic schedule, subprocess supervision, CLI, and checkpoint updates.
- `experiments/phase1/report.py`: transparent JSON-to-HTML report.
- `tests/phase1/`: direct behavior tests, with no real 24-hour waits or real process kills.

### Task 1: Make independent solution validation fail closed

**Files:**
- Modify: `core/optimize/audit_types.py`
- Modify: `core/optimize/milp.py`
- Modify: `core/optimize/validation.py`
- Modify: `tests/phase0/factories.py`
- Modify: `tests/test_solution_validation.py`
- Test: `tests/phase1/test_raw_solution_contract.py`

**Interfaces:**
- Produces `SolverEvidence(solver_name, native_status, termination_reason, has_incumbent, best_bound, options)` and `RawMilpSolution.evidence`.
- Produces `validate_raw_solution(...) -> ValidationReport`; invalid raw fields appear as structured `ValidationIssue`s and `valid` is false.
- CBC diagnostic path sets `has_incumbent` only when all expected raw variable values are present and finite. Its unavailable bound is `None`.

- [ ] **Step 1: Write failing contract tests**

Create `tests/phase1/test_raw_solution_contract.py` using the hand-written `all_terms_fixture`. Each test must create a `dataclasses.replace(raw, ...)` value and assert the specified code occurs:

```python
@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("z", {(0, 0): float("nan"), (1, 0): 1.0}, "nonfinite_value"),
        ("a", {(0, 0): None, (1, 0): 1.0}, "missing_value"),
        ("z", {(0, 0): 0.5, (1, 0): 1.0}, "binary_domain"),
        ("z", {(0, 0): 1.0}, "missing_key"),
        ("y", {(0, 1, 0): 1.0, (9, 9, 0): 0.0}, "unexpected_key"),
    ],
)
def test_raw_contract_rejects_untrustworthy_values(field, replacement, code):
    graph, skill, synergy, params, raw = all_terms_fixture()
    report = validate_raw_solution(graph, skill, synergy, params, replace(raw, **{field: replacement}))
    assert not report.valid
    assert code in {issue.code for issue in report.issues}
```

Add one test that `RawMilpSolution` carries an evidence object and that CBC returns `best_bound is None` rather than inventing it.

- [ ] **Step 2: Run the new tests to verify RED**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_raw_solution_contract.py -q`

Expected: FAIL because `SolverEvidence`/strict validation behavior does not exist yet.

- [ ] **Step 3: Implement the smallest strict contract**

Add frozen `SolverEvidence` with JSON-safe primitive fields. Require it in all `RawMilpSolution` construction sites. In the validator, derive every expected z/a/slack/y key set; check keys before arithmetic; use `isinstance(value, (int, float)) and math.isfinite(value)`; then enforce z in [0,1] and distance to {0,1} <= tolerance, a/y in [0,1], slack >= 0. Do not use `.get(..., 0.0)` for expected raw values. Preserve the existing independent objective and display validation only after raw-contract validation has collected issues.

Set CBC evidence with `solver_name="CBC"`, a native/PuLP status string, termination reason, `has_incumbent`, `best_bound=None`, and the actual `time_limit`/gap option values. Continue preserving the public `solve_milp(...) -> PlanAssignment` contract.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_raw_solution_contract.py tests/test_solution_validation.py tests/test_milp_diagnostics.py -q`

Expected: PASS. Existing Phase 0 fixture must be updated with valid evidence rather than weakening the contract.

- [ ] **Step 5: Commit**

```bash
git add core/optimize/audit_types.py core/optimize/milp.py core/optimize/validation.py tests/phase0/factories.py tests/test_solution_validation.py tests/phase1/test_raw_solution_contract.py
git commit -m "feat: fail closed on raw solver diagnostics"
```

### Task 2: Add benchmark solver adapters and prove small parity

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `experiments/phase1/__init__.py`
- Create: `experiments/phase1/types.py`
- Create: `experiments/phase1/solvers.py`
- Test: `tests/phase1/test_solvers.py`

**Interfaces:**
- Produces `available_solvers() -> dict[str, SolverAvailability]` and `solve_case(problem: BenchmarkProblem, solver_name: str, options: SolverOptions) -> RawMilpSolution`.
- `SolverAvailability` names `AVAILABLE` or `UNAVAILABLE` and captures the import/version/error text.
- `BenchmarkProblem` carries graph, S, C, and `MilpParams`; every adapter uses its exact objects and returns a fully populated raw solution/evidence contract.

- [ ] **Step 1: Write failing adapter tests**

Create tests that use Phase 0's `all_terms_fixture` problem. Assert CBC produces independent-valid output, each unavailable optional adapter returns `UNAVAILABLE` without raising, and every available adapter has a non-empty native status, Boolean incumbent flag, recorded thread/time options, and a finite objective only when it reports an incumbent. Include a table-driven parity test: for each available solver, the small-oracle expected objective is `-98.38` within `1e-6`.

- [ ] **Step 2: Run tests to verify RED**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_solvers.py -q`

Expected: FAIL because `experiments.phase1.solvers` does not exist.

- [ ] **Step 3: Add locked benchmark dependencies and adapters**

Use `uv add --group benchmark highspy pyscipopt` from this worktree; retain the resulting exact lock entries. Do not downgrade Python or PuLP. Model the same variables/objective/constraints through one private builder that takes a backend variable/model factory. CBC may wrap the existing diagnostic implementation only if it satisfies the new evidence contract; HiGHS/SCIP must not alter coefficients or pair sets. Capture native bounds from their APIs when available; otherwise return `None`.

Each adapter must catch only import/install/solver-setup exceptions at the availability boundary and return `UNAVAILABLE`; mathematical invalidity or extraction failure must be a recorded failed solve, not `UNAVAILABLE`. Thread and time options use requested values of 1 and the supplied seconds and are reflected in evidence even when backend reports unsupported.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark pytest tests/phase1/test_solvers.py tests/phase0/test_oracle.py -q`

Expected: PASS. If either optional package cannot install on this host, its test must pass by recording `UNAVAILABLE`; document the exact exception in the test output/report.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock experiments/phase1 tests/phase1/test_solvers.py
git commit -m "feat: add auditable benchmark solver adapters"
```

### Task 3: Freeze scenario inputs and pre-registered schedule

**Files:**
- Create: `experiments/phase1/scenarios.py`
- Create: `experiments/phase1/schedule.py`
- Test: `tests/phase1/test_scenarios.py`
- Test: `tests/phase1/test_schedule.py`

**Interfaces:**
- Produces `build_snapshot(n_people, n_projects, seed, scenario) -> FrozenBenchmarkInput` with cloned domain data, S, C, pair scopes, metrics, and SHA-256 hashes.
- Produces `build_schedule() -> tuple[SweepCase, ...]` with exactly 357 ordered cases and `slot_seconds` totaling 79,650.

- [ ] **Step 1: Write failing deterministic-input tests**

Write literal-behavior tests asserting: two snapshots with identical arguments have identical hashes; applying a scenario never changes a later `datasets.build_scale(50, 10, 42)` base snapshot hash; `budget_pressure` changes each budget by exactly 0.85; the schedule has 21 30-second oracle slots, 3 60-second compatibility slots, 9 120-second pilot slots, 288 240-second primary slots, 36 240-second 300/60 confirmation slots, 48 distinct primary inputs, 357 total slots, and 79,650 total seconds. Assert the primary seeds are exactly `{100,101,102}`.

- [ ] **Step 2: Run tests to verify RED**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_scenarios.py tests/phase1/test_schedule.py -q`

Expected: FAIL because the Phase 1 scenario/schedule modules do not exist.

- [ ] **Step 3: Implement clone-only scenario transformation and schedule**

Use `copy.deepcopy` of the cached base dataset before every scenario. Implement only `baseline`, `availability_pressure`, `budget_pressure`, and `dense_collaboration`; never recalibrate capacity after the transformation. Serialize canonical, sorted JSON with decimal values represented consistently and hash it with SHA-256. Dense collaboration adds only absent undirected edges to reach `round(5.5*n_people)` edges, seeded deterministically; preserve existing edges. Construct the schedule before any result is read, cycle solver order per equal input block, and attach immutable case IDs.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_scenarios.py tests/phase1/test_schedule.py -q`

Expected: PASS and no mutation of cached `datasets.build_scale` objects.

- [ ] **Step 5: Commit**

```bash
git add experiments/phase1/scenarios.py experiments/phase1/schedule.py tests/phase1/test_scenarios.py tests/phase1/test_schedule.py
git commit -m "feat: freeze phase1 scenarios and schedule"
```

### Task 4: Build atomic checkpointing and bounded one-case supervision

**Files:**
- Create: `experiments/phase1/checkpoint.py`
- Create: `experiments/phase1/worker.py`
- Create: `experiments/phase1/runner.py`
- Test: `tests/phase1/test_checkpoint.py`
- Test: `tests/phase1/test_runner.py`

**Interfaces:**
- Produces `create_or_load_run(run_dir, manifest) -> RunState` and `record_terminal_case(state, result) -> RunState`.
- Produces `run_case_subprocess(case, run_dir, deadline_seconds, command_factory) -> CaseResult` used by `run_schedule(...)`.
- CLI is `python -m experiments.phase1.runner --run-id <id> --max-active-seconds <n> [--max-cases <n>] [--resume]`.

- [ ] **Step 1: Write failing persistence and supervisor tests**

Use temporary directories and a short Python child command. Assert: a terminal case result survives a new `create_or_load_run`; a manifest hash mismatch raises `ManifestMismatch`; a `RUNNING` case found during resume consumes its whole assigned slot and becomes `ORPHANED`; a child that exceeds `deadline_seconds=0.1` is terminated by its own process group and becomes `DEADLINE_EXCEEDED`; completed case IDs are skipped on resume; an insufficient remaining budget starts no new case. Tests must never send signals to a process not launched by the test.

- [ ] **Step 2: Run tests to verify RED**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_checkpoint.py tests/phase1/test_runner.py -q`

Expected: FAIL because the checkpoint/runner modules do not exist.

- [ ] **Step 3: Implement atomic state and owned-process control**

Write JSON through a same-directory temporary file, flush and `os.fsync`, then `os.replace`. Store manifest, event JSONL, per-case `attempt-N/result.json`, and a compact checkpoint with active-time ledger. Start each child in its own process group/session. Send termination only to that recorded group after deadline; collect stdout/stderr in the case directory. Reserve a slot before start. Every 15 seconds at most, write a heartbeat. On resume, validate manifest fingerprint before skipping terminal cases; conservatively charge an unclosed reservation the full slot duration. `--max-cases` is a test/short-run cap and does not mutate the pre-registered schedule.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_checkpoint.py tests/phase1/test_runner.py -q`

Expected: PASS. Inspect that no test leaves a child process running.

- [ ] **Step 5: Commit**

```bash
git add experiments/phase1/checkpoint.py experiments/phase1/worker.py experiments/phase1/runner.py tests/phase1/test_checkpoint.py tests/phase1/test_runner.py
git commit -m "feat: add resumable phase1 sweep supervisor"
```

### Task 5: Produce transparent reports and run the start gate

**Files:**
- Create: `experiments/phase1/report.py`
- Modify: `README.md`
- Modify: `docs/phase1-checkpoint.md`
- Test: `tests/phase1/test_phase1_report.py`

**Interfaces:**
- Produces `render_report(run_dir, output_path) -> Path` and CLI `python -m experiments.phase1.report <run_dir> outputs/phase1-solver-benchmark.html`.
- Report includes status counts, unavailable solvers, exact versions/options, hash/manifest identity, elapsed active time, validation failures, L/U/gap/quality statuses, per-case results, and visible `NOT_CALIBRATED` boundary.

- [ ] **Step 1: Write failing report test**

Create a minimal fixture run directory containing one `QUALITY_PASS`, one `BOUND_UNKNOWN`, and one `UNAVAILABLE` terminal result. Assert rendered HTML contains the case IDs, these three distinct statuses, `NOT_CALIBRATED`, and HTML-escapes a case name containing `<script>`.

- [ ] **Step 2: Run test to verify RED**

Run: `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest tests/phase1/test_phase1_report.py -q`

Expected: FAIL because the Phase 1 report module does not exist.

- [ ] **Step 3: Implement report and explicit start-gate command**

Render one self-contained HTML file with the same restrictive CSP used by Phase 0 reports. State “DESIGNED / NOT_RUN”, “PARTIAL”, or completed status based only on recorded cases. Update README with two exact commands: a short start gate using `--max-cases 3`, then the full command using `--max-active-seconds 86400 --resume`; do not write a command that claims to run 24 hours before the gate succeeds. Update `docs/phase1-checkpoint.md` with the actual dependency versions, run ID, start time, checkpoint path, and last completed case only after the command has produced them.

- [ ] **Step 4: Run report and full regression verification**

Run in order:

```bash
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark pytest tests/phase1/test_phase1_report.py -q
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark pytest -q
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark python -m experiments.phase1.runner --run-id phase1-start-gate --max-active-seconds 720 --max-cases 3
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark python -m experiments.phase1.report experiments/results/phase1/phase1-start-gate outputs/phase1-solver-benchmark.html
```

Expected: tests pass; every available solver completes its gate case with independent validity; output report renders the real status. A missing solver is visibly `UNAVAILABLE` and prevents automatic full-sweep start.

- [ ] **Step 5: Commit and start the long sweep only if the gate permits it**

```bash
git add experiments/phase1/report.py README.md docs/phase1-checkpoint.md tests/phase1/test_phase1_report.py outputs/phase1-solver-benchmark.html
git commit -m "feat: report phase1 solver sweep evidence"
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark python -m experiments.phase1.runner --run-id phase1-long-sweep --max-active-seconds 86400 --resume
```

If the gate reports any `UNAVAILABLE`, failed oracle parity, invalid solution, or missing evidence, commit the evidence and stop before the full command. Do not replace a failed case with a different input or solver setting.

## Plan self-review

- Spec coverage: Tasks 1–2 implement strict correctness and solver evidence; Task 3 freezes all 357 cases; Task 4 implements deadline/restart budget conservation; Task 5 creates transparent evidence and only then begins execution.
- Scope ruling: native APIs may differ by installed solver version. The plan requires recorded `UNAVAILABLE`/`BOUND_UNKNOWN` rather than guessing a bound or altering the benchmark.
- Placeholder scan: no task uses deferred implementation wording; each has exact files, interfaces, a failing behavior test, verification command, and commit.
- Type consistency: `BenchmarkProblem`/`SolverOptions` are defined in Task 2 and are consumed only by later runner/worker tasks; `SweepCase`, `CaseResult`, and `RunState` are defined in Task 3/4 before Task 5 consumes their serialized results.
