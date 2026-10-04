# C1 Numerical Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 누락된 검증 실패를 정정하고, 엄격한 최종 검증을 유지하는 제한적 수치 복구가 C0 회귀를 안전하게 해결하는지 증명한다.

**Architecture:** 과거 증거 정정과 새로운 후보 복구를 독립 체크포인트로 분리한다. 순수 증거 분류기와 core 내부 assessment를 만들고, 진단 파일럿 통과 후 서비스·벤치 어댑터에 연결한다. 과거 run은 불변으로 두고 새 파이프라인은 새 run에서 측정한다.

**Tech Stack:** Python 3.12, 기존 PuLP/CBC·highspy·PySCIPOpt, 기존 NumPy/SciPy `linprog(method="highs")`, pytest, JSON/SHA256, self-contained HTML. 새 의존성/상용 솔버 없음.

**Spec:** `docs/superpowers/specs/2026-10-04-c1-numerical-evidence-design.md`

현재 상태: **미실행 계획**. 이번 요청은 설계·계획 작성이며 제품 코드 구현은 후속 승인 후 진행한다. 아래 체크박스는 모두 향후 작업이다.

## Global Constraints

- 검증 `tol=1e-6` 유지; 기존 실패 테스트를 xfail/skip하거나 기대치를 낮춰 통과시키지 않는다.
- 정책 `c1-near-feasible-v1`: budget-only, z exact 0/1, budget normalized residual ≤1e-7, allocation delta ≤1e-7, 복구 soft budget ≤2초. G1 실패 시 임계값 확대 금지.
- 목적 4항과 `pruned_pairs`/`_overfamiliar_pairs` 의미·시그니처 유지. 추가 callback 제약도 보존하거나 복구를 거절한다.
- 모든 결과 `NOT_CALIBRATED`; 실제 비용 단위·HR 성과를 추정하지 않는다.
- 원본 v2 run/과거 보고서/비공개 `private/**` 불변. untracked `experiments/results/phase1/` 전체를 git add하지 않는다.
- 수정은 Codex 소유 영역 및 승인된 관련 테스트. 공유 계약은 work-split 요청 선기록; API/웹/Claude 영역 수정 금지.
- native, 정규화 후 검증 후보, 복구 후 최종 해와 native/pipeline 성능을 분리한다. 새 정책은 새 run ID.
- 시작 전/병합 전 main 동기화. main 병합/push는 사용자 승인 후에만 한다.
- 각 task마다 TDD red→green, 최고 역량 독립 리뷰, 증거·잔여 위험 기록, Korean ELI5 HTML, 명시적 경로로 로컬 커밋. inline 구현이 기본이며 구현 subagent를 자동 배정하지 않는다.

## Review Focus

1. **오류 증거 부재/충돌:** Task 1의 legacy·충돌·UNKNOWN 테스트와 Task 4의 실패 sidecar 테스트로 감시한다. Optimal을 PASS로 추론하지 않는다.
2. **작은 예산 오차로 위장한 잘못된 해:** Task 3의 fractional/NaN/큰 초과/복합 위반/경계 테스트로 복구 거절을 증명한다.
3. **고정팀 복구가 원래 문제를 변경:** Task 3/5의 추가 a 제약·z 컷·품질하한·4항 목적 테스트로 감시한다.
4. **시간·상한·후처리 공정성:** Task 4/5의 deadline/BOUND_INVALID/BOUND_UNKNOWN 및 native/pipeline 집계 테스트로 감시한다.
5. **회귀를 숨긴 통합 성공:** Task 5에서 기존 6개 테스트·전체 기본 suite·Phase 0·기본 warm-up을 실제 실행하고 C2/C3와 사업효과 한계를 분리한다.

## 파일 지도

