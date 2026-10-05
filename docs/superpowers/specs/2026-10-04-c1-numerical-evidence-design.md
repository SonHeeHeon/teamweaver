# C1 설계 — 실패 증거 정정과 작은 수치 오차의 제한적 복구

작성: 2026-10-04 · 기준: `703bf4a`(main `f87309c` 반영) · **설계 제안, 구현 전**.
사업 효과: **NOT_CALIBRATED**. 사용자 요청은 설계와 구현 계획이며 이번 작업에서 제품 코드는 바꾸지 않는다.

## 1. 왜 C1이 다음인가

C0는 솔버가 내놓은 후보를 별도 계산으로 검사한 뒤에만 반환하도록 했다. 그런데 CBC가 반환한 일부 후보가 예산을 아주 조금 넘는다. 이를 막자 기존 전체 테스트가 **539 passed / 6 failed / 10 deselected**가 됐고 기본 fixture를 쓰는 API 사전 계산도 거절됐다. 이는 C0 체크포인트에서 측정한 결과이지 이번 문서 작업에서 새로 실행한 결과가 아니다.

예: seed 2, 25명/5프로젝트의 j00 예산은 2046인데 계산 비용은 2046.0000025다. 초과량 약 0.0000025는 독립 검증의 현재 절대 허용오차 0.000001보다 크다. 아직 비용 단위를 실제 HR 자료와 연결하지 않았으므로 이를 원화 몇 원이라고 설명하지 않는다. 기본 100명/20프로젝트 fixture에서도 2~2.5e-6 잔차가 관측됐다.

동시에 Phase 1 보고서는 예외 안쪽 `payload.error`를 충분히 읽지 않아 실패 87건을 검증 실패로 집계하지 못했다. **기록을 정정하는 일**과 **새 후보를 안전하게 복구하는 일**은 다른 작업이다. 과거 실패를 성공으로 바꾸는 것이 C1의 목표가 아니다.

## 2. 현재 확인된 사실과 모르는 것

2026-10-04, `experiments.phase1.report._load_run`으로 v2 manifest/checkpoint/terminal result의 기존 해시 결속을 검사하고 357개 결과를 읽었다. 원본 파일은 변경하지 않았다.

| 구분 | CBC | HiGHS | SCIP | 합계 |
|---|---:|---:|---:|---:|
| 핵심 primary+confirmation 예정 | 108 | 108 | 108 | 324 |
| 핵심 DONE | 21 | 76 | 72 | 169 |
| 핵심 NO_VALID_INCUMBENT | 83 | 1 | 0 | 84 |
| 핵심 DEADLINE_EXCEEDED | 0 | 30 | 36 | 66 |
| 핵심 ORPHANED_ACKNOWLEDGED | 4 | 1 | 0 | 5 |
| pilot 예정 / DONE | 3 / 0 | 3 / 2 | 3 / 2 | 9 / 4 |
| compatibility 예정 / DONE | 1 / 1 | 1 / 1 | 1 / 1 | 3 / 3 |
| oracle 예정 / DONE | 7 / 7 | 7 / 7 | 7 / 7 | 21 / 21 |

전체 원본 상태는 DONE 197 / NO_VALID_INCUMBENT 87 / DEADLINE_EXCEEDED 68 / ORPHANED_ACKNOWLEDGED 5다. 87건의 termination 표시는 CBC budget 85, CBC binary_domain 1, HiGHS budget 1이며 **87건 모두 `raw-solution.json`이 없다**. 따라서 86건을 예산 검증 거절이라고 분류할 수 있지만 잔차 크기나 반올림 원인을 복원할 수 없다. 새 프로세스로 재현해도 당시 원시 해의 복구는 아니다.

