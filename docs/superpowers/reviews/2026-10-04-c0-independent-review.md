# C0 독립 리뷰·검증 체크포인트 (2026-10-04)

## 판정

- 구현자: Codex. 독립 리뷰어: `gpt-6-astra`, high reasoning, 별도 에이전트 `c0_review`.
- 사용자 규칙의 최고 역량 모델·독립 리뷰 준수: **PASS**. 모델 대체 없음.
- C0 안전 동작·소유 영역 준수: **PASS**.
- 전체 회귀·서비스 가동·main 병합 준비: **FAIL / NOT_READY**.
- 이 문서는 병합 승인이 아니라, 실패를 보존한 로컬 구현 체크포인트다.

## 요구 및 구현

사용자가 승인한 범위는 서비스 `solve_milp_diagnostic`의 독립 검증 강제,
이를 공유하는 `solve_milp`와 대안 생성의 잘못된 후보 차단, 검증기의 NaN·무한대·중복 계획 방어다.
원시 해 후보를 조립한 후 `validate_raw_solution`이 통과해야 반환하고,
실패하면 native status와 issue code를 포함한 `RuntimeError`로 중단한다.
시간 제한 `Not Solved`라도 정수·기본 제약을 통과한 후보는 유지한다.
벤치마크 3솔버 어댑터는 이미 같은 검증기로 후보를 거절하는 경로가 있어 유지했다.

목적식·계수·`pruned_pairs`·`_overfamiliar_pairs`의 시그니처 및 의미는 변경하지 않았다.
Claude 소유인 API, 웹, 평가기, PDF, 리뷰 근거, 그래프 검색은 읽기만 했다.
관련 테스트는 사용자가 승인한 구현 설계에 포함됐다.

## TDD 및 현재 검증

- 새 관문/공개 값/중복 테스트 최초 실행: **17 failed, 12 passed**.
  분수·예산 위반이 세 서비스 반환 경로를 빠져나감, 실제 CBC 0초 분수 해,
  NaN 값·중복 plan entry가 검증을 통과하는 기존 결함을 확인했다.
- 비유한 재계산 목적/제약 경계 테스트: 비교 가드를 넣기 전 **3 failed** 확인.
- 최종 관련 테스트: **64 passed**.
  `tests/test_solver_validation_gate.py`, `tests/phase1/test_raw_solution_contract.py`,
  `tests/test_solution_validation.py`, `tests/phase1/test_solvers.py`.
- 최종 전체 테스트:
  `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --offline --group benchmark pytest -q --tb=short --disable-warnings`
  → **6 failed, 539 passed, 10 deselected**, 54.91초. PuLP 등의 기존 deprecation 경고 79,967개.
- 기존 회귀 테스트의 성공 기대를 수정하거나 실패를 xfail/skip 처리하지 않았다.
- 실제 CBC 0초·1초 호출, 유효한 시간 제한 후보 보존, A안 뒤의 잘못된 B안 차단,
  2.5e-6 예산 잔차를 현재 tol=1e-6에서 거절하는 테스트를 포함한다.
- main K7 문서 추가를 작업 중 반영했다. Python 코드 차이는 없었다.

### 전체 테스트의 6개 실패 (전부 `independent_validation_failed:budget`)

1. `tests/test_alternatives.py::test_alternatives_quality_and_diversity`
2. `tests/test_alternatives.py::test_diversity_binds_against_every_prior_plan_end_to_end[3]`
3. `tests/test_alternatives.py::test_alternatives_are_genuine_substitutions_not_truncations`
4. `tests/test_alternatives.py::test_generate_plans_streaming_yields_plan_a_first_then_alternatives`
5. `tests/test_alternatives.py::test_generate_plans_still_returns_a_list_backward_compatible`
6. `tests/test_milp.py::test_milp_solution_satisfies_all_constraints[2]`

## 독립 리뷰에서 확인한 결과

리뷰어는 production diff, 새/변경 테스트, wrapper/streaming 경로,
원시 계약, 검증식, `api/main.py` startup warm-up을 읽었다.
직접 실행한 결과는 targeted 49 passed/1 failed와
Phase 0/1·대안·E2E 선택 52 passed/5 failed/2 deselected였다(보완 테스트 추가 전).
validation-first와 evaluator-first의 새 프로세스 import 순서 둘 다 통과했다.