| 파일 | 책임 |
|---|---|
| 새 `experiments/phase1/evidence.py` | v1/v2 기록을 정규화하는 CaseAssessment와 순수 분류 함수 |
| 수정 `experiments/phase1/report.py` | 정규화 오류·단계별 분모·native/pipeline 결과 표시; 기존 무결성 검사 유지 |
| 새 `experiments/phase1/numerical_probe.py` | 제한된 재현·후보 비교와 비용/시간/잔차 지표 산출 |
| 새 `core/optimize/numerics.py` | NumericalPolicy, RefinementEvidence, CandidateAssessment, assessment 및 미세 LP |
| 수정 `core/optimize/milp.py` | service 내부 진단·추가 제약 투영·기존 wrapper의 최종 검증 반환 |
| 수정 `experiments/phase1/solvers.py` | 3솔버 진단 경로·native capture·실패 assessment 보존 |
| 수정 `experiments/phase1/worker.py` | v2 payload·성공/실패 sidecar·시간·SHA256 저장 |
| 수정 `experiments/phase1/runner.py` | manifest에 전체 numerical policy와 fingerprint 동결 |
| 관련 `tests/` | 아래 task별 구체적 회귀·결함 주입 |
| `outputs/phase1-c1-*`, `docs/superpowers/reviews/*` | 정정 보고서·비교지표·최고 역량 리뷰·ELI5 인계 |

`audit_types.py`/공유 도메인 모델 확장은 기본 계획에 없다. core 내부 새 타입은 다른 영역을 수정하지 않고 사용한다. 자료 보관을 위한 경량 진단 JSON만 추적하며 대규모 원본 보관은 C4에서 사용자와 결정한다.

## Task 1 — 과거 실패 증거와 분모 정정 (G0)

Files: create `experiments/phase1/evidence.py`, `tests/phase1/test_case_evidence.py`; modify `experiments/phase1/report.py`, `tests/phase1/test_phase1_report.py`.

- [ ] 기존 v2 run을 `_load_run`으로 읽고 status/stage/reason 집계만 한다. 원본 357건 해시 결속 검사를 남긴다. 분모 108/3/1/7과 예산86·이진1, raw 부재87을 설계 수치와 대조한다.
- [ ] failing tests를 작성한다: `test_payload_error_and_exact_legacy_token_are_classified`(outer error=None, budget FAIL); `test_no_incumbent_without_validation_is_unknown`(FAIL 추정 금지); `test_optimal_without_validation_is_not_pass`; `test_conflicting_evidence_is_not_success`; `test_input_and_oracle_failures_are_separate`; `test_arbitrary_error_text_is_not_validation_proof`; `test_stage_denominators_do_not_pool_preflight`; `test_error_text_is_html_escaped`.
- [ ] `uv run --offline --group benchmark pytest -q tests/phase1/test_case_evidence.py tests/phase1/test_phase1_report.py` 실행, 새 테스트가 원하는 누락 동작으로 실패하는지 확인한다(import failure만으로 red 확인을 끝내지 않는다).
- [ ] 최소 구현: `CaseAssessment`에 spec 필드를 정의하고 `classify_case(case: dict, result: dict | None) -> CaseAssessment`; report는 해당 객체만 읽어 실패·오류를 표시한다. 예정 분모는 manifest schedule에서 계산하고 recorded/DONE/validation/quality를 따로 센다. legacy 원본 status를 수정하지 않는다.
- [ ] 위 테스트 green 및 기존 report hash/path corruption 테스트 유지. 합성 축소 fixture로 87건의 원인 분류/0건 원시 해 복구를 검사한다. 원본 v2로 새 `outputs/phase1-c1-evidence-correction.html`을 생성하고 197/87/68/5 및 core DONE 21/76/72를 확인한다.
- [ ] 정정 전후 지표 JSON과 ELI5 HTML을 작성·검사하고 최고 역량 독립 리뷰를 받는다. commit: `fix: classify legacy solver validation rejections and split denominators`. 명시적 파일만 stage한다.

산출물은 이 task만으로 독립 사용 가능하다. 수치 복구가 실패해도 정정 보고서는 유지한다.

## Task 2 — 원인·후보 비교 파일럿과 정책 동결 (G1)

Files: create `experiments/phase1/numerical_probe.py`, `tests/phase1/test_numerical_probe.py`; outputs `outputs/phase1-c1-numerical-probe.json`, 해당 ELI5 HTML; review `docs/superpowers/reviews/2026-10-04-c1-g1-review.md`.