DONE는 검증된 계산 결과가 기록됐다는 상태이지 5% 품질 증명과 같은 말이 아니다. CBC의 상한 부재는 별도로 BOUND_UNKNOWN이다. 핵심 완료 수 21/76/72는 112건을 섞은 기존 완료 수 22/79/75와 다르다. 108건에는 같은 입력의 반복 측정이 들어 있으므로 독립적인 108개 고객 사례라고 해석하지 않는다.

## 3. 범위와 비범위

포함: 오류 계층 정규화, 검증 실패 이유·분모 정정, 새로운 실패 원시 증거 저장, 예산 잔차 진단, 제한적 수치 복구의 비교·실험, C0 6개 회귀 및 기본 사전 계산 재검증.

제외: 목적식/가중치 변경, 실제 성과 학습, 필수 기술·미기재 등급 정책(C6), 대안 중복/빈 계획 정책(C2), 표시 최소 투입률 반올림 문제(C3), Gurobi(C5), 423MB 원본 아카이브 경로 결정(C4), API 오류 처리 변경(Claude 영역). C1 통과가 C2/C3나 사업 타당성 통과를 뜻하지 않는다.

`pruned_pairs`와 `_overfamiliar_pairs`의 의미·시그니처 및 목적 4항을 유지한다. `core/domain/models.py` 등 공유 계약도 이번 설계로 변경하지 않는다.

## 4. 기술 후보와 잠정 선택

| 후보 | 장점 | 위험 / 판단 |
|---|---|---|
| A. 검증 허용오차를 크게 설정 | 빠르고 추가 풀이 없음 | 비용 단위 미정이며 실제 초과까지 통과시킬 수 있음. 기준 완화로 회귀를 숨길 수 있어 기본안으로 선택하지 않음 |
| B. CBC 출력 정밀도/읽기 경로 개선 | 원인과 일치하면 후처리 없이 해결 | 설치 바이너리의 실제 지원과 값 손실 위치 확인 필요. 번들 교체·빌드·배포 차이 및 비교 조건 변경 위험 |
| C. 선택한 사람은 고정하고 투입률만 미세하게 다시 계산 | 기존 SciPy 사용, 예산을 실제로 만족시키는 최종 해를 재검증 가능 | 보조 HiGHS가 들어가므로 순수 솔버 성능과 구분 필요. 추가 시간·목적값 변화·복구 실패 가능 |

**잠정 권고는 C, 단 진단 파일럿 G1 통과 후에만 연결한다.** B가 설치 환경에서 더 작고 재현 가능한 해결로 확인되면 최고 역량 리뷰를 거쳐 설계 변경을 기록한다. 이유 없이 임계값을 넓혀 C를 통과시키지 않는다.

SciPy `linprog(method='highs')`는 연속 선형계획(사람 선택을 바꾸지 않고 수치만 계산하는 문제), 변수 경계, 부등식, 시간 제한을 지원한다. 공식 자료: <https://docs.scipy.org/doc/scipy/reference/optimize.linprog-highs.html>. 저장소에는 SciPy가 이미 있으므로 새 상용 라이선스·새 패키지 추가는 필요하지 않다. 공식 최신 문서와 설치 버전의 지원 차이는 파일럿에서 확인한다. CBC 텍스트 직렬화가 원인이라는 설명은 **가설**이며 설치 바이너리의 실제 출력과 읽기값으로 검증해야 한다.

## 5. 설계 A — 과거 보고서를 사실대로 읽기

새 `experiments/phase1/evidence.py`가 순수 함수 `classify_case(case, result) -> CaseAssessment`를 제공한다. 입력은 이미 `_load_run`으로 무결성을 확인한 행이다. 새 타입은 실험 영역 내부에 두고 원본 checkpoint 상태를 덮어쓰지 않는다.

필드: 원래 status, stage, solver, `validation_state`(PASS/FAIL/NOT_RUN/UNKNOWN), 실패 범주, issue_codes, error_messages, evidence_source(STRUCTURED/LEGACY_TERMINATION/UNKNOWN), raw_candidate_available. 여러 오류 메시지를 보존하고 화면 대표 메시지는 payload.error → result.error → issue 순으로 선택한다.

