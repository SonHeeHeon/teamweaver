# C1 설계·구현 계획 독립 리뷰

2026-10-04 · 대상 기준 `703bf4a`, main `f87309c` 반영.

## 최종 판정

**PASS — 설계·구현 계획 승인 가능. 구현 성공, main 병합 또는 push 승인이 아님.**

Reviewer: 독립 agent `/root/c1_design_review`, **gpt-6-astra / high reasoning**. 활성 환경에서 제공되는 최고 역량 모델을 사용했다. implementer와 별도 세션, 읽기 전용, 추가 agent 없음. fallback 없음.

Compliance verdict: **PASS** — 최고 역량·독립성·읽기 전용·사용자 요청 범위 준수. 사업 타당성은 NOT_CALIBRATED이며 runtime 구현/테스트 성공을 추정하지 않았다.

## 확인한 근거

- 설계 `docs/superpowers/specs/2026-10-04-c1-numerical-evidence-design.md`와 구현 계획 `docs/superpowers/plans/2026-10-04-c1-numerical-evidence.md` 전체.
- `core/optimize/milp.py`, `validation.py`, `alternatives.py`, `audit_types.py`; `experiments/phase1/solvers.py`, `worker.py`, `report.py`, `checkpoint.py`, `runner.py`의 관련 계약.
- work-split, handoff, project-context, CLAUDE gotchas: 파일 소유권, 두 MILP 정식, K1 평가기 의존성, 새 run/source 동결 규칙.
- reviewer도 `.venv/bin/python -B`와 `_load_run`으로 **357개 terminal 해시 결속 통과**를 독립 확인. status **197/87/68/5**, core DONE **CBC21/HiGHS76/SCIP72**, legacy 거절 **CBC budget85+binary1, HiGHS budget1**, 해당 87건 raw 부재.
- HTML 전체와 정적 파서: 한국어, 설명 절8개, SVG2개, 중복ID0, 외부자산0. 내용·정적 구성 PASS. 시각 렌더링 미검증.

기존 **539 passed / 6 failed / 10 deselected**와 기본 warm-up 거절은 C0 기록을 인용했다. 이번 설계 작업에서 솔버 파일럿·회귀 테스트·API 부팅을 실행하지 않았다.

## 최초 리뷰와 수정 내역

최초 판정 REVISE. 아래 4건을 수정 후 재리뷰에서 모두 해결 확인, 남은 MUST 없음.

| 등급 | 발견 | 보완 및 검증 계획 |
|---|---|---|
| MUST-1 | 벤치의 기존 1e-6 경계 정규화 후 후보만 assessment에 주면 native 분수 z와 정규화 후 z를 구분하지 못함 | native_capture/native_validation/validation_candidate/initial/final을 분리. exact z와 a delta는 정규화 전 native 기준. native-invalid/normalized-valid를 native PASS로 세지 않는 테스트 포함 |
| MUST-2 | callback이 변수 bounds를 변경하면 constraints 행 투영만으로 보존 못함 | 복구 callback은 추가 선형 행만 지원. before/after bounds/category/objective/base rows 및 새 변수 감시. 지원 밖이면 fail closed. 실제 PuLP 캡처/투영/변형 테스트를 G1에 포함 |
| SHOULD-1 | LP success가 2초 추가 예산을 초과한 뒤 도착했을 때 판정 불명확 | 재검증까지 목표 초과면 REFINEMENT_BUDGET_EXCEEDED 거절. 실제 시간 기록 및 success_after_budget 테스트 |
| SHOULD-2 | 수치 정책 동결 위치와 schema v2 범위 불명확 | runner manifest에 전체 policy+fingerprint, worker 대조. payload만v2, 외부 CaseResult/checkpoint는v1. legacy policy 없으면 UNKNOWN |

## 타당하다고 확인한 설계

- 최종 검사 tol=1e-6 유지. 자격용 정규화 잔차/변수 변화 1e-7은 다른 목적의 보수적 복구 가드다.
- 고정 z/y/slack에서 기술항만 LP로 풀고 최종 목적 4항을 재계산하는 구성.
- 복구 후 목적 기준 대안 품질, native bound 보존, BOUND_UNKNOWN/INVALID 및 복구 proven_optimal=False.
- 과거 보고서 정정과 새 정책 실험 분리, G1 실패 시 미연결.
- 성공/실패 sidecar 상대경로·SHA256·원자 쓰기·비유한 값 태그·쓰기 실패 비성공 처리.
- 기존 6개 회귀와 실제 warm-up을 요구하고 C2/C3·사업효과를 통과했다고 주장하지 않는 G3.

## 잔여 위험·미검증

실제 LP 성공률, 설치 CBC 값 손실 원인, 제안된 1e-7/2초 정책의 충분성, 6개 회귀 해소, 정상 부팅, native/pipeline 성능은 구현 후 G1~G4에서 확인해야 한다. 이번 PASS는 이 검증을 진행할 설계의 판정일 뿐이다.

브라우저 파일 접근 정책으로 HTML 렌더링 QA는 수행하지 않았고 우회하지 않았다. 정적 체크는 가로 잘림·모바일 가독성의 실측을 대체하지 않는다. HTML 정적 체커의 상세 문서 word budget은 3300으로 설정했다(사용자 상세 인계 요구); 최종 실행값은 handoff-log에 기록한다.

source/dependency/policy가 달라지는 새 실행은 새 run ID. 원본 v2는 수정하지 않는다. main 병합/push는 승인 전 금지. 원격 origin 설정은 다운로드 가능성을 뜻하지 않는다.