- [ ] 작은 deterministic 입력에서 `test_probe_records_absolute_and_normalized_residuals`, `test_probe_does_not_change_frozen_inputs`, `test_probe_records_failure_not_success_when_candidate_missing`, `test_probe_compares_policies_without_reclassifying_old_run`을 작성한다. 반환 지표에는 입력 해시·source commit·설치 버전·원본 후보·판정·objective delta·allocation delta·timings·비용 항목이 있어야 한다.
- [ ] `uv run --offline --group benchmark pytest -q tests/phase1/test_numerical_probe.py`로 red를 확인한다.
- [ ] `probe_case(problem, *, source_identity, strategies) -> dict` 및 CLI를 구현한다. 복구 알고리즘의 중복 구현은 만들지 않는다. 이 task는 진단/입력 캡처와 후보 A/B 측정까지 수행하고 C 측정은 Task 3의 assessment 구현을 재사용한 후 G1 최종 판정한다.
- [ ] 같은 seed2 25/5 및 기본 fixture Plan A를 제한 실행해 raw CBC solution text·파싱값·비용 재계산의 차이를 기록한다. 설치 CBC 바이너리/버전/옵션을 기록하고 정밀 출력 후보 B의 실제 지원을 확인한다. 단순 가설을 원인 확정으로 쓰지 않는다. 후보 A는 진단 비교만 하며 service validator tol은 바꾸지 않는다.
- [ ] green 테스트와 CLI 진단을 실행하고 비용은 새 패키지/상용 라이선스 0, 추가 계산 시간, 변경 파일 수·유지 위험으로 기록한다. 지원되지 않는 B는 NOT_SUPPORTED로 남긴다.
- [ ] 진단/캡처 체크포인트를 리뷰·ELI5·로컬 커밋한다. C 구현은 아직 service 비활성. 정책 가드레일을 바꿀 사유가 생기면 spec/plan을 수정해 재리뷰 및 사용자 확인 후 진행한다.

## Task 3 — 비활성 상태의 미세 LP와 독립 재검증 (G1 완결 / G2)

Files: create `core/optimize/numerics.py`, `tests/test_numerical_refinement.py`; extend `experiments/phase1/numerical_probe.py` to call it. Product integration is not part of this task.

- [ ] `test_valid_candidate_is_unchanged_without_lp`; `test_budget_only_refinement_keeps_selection_and_strictly_validates`; `test_allocation_delta_is_bounded_from_native`; `test_fractional_z_is_not_repaired`; `test_near_boundary_native_z_with_budget_never_enters_lp`; `test_native_invalid_normalized_valid_is_not_native_pass`; `test_nonfinite_and_missing_are_not_repaired`; `test_large_or_multiple_constraint_failures_are_rejected`; `test_infeasible_lp_is_rejected`; `test_lp_success_with_invalid_result_is_rejected`; `test_deadline_leaves_no_time_for_refinement`; `test_success_after_budget_is_rejected`; `test_disabled_policy_retains_rejection`을 작성한다.
- [ ] `uv run --offline --group benchmark pytest -q tests/test_numerical_refinement.py`로 원하는 red를 확인한다. deterministic budget fixture는 mock validator가 아니라 실제 validator로 실패/복구를 검증한다.
- [ ] spec의 정확한 NumericalPolicy/CandidateAssessment 계약을 구현한다. `assess_candidate(..., *, native_capture, policy, deadline=None, extra_linear_constraints=())`는 원본을 mutate하지 않으며 단 한 번의 LP와 재검증을 수행한다. native/initial/final validation을 구분하고 정확한 raw z 자격과 ±1e-7 a 경계는 정규화 전 native 기준이다. LP는 fixed z, 원래 availability/budget, -S 선형 목적을 쓴다. y/slack 및 3개 상수항 유지. 최종 plan/4항 objective를 재구성한다. 설치 SciPy의 지원 옵션과 미사용 옵션 경고를 확인한다.
- [ ] 추가 제약 투영 경계를 구현한다: spec의 `LinearAllocationConstraint(coefficients, sense, rhs)`와 `extra_linear_constraints=()` hook으로 service의 원래 제약을 전달한다. numerics 내부 `capture_model_contract(prob)`와 `project_additive_constraints(before, after, *, allocation_variables, fixed_values)`를 구현하고 `UnsupportedModelMutation`을 처리한다. callback 적용 전후 bounds/category/objective/base rows를 비교해 추가 선형 행 이외의 변형은 복구를 거절한다. `test_extra_a_constraint_is_preserved`, `test_constant_only_cut_is_checked`, `test_callback_bound_change_without_rows_fails_closed`, `test_callback_category_or_objective_mutation_fails_closed`, `test_callback_conflicting_bounds_fails_closed`, `test_unsupported_extra_constraint_fails_closed`로 실제 PuLP 캡처/투영 경로를 검증한다.
- [ ] 위 테스트 green. 초기·최종 잔차/최대delta/목적변화/elapsed/보조 엔진 버전을 검증하고 원본 객체/hash 불변을 확인한다. LP timeout 및 재검증 실패를 주입해 거절 기록을 확인한다.
- [ ] Task 2 probe로 후보 C를 실행해 A/B/C 지표를 완성한다. **G1 gate:** 두 회귀 입력 strict-pass, 잘못된 후보 수용 0, 추가 제약 보존, 고정 delta/시간 가드레일 및 단위 설명 충족 여부를 최고 역량 독립 리뷰로 판정한다. 미충족이면 service 연결 없이 중단·인계한다. G1 PASS가 원인86건 확정/사업효과 증명은 아니다.
- [ ] task ELI5 및 진단 지표를 기록하고 commit: `feat: add gated fixed-selection numerical refinement`. 정책 기본 enabled=False는 여기까지 유지한다.