분류 우선순위:

1. 구조화된 validation.valid/issue를 우선한다. 구조화된 기록과 legacy 문구가 충돌하면 EVIDENCE_CONFLICT로 표시하고 성공에 넣지 않는다.
2. legacy의 정확한 `independent_validation_failed:<codes>` termination 토큰만 보조 근거로 파싱한다. 임의 오류 문구나 native Optimal만 보고 검증 통과를 추정하지 않는다.
3. validation rejection, input mismatch, oracle mismatch, no incumbent, deadline, unavailable, orchestration interruption, unknown failure를 구분한다. 입력/정답 불일치를 예산 검증 실패로 합치지 않는다.
4. 단순 NO_VALID_INCUMBENT는 검증 실패 증거가 없으면 NO_INCUMBENT/UNKNOWN이지 FAIL 확정이 아니다.

집계는 oracle / compatibility / pilot / core(primary+confirmation)별로 **예정·기록·완료·검증 통과·품질 통과·실패 원인**을 제시한다. core의 분모는 솔버별 108이며 미실행/중단도 분모에서 제거하지 않는다. 성공한 케이스만의 시간 중앙값은 생존 편향을 명시한다. 원본 상태 수와 정규화된 원인 수를 함께 보여준다.

정정 HTML은 새 경로 `outputs/phase1-c1-evidence-correction.html`로 생성한다. 기존 v2 manifest/checkpoint/result와 과거 보고서를 덮어쓰지 않는다. 이 변경만으로 기존 순위가 새 정책에서 검증됐다고 주장하지 않는다.

## 6. 설계 B — 새 후보의 제한적 수치 복구

### 6.1 단계와 증거

`native capture → 기존 경계 정규화(벤치의 기존 0/1 처리만) → 독립 검증 → 자격 검사 → 미세 LP → 독립 재검증 → 최종 반환/거절`.

네이티브 캡처, 검증 입력 후보, 복구 후 후보를 구분한다. service에 벤치의 경계 정규화를 몰래 추가하지 않는다. 이미 유효하면 복구하지 않는다. 후보를 추출하지 못하면 복구하지 않는다. native candidate가 분수해이면 반올림해서 인력을 선택하지 않는다.

새 `core/optimize/numerics.py`의 내부 계약:

```python
@dataclass(frozen=True)
class NumericalPolicy:
    version: str = "c1-near-feasible-v1"
    enabled: bool = False  # G1 전에는 서비스 연결 금지
    budget_relative_admission: float = 1e-7
    max_allocation_delta: float = 1e-7
    max_refinement_seconds: float = 2.0

@dataclass(frozen=True)
class CandidateAssessment:
    native_capture: RawMilpSolution | None
    native_validation: ValidationReport | None
    validation_candidate: RawMilpSolution
    initial_validation: ValidationReport
    accepted: RawMilpSolution | None
    final_validation: ValidationReport | None
    refinement: RefinementEvidence

def assess_candidate(graph, S, C, params, candidate, *, native_capture, policy,
                     deadline: float | None = None,
                     extra_linear_constraints=()) -> CandidateAssessment: ...
```

`native_capture`는 기존 경계 정규화 전 숫자와 그 숫자로 구성한 plan/objective다. 추출 불가면 None이며 원래 숫자 조각/태그와 사유는 실패 증거로 별도 저장한다. `native_validation`은 이 캡처를 독립 검사한 결과로, 캡처 불가일 때 None이지 PASS가 아니다. `validation_candidate`는 벤치의 기존 경계 정규화 후(서비스는 정규화 없음) 후보이며 `initial_validation`은 이 후보의 검사다. `final_validation`은 복구 후 검사, 복구하지 않고 유효하면 initial_validation과 동일하다. native strict-pass는 오직 native_validation.valid=True이고 정규화 후 통과 수는 별도다.