### Important: 기본 데모/API 부팅 회귀

기본 동결 fixture, 기본 MilpParams, 대안 3개 조건의 Plan A가
약 6.18초 뒤 `Optimal; independent_validation_failed:budget`로 거절됐다.
`api/main.py` warm-up은 이를 처리하지 않으므로 정상 API 부팅이 실패한다.
`TEAMWEAVER_SKIP_WARM=1`을 사용하는 API 테스트만 통과했다고 정상 부팅을 주장할 수 없다.

리뷰어의 관측 잔차:

| 프로젝트 | 원시 비용 | 예산 | 양의 잔차 |
| --- | ---: | ---: | ---: |
| j01 | 3601.0000025 | 3601 | 2.499999936844688e-6 |
| j02 | 2118.000002 | 2118 | 2.000000222324161e-6 |
| j03 | 4233.0000025 | 4233 | 2.499999936844688e-6 |

구현자의 별도 seed 2 재현: j00 비용 2046.0000025, 예산 2046,
잔차 2.499999936844688e-6, 모든 z 정수. 현행 절대 tol=1e-6보다 크다.
이는 현재 검사 계약상 거절이 맞다는 증거이며, 현실 비용 초과 또는
CBC 엔진이 수학적으로 불가능한 해를 생성했다고 단정하는 증거는 아니다.

**처리:** 엄격한 관문을 유지했다. 기존 기대를 완화하지 않았다.
C1의 수치 정책·단위·출력 정밀도 분석과 Claude API warm-up 정책이 필요하다는
공유 요청을 남겼다. 실패가 해결되기 전 main 병합·서비스 적용을 보류한다.

### Minor: 이후 대안에 대한 직접 테스트

리뷰 시 첫 next()만 직접 검사하고 있었다.
**처리:** 유효한 A안이 반환된 뒤 분수 후보 B안을 주입해,
두 번째 next()가 후보를 내보내기 전에 예외를 내는 회귀 테스트를 추가했다.
tiny budget residual 거절 테스트도 추가했다. 추가 테스트를 포함한 전체 결과는 위와 같다.

## 판단 유보 범위

- 화폐 단위와 상대/절대 허용오차 정책: C1.
- 표시 최소 투입률 clamp/rounding과 표시 배정의 독립 제약 검증: C3.
- 대안 중복·빈 팀·품질 정책 및 추가 diversity cut의 독립 검증: C2.
- 실제 인력 배치의 효과: 모든 데이터 합성, `NOT_CALIBRATED`.

## 다음 단계

C1에서 실패별 잔차를 저장/분류하고, 출력 정밀도·예산 단위·허용오차의
명시적인 처리 정책을 정한 뒤 6개 회귀와 기본 부팅을 재검증한다.
현재 C0를 안전 동작까지 구현한 것으로 기록하되 전체 완료·병합 가능으로 표시하지 않는다.
ELI5 인계: `outputs/phase0-c0-eli5.html`.
origin은 설정돼 있지만 사용자 승인 전 push하지 않는다.

## 최종 재리뷰

같은 독립 `gpt-6-astra` 리뷰어가 보완 테스트와 HTML/리뷰 문서를 읽고
관련 네 파일을 직접 실행해 **64 passed, 5,963 warnings, 1.21초**를 확인했다.
스트리밍 후속 후보 테스트 지적은 해결됐고, 신규 Critical/Important 발견은 없었다.
최종 판정은 **C0 safety PASS / local checkpoint ACCEPTABLE / main merge NOT_READY**다.
최종 전체 539/6/10 결과는 구현자가 실행한 근거이며, 리뷰어는 기존 6건 실패와
기본 데모 거절을 초기 리뷰에서 독립 재현했다.
HTML 정적 검사 통과(1,752 visible word units); 전역의 상세 인계 요구에 맞춰
word budget을 확장했다. 브라우저 로컬 파일 정책 때문에 화면 렌더링 검수는 하지 않았다.