## Task 4 — 벤치 연결과 실패 원시 증거 저장 (G2)

Files: modify `experiments/phase1/solvers.py`, `experiments/phase1/worker.py`, `experiments/phase1/report.py`, `experiments/phase1/runner.py`; tests extend `tests/phase1/test_solvers.py`, `tests/phase1/test_checkpoint.py` and add `tests/phase1/test_candidate_evidence.py`. G1 PASS prerequisite.

- [ ] `test_diagnostic_keeps_native_and_validation_candidates_distinct`, `test_old_solve_case_wrapper_returns_only_accepted_solution`, `test_solve_error_keeps_optional_assessment`, `test_rejected_candidate_is_persisted_and_hash_bound`, `test_only_accepted_solution_gets_raw_solution_file`, `test_nonfinite_candidate_has_valid_tagged_json`, `test_sidecar_tamper_is_rejected`, `test_evidence_write_error_never_becomes_done`, `test_refinement_uses_existing_guard_and_deadline`을 작성한다.
- [ ] `uv run --offline --group benchmark pytest -q tests/phase1/test_solvers.py tests/phase1/test_candidate_evidence.py tests/phase1/test_phase1_report.py`로 원하는 red를 확인한다.
- [ ] `solve_case_diagnostic(problem, solver_name, options, *, numerical_policy, deadline) -> CandidateAssessment`를 추가한다. 기존 `solve_case`는 호환 wrapper이며 3솔버가 같은 assessment를 공유한다. SolverSolveError의 optional assessment/native capture를 추가하되 `(message, evidence)` 호출은 유지한다. 기존 벤치 0/1 canonicalization 전 값을 별도로 캡처한다.
- [ ] worker가 예외를 terminal payload로 바꾸기 전에 sidecar를 원자 저장한다. worker payload만 schema_version=2, 외부 CaseResult/checkpoint v1 유지. sidecar path+SHA256, native/initial/final validation과 refinement/timings를 기록한다. 성공/실패 공통 경로를 쓰고 nonfinite JSON 태그를 명시한다. path를 attempt 외부로 해석하지 않는다. reader는 payload v1/v2를 지원하며 sidecar hash 결속도 검사한다.
- [ ] runner manifest에 numerical_policy의 version/enabled/전체 임계값과 fingerprint를 포함한다. worker는 manifest와 실제 정책의 일치를 검사한다. `test_manifest_binds_all_policy_fields`, `test_policy_change_refuses_resume`, `test_legacy_missing_policy_is_unknown_not_new_default`를 red→green으로 검증한다. legacy를 보고서로 읽는 것과 새 정책 실행/재개를 구분한다.
- [ ] 상한 미상 유지·복구 proven_optimal=False·재계산 objective 기준 gap·BOUND_INVALID 거절 테스트를 추가한다. native strict-pass와 pipeline-pass가 같다고 잘못 집계되는 결함을 주입해 test가 실패함을 확인한다.
- [ ] green, schema/path/hash/timeout 기존 tests까지 실행한다. 기존 run을 새 정책으로 재개하지 않는지 확인. ELI5/리뷰/commit: `feat: preserve native rejected candidates and refinement evidence`.

## Task 5 — 서비스 연결, C0 회귀 해소와 제한 재비교 (G3/G4)

Files: modify `core/optimize/milp.py`; extend `tests/test_solver_validation_gate.py`, `tests/test_milp.py`, `tests/test_alternatives.py`; outputs/reviews/checkpoint docs in Codex lanes. API source unchanged. G1/G2 PASS prerequisite.