RefinementEvidence는 정책 버전, 시도 여부·종료 이유, 예산의 절대/정규화 잔차 전후, **정규화 전 native a 대비** 최대 투입률 변화, 목적값 전후, 소요 시간, 보조 엔진/설치 버전을 가진다. 서비스 `RawMilpSolution`을 확장하지 않고, 진단 경로/예외에 assessment를 제공한다. 기존 공개 `solve_milp`와 `solve_case`는 최종 검증된 결과만 반환하는 호환 wrapper로 유지한다.

### 6.2 복구 자격과 미세 LP

아래 값은 **제안된 엔지니어링 가드레일**이며 실제 인사 허용치가 아니다. G1에서 고정 후 정책 버전으로 남긴다.

- 현재 독립 검증 `tol=1e-6` 유지. initial issue_codes가 정확히 `{budget}`일 때만 복구 시도.
- native_capture가 있고 **정규화 전 원시 z**가 모두 정확히 0/1. 원시 z=0.9999995를 정규화해 복구 자격을 얻을 수 없다. y/slack/쌍/키/목적/표시 계약은 이미 검증을 통과하고 그대로 유지. native_validation도 budget만 실패해야 하며 native의 다른 위반이 정규화로 숨겨진 후보는 복구 제외.
- 프로젝트별 양의 예산 잔차를 `max(1, abs(budget), sum(abs(rate*a)))`로 나눈 값이 모두 ≤1e-7일 때만 자격 부여. 예산·단가의 단위는 입력 단위를 그대로 사용하며 재정의하지 않는다.
- 고정 z에서 a만 풀고 **정규화 전 native a** 기준 `max(min_alloc*z, native_a−1e-7) ≤ a ≤ min(z, native_a+1e-7)` 경계를 둔다. 경계가 모순이면 거절. z=0은 a=0으로 고정.
- 원래 월별 가용률 및 프로젝트 월 예산을 그대로 적용한다. 목적은 `Σ S*a` 최대화; 나머지 목적 3항은 고정 y/slack 때문에 상수다. 목적 4항과 plan.objective는 새 a로 재계산한다. 최종 공개 plan을 기존 표시 규칙으로 다시 구성하되 표시 규칙을 고치지는 않는다.
- 원시 후보와 같은 인력 구성인지 검사하고 모든 a 변화가 1e-7 이내인지 검사한다. 기존 독립 검증을 다시 호출해 `tol=1e-6` 기준을 통과한 결과만 반환한다. LP 자체 success/status만으로 통과시키지 않는다.
- NaN/무한대/결측/분수/큰 초과/다른 제약 위반은 복구 대상 아님. infeasible·timeout·예외·재검증 실패는 원래 거절을 유지하고 증거를 남긴다.

추가 제약 callback은 현재 대안 생성에서 z만 제한하지만 외부 callback은 prob를 통해 a에도 접근할 수 있다. 복구 시 기존 PuLP 문제의 **추가 제약을 고정 z/y/slack로 평가·투영하여 a-LP에 함께 넣거나, 투영 지원이 확인되지 않으면 fail closed**해야 한다. 내부 `LinearAllocationConstraint`는 `coefficients: Mapping[tuple[int,int], float]`, `sense: Literal["LE","EQ","GE"]`, `rhs: float`로 정의한다. 상수만 남는 제약도 평가한다. 미지 변수/비유한 계수는 거절한다. 원래 문제 제약 전부의 최종 잔차도 검사한다.

복구를 지원하는 callback 범위는 **기존 변수에 선형 행을 추가하는 것만**이다. 적용 전후 원래 변수의 lower/upper bounds·종류, 목적식, 원래 제약 행을 캡처·비교한다. bounds/종류/목적 변경, 기존 행 수정·삭제, 새 변수 도입이 있으면 UNSUPPORTED_MODEL_MUTATION으로 복구 거절한다. 경계 변경은 prob.constraints에 나타나지 않으므로 반드시 별도 검사한다. 유효 native 결과의 기존 반환 호환성은 유지하되 미세 복구를 지원하지 않는다는 의미다. G1은 실제 PuLP callback 변형→캡처→거절/투영을 시험해야 한다. 대안의 기존 품질 하한은 복구 후 objective로 검사하며 C2 정책은 바꾸지 않는다.

이를 위한 내부 helper는 `capture_model_contract(prob) -> ModelContractSnapshot`, `project_additive_constraints(before, after, *, allocation_variables, fixed_values) -> tuple[LinearAllocationConstraint, ...]`다. snapshot은 변수 id/name/bounds/category, 목적 계수·상수, 기존 행 계수·sense·rhs를 보존한다. projection은 지원 밖 변형에서 `UnsupportedModelMutation`을 발생시키며 assessment가 이를 거절 증거로 처리한다. helper는 numerics 내부에 두어 G1에서 service 연결 전에 실제 PuLP 객체로 검증할 수 있게 한다.

### 6.3 시간·공정성·상한

2초는 LP 함수만이 아니라 복구 자격 검사부터 재검증까지의 추가 작업 상한이다. native valid 경로의 기존 검증 비용은 별도 기록한다. 지원 엔진 time_limit을 사용하되 in-process 시간 제한은 엄격한 OS 중단 보장이 아니다. worker는 기존 부모 absolute deadline 안에서만 실행하고 기존 10초 검증/저장 guard 중 최대 2초를 복구에 할당한다. 나머지 저장 guard를 잠식하거나 240초 기한을 늘리지 않는다. 시간을 확보하지 못하면 복구하지 않는다. LP가 success를 반환해도 재검증 종료 시 남은 시간이나 2초 목표를 초과한 후보는 `REFINEMENT_BUDGET_EXCEEDED`로 거절하고 실제 경과시간을 기록한다.

서비스 native time_limit은 현재 의미를 유지하고 복구 추가 비용은 최대 2초의 soft budget으로 문서화한다. 서비스에는 부모 프로세스 감독자의 hard kill 보장이 없으므로 총 응답시간을 엄격히 native+2초라고 보장하지 않는다. hard request timeout/취소는 별도 운영 설계다.

CBC/HiGHS/SCIP에 같은 정책을 적용한다. 보고서는 **native strict-pass 성능**과 **native+LP 최종 strict-pass 성능**을 따로 제시하고 native/복구/검증/전체 시간을 기록한다. 보조 엔진이 HiGHS이므로 결과는 순수 CBC와 순수 SCIP의 비교가 아니라 해당 파이프라인 비교다.

native best_bound는 원래 MILP의 상한 그대로 보존하고 새 최종 feasible objective를 하한으로 사용한다. 상한이 없으면 BOUND_UNKNOWN 유지. 복구 LP가 optimal이라는 사실을 전체 MILP optimal로 승격하지 않는다. 복구 케이스는 우선 `proven_optimal=False`로 보수적으로 기록하며 native_status를 수정하지 않는다. bound가 최종 objective보다 허용 범위 이상 작으면 BOUND_INVALID로 품질 통과를 거절한다.

### 6.4 새로운 실패 증거

`solve_case_diagnostic(..., *, numerical_policy, deadline) -> CandidateAssessment`와 assessment를 포함하는 `SolverSolveError`를 추가한다. 기존 두 인수 예외 생성은 호환 유지한다. service에도 내부 assessment 진단 경로를 두고 반환 직전 C0 관문은 유지한다.

worker는 성공/거절 모두 추출 가능한 후보를 저장한다. `native-candidate.json`(정규화 전), `validation-candidate.json`(독립 검사 입력), `candidate-assessment.json`(검증/복구 이력), `raw-solution.json`(최종 통과한 해만)으로 구분한다. 각 sidecar 상대 경로·SHA256을 terminal payload에 포함하여 기존 terminal checksum에 결속한다. 원자 쓰기, 제한된 attempt 내부 경로, 유한 JSON 검증을 적용한다. 비유한 원시 값은 임의 0 치환이나 JSON NaN 대신 태그형 문자열로 기록하고 검증 실패로 남긴다.