- [ ] service의 원본 후보/assessment 진단 hook과 wrapper 호환성을 TDD로 정의한다. `test_service_refines_budget_only_then_revalidates`, `test_service_never_bypasses_final_gate`, `test_service_extra_constraints_survive_refinement`, `test_alternative_quality_uses_final_objective`, `test_valid_a_invalid_b_stream_behavior_is_preserved`를 작성한다. 기존 6개 실패 테스트는 변경하지 않는다.
- [ ] `uv run --offline --group benchmark pytest -q tests/test_solver_validation_gate.py tests/test_milp.py tests/test_alternatives.py`로 red를 확인한다.
- [ ] G1 통과 정책을 내부 service 진단 경로에 연결한다. PuLP 추가 제약의 투영/검증을 전달하고 미지원이면 fail closed한다. 반환 직전 C0 독립 gate를 유지한다. 기존 solve_milp/solve_milp_diagnostic 호출 및 PlanAssignment 형식을 유지하며 native time_limit에 추가되는 복구 soft budget을 진단 증거로 기록한다.
- [ ] 같은 명령 green 확인. 전체 `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --offline --group benchmark pytest -q --tb=short --disable-warnings` 실행(프로세스 감독 테스트 권한 필요 시 승인 실행). 기대: **실패 0**; 정확한 pass 수는 실행 후 기록하며 예상 숫자를 성공으로 쓰지 않는다.
- [ ] Phase 0의 정답 대조/단조성/pair 범위 검증을 실제 재실행한다. 기존 `experiments/bench/phase0_model.py --help`로 CLI 인자를 확인한 후 동일 기본 case를 별도 산출 경로에 실행한다. 결과를 덮어쓰지 않는다.
- [ ] 기본 fixture를 그대로 읽고 API warm-up과 같은 `generate_plans` 호출을 실행한다. 기본 옵션/n_alternatives로 사전 계산 통과, raw/final feasibility와 objective를 기록한다. skip-warm 우회 금지. 실제 API lifespan smoke는 work-split에 Claude 요청을 기록한다. 실제 부팅이 미확인되면 G3를 완료로 표시하지 않는다.
- [ ] 구현 checkpoint를 커밋한 뒤 source/dependencies/policy/input을 동결한다. 3솔버의 oracle·기본·budget_pressure 소형 파일럿을 새 run ID로 cold process 실행한다(범위/기한은 실행 전 체크포인트에 명시, 케이스당 최대240초, native옵션 동일). native vs pipeline 완료·검증·gap·time를 비교하고 실패/중단도 분모에 포함한다. 장기 run 중 커밋 금지.
- [ ] 최고 역량 독립 최종 리뷰: 안전성/증거/회귀/추가 제약/공정성 각각 PASS 또는 NOT_READY. 결과 ELI5 HTML과 재현 명령·원본 해시를 기록하고 work-split/handoff를 갱신한다. 최종 로컬 커밋. **main 병합·push는 사용자 승인 전 하지 않는다.**

## 체크포인트 및 중단 조건

Task 1은 독립 완료 가능. Task 2/3은 service 비활성 상태로 G1을 판정한다. G1 실패, unsupported callback, sidecar 무결성 실패, 6개 회귀/기본 부팅 잔존, deadline 계약 위반이면 해당 gate는 NOT_READY다. C0의 현재 main 병합 보류를 해제하지 않는다. 각 task가 길어지면 red/green/리뷰 결과·현재 commit·다음 명령을 docs/superpowers에 중간 기록한다. 매 task별 HTML 인계도 누락하지 않는다.

## 계획 자체 검수

- Spec coverage: 보고서=Task1; 원인/기술비교=Task2+3; 정책/제약=Task3+5; 실패 증거/시간/상한=Task4; 회귀/Phase0/서비스/재비교=Task5.
- Task 3의 extra_linear_constraints hook은 공유 도메인 타입이 아니라 core 내부 계약이며 spec signature와 일치한다.
- 비중: 보고서 정정은 작은 독립 변경, LP/추가 제약은 높은 위험이므로 별도 gate, 통합 검증은 마지막이다. CSV·실제 성과·다른 감사 항목을 끼워 넣지 않는다.
- 검증 증거 부재를 PASS로 표현하지 않는다. 최고 역량 독립 리뷰 지적은 관련 테스트/정책 문구로 반영하고 판정을 남긴다.