실패 기록 저장 자체가 실패하면 성공으로 승격하지 않는다. terminal 결과를 쓸 수 있으면 EVIDENCE_WRITE_ERROR를 기록한다. 부모의 deadline/orphan 상태는 원래 감독자 정책을 유지한다. **worker payload만 schema_version 2**로 구분하고 외부 CaseResult/checkpoint v1은 유지한다. reader는 payload v1/v2를 읽는다. runner manifest에 numerical_policy의 version/enabled/전체 임계값과 fingerprint를 기록해 기존 manifest 결속에 포함하고 worker가 확인한다. legacy manifest에 policy가 없으면 LEGACY_POLICY_UNKNOWN으로 읽고 새로운 복구 실행에 사용하지 않는다. source/dependencies/policy를 동결한 **새 run ID**만 사용하며 기존 v2 run을 새 코드로 재개하지 않는다.

## 7. 단계별 통과 기준

**G0 — 보고서 정정:** 모든 legacy 거절 87건이 정확한 근거·원인으로 집계되고 핵심 분모가 108. 원본 357건 상태·해시 불변, 임의 문자열/Optimal에서 성공 추정 없음.

**G1 — 진단 파일럿:** 설치 CBC의 native 출력→읽기→예산 재계산 경로를 기록한다. seed2 실패와 기본 fixture Plan A를 같은 입력으로 재현하되 당시 실패87건의 복구라고 하지 않는다. A/B/C를 잔차·판정·목적 변화·변수 최대 변화·시간·의존성·설치/유지 비용으로 비교한다. C가 고정 가드레일 내에서 재검증을 통과하고 잘못된 해 0건 수용, 추가 제약 보존을 입증해야 연결 가능. 실패하면 보고서 정정만 완료하고 C0 통합 보류를 유지한다.

**G2 — 구현 안전성:** NaN/분수/큰 초과/추가 제약/LP timeout/재검증 실패 결함 주입을 거절. 성공과 실패 sidecar 해시/원자 저장을 검사. 기존 검증 tol과 테스트 기대를 낮추지 않는다.

**G3 — 통합:** 기록된 6개 테스트 회귀 해소, 전체 기본 테스트 실패 0, Phase 0 정답 대조 유지, 기본 fixture로 실제 사전 계산 경로 통과(`TEAMWEAVER_SKIP_WARM=1`로 우회한 것을 증거로 쓰지 않음). API 소스 수정 없이 검증 가능한 범위만 Codex가 확인한다. Claude의 실제 lifespan smoke는 상대에게 요청한다. 불안정 케이스는 숨기지 않는다.

**G4 — 재비교:** 구현 커밋/설치 버전/정책을 동결하고 3솔버의 제한된 새 파일럿을 실행한다. 동일 입력·native 옵션·전체 기한, cold process, 별도 run ID. native vs pipeline 성능을 분리하고 성공 케이스만의 속도로 추천하지 않는다. 24시간 전체 sweep은 자동 시작하지 않으며 작은 파일럿과 리뷰 후 별도 범위를 확인한다.

## 8. 인계·승인

설계·구현 계획·최고 역량 독립 리뷰·한국어 ELI5 HTML을 로컬 기능 브랜치에 기록한다. main 병합/push는 승인 전 금지. 현재 요청 범위는 이 문서 묶음까지이며 **계획 승인 후 G0부터 구현**한다. C0/C1 해결 후 C2/C3, C4 보존 경로 결정, C5/C6 및 Claude 서비스/CSV 축을 분담표에 맞춰 진행한다. 실데이터 스키마가 없어도 계산·증거 검증은 가능하지만 현실 성과 검증은 불가능하다.
