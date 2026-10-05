# Phase 1 checkpoint — 2026-10-03

## 장기 스윕 v2 실행 현황 — 2026-10-02 02:24 KST 스냅샷

이 절은 실행 중인 체크포인트를 사람이 읽기 쉽게 옮긴 **시점 스냅샷**이다. 실시간 값은
`experiments/results/phase1/phase1-long-sweep-v2/checkpoint.json`이 기준이다.

- run ID: `phase1-long-sweep-v2`
- 고정 소스 커밋: `cf82e197206e6926b2ef516f6ca8038ee723b5c3`
- 원본 실행 디렉터리: `experiments/results/phase1/phase1-long-sweep-v2/`
- 상태: `PARTIAL`(아직 실행 중), supervisor 활성
- 예약/기록된 케이스: **237 / 357**
- 상태 합계: `DONE 180`, `NO_VALID_INCUMBENT 52`, `DEADLINE_EXCEEDED 2`,
  `ORPHANED_ACKNOWLEDGED 2`, `RUNNING 1`
- 누적 활성 예산: `11,747.058초`(약 3시간 15분 47초). 달력상 대기 시간은 포함하지 않는다.
- 스냅샷 당시 실행: `237-primary-n200-p40-seed100-dense_collaboration-r2-cbc`,
  heartbeat `135.052초`
- 사업 성과 검증: 계속 **`NOT_CALIBRATED`**. 이 결과는 합성 입력에서 솔버를 비교할 뿐,
  실제 인력 배치가 고객 만족이나 프로젝트 성과를 높인다는 증거가 아니다.

### 중단·복구 이력

1. 178번 CBC에서 실행 채널이 종료됐다. 체크포인트 복구는 슬롯 240초 전부를 차감하고
   `ORPHANED`로 멈췄다. 남아 있던 worker 결과는 감독자가 종료·시간·메모리를 끝까지
   확인하지 못했으므로 성과 수치로 채택하지 않고 `ORPHANED_ACKNOWLEDGED`로 보존했다.
2. 179번 HiGHS 재개를 제한된 샌드박스에서 잘못 시작해 macOS `ps`와 `killpg`가
   차단됐다. 권한 있는 읽기 전용 프로세스 조회로 잔존 runner/worker가 없음을 확인한 뒤,
   이 슬롯도 240초 전액 차감하고 미검증 worker 결과를 버렸다.
3. 이후 재개는 프로세스 그룹 확인·정리가 허용된 실행 경로만 사용했다. 180번 이후 이
   스냅샷까지 새 프로세스 감독 오류, 무결성 중단, 메모리 초과는 발생하지 않았다.

#### 2026-10-02 12:45 KST 후속 체크포인트

장기 실행 채널이 약 2시간 30분 뒤 종료되면서 237번 CBC 예약이 열려 있었다. 권한 있는
프로세스 조회에서 잔존 runner/worker가 0임을 확인했다. worker 파일은 존재했지만 감독자가
끝까지 수거·검증·정산하지 못했으므로 결과로 채택하지 않았다. 복구 로직으로 240초 전액을
차감한 뒤 `ORPHANED_ACKNOWLEDGED`로 보존했다.

- 기록된 케이스: **237 / 357**, 현재 열린 예약 없음
- 상태 합계: `DONE 180`, `NO_VALID_INCUMBENT 52`, `DEADLINE_EXCEEDED 2`,
  `ORPHANED_ACKNOWLEDGED 3`
- 누적 활성 예산: `11,747.416초`(약 3시간 15분 47초)
- 다음 재개 대상: 238번
- 세 번째 복구 원본: `recovery-backups/orphan-237/`
- 버린 worker 결과 해시와 복구 원인은 `events.jsonl`의 `ORPHAN_ACKNOWLEDGED` 사건에 기록

#### 2026-10-02 13:09 KST — 250번째 케이스 이정표

238번부터 권한 있는 감독 경로로 정상 재개했다. 249번까지 다시 프로세스 감독 오류 없이
종료·검증·예산 정산·다음 예약이 이어졌고, 250번부터 가장 큰 `300명 × 60프로젝트`
주요 실험 구간에 들어갔다.

- 기록된 케이스: **250 / 357**
- 상태 합계: `DONE 188`, `NO_VALID_INCUMBENT 56`, `DEADLINE_EXCEEDED 2`,
  `ORPHANED_ACKNOWLEDGED 3`, `RUNNING 1`
- 누적 활성 예산: `13,391.432초`(약 3시간 43분 11초)
- 실행 중: `250-primary-n300-p60-seed102-budget_pressure-r1-cbc`
- 새 무결성 중단·메모리 초과·프로세스 감독 오류: 0

#### 2026-10-02 21:17 KST — 273번째 케이스 복구 이정표

실행 채널 수명이 끝나 273번 CBC 예약이 열려 있었다. 권한 있는 조회에서 잔존
runner/worker가 0임을 확인했다. worker 파일은 감독자가 수거·검증·정산하기 전에 남은
것이므로 결과로 채택하지 않았고, 슬롯 240초 전액을 차감해
`ORPHANED_ACKNOWLEDGED`로 보존했다.

- 기록된 케이스: **273 / 357**, 현재 열린 예약 없음
- 상태 합계: `DONE 190`, `NO_VALID_INCUMBENT 63`, `DEADLINE_EXCEEDED 16`,
  `ORPHANED_ACKNOWLEDGED 4`
- 누적 활성 예산: `18,453.064초`(약 5시간 7분 33초)
- 다음 재개 대상: 274번
- 네 번째 복구 원본: `recovery-backups/orphan-273/`
- 250번 이후 새 무결성 중단·메모리 초과·솔버 사용 불가: 0

#### 2026-10-02 22:52 KST — 300번째 케이스 이정표

274번부터 권한 있는 감독 경로로 정상 재개했고, 299번까지 새 프로세스 감독 오류 없이
종료·검증·예산 정산이 이어졌다. 300번 예약 시점의 스냅샷은 다음과 같다.

- 기록된 케이스: **300 / 357**
- 상태 합계: `DONE 194`, `NO_VALID_INCUMBENT 69`, `DEADLINE_EXCEEDED 32`,
  `ORPHANED_ACKNOWLEDGED 4`, `RUNNING 1`
- 누적 활성 예산: `24,340.566초`(약 6시간 45분 41초)
- 실행 중: `300-primary-n300-p60-seed101-baseline-r1-cbc`, heartbeat `30.009초`
- 273번 복구 이후 새 무결성 중단·메모리 초과·프로세스 감독 오류·솔버 사용 불가: 0
- 이 분포는 실행 중간값이며 솔버 선택 결론이 아니다. 합성 입력의 계산 성능만 비교하고,
  현실의 고객 만족·인력 교체·후속 과제 성과는 계속 `NOT_CALIBRATED`다.

#### 2026-10-03 13:31 KST — 331번째 케이스 복구 이정표

실행 채널이 만료된 뒤 체크포인트에는 331번 CBC 예약이 열려 있었고, 권한 있는 프로세스
조회에서 잔존 runner/worker가 0임을 확인했다. 감독자가 종료·검증·정산을 끝내지 못한
worker 결과는 성공으로 채택하지 않고, 240초 전액을 차감한
`ORPHANED_ACKNOWLEDGED`로 보존했다.

- 기록된 케이스: **331 / 357**, 현재 열린 예약 없음
- 상태 합계: `DONE 194`, `NO_VALID_INCUMBENT 80`, `DEADLINE_EXCEEDED 52`,
  `ORPHANED_ACKNOWLEDGED 5`
- 누적 활성 예산: `31,259.044초`(약 8시간 40분 59초)
- 다음 재개 대상: 332번
- 다섯 번째 복구 원본: `recovery-backups/orphan-331/`
- 버린 worker 결과 SHA-256:
  `58104c47586822a3c547efa98b7759a4ffce6f70ba9cf60ebb16e627606c4cd6`
- 300번 이후 새 무결성 중단·메모리 초과·솔버 사용 불가: 0

#### 2026-10-03 15:06 KST — 357개 전체 완료

332번부터 권한 있는 감독 경로로 재개했고, 사전 등록한 357개 케이스가 모두 터미널
상태로 기록됐다. 마지막 예약과 supervisor 표시는 정상적으로 닫혔고, 권한 있는 프로세스
조회에서도 잔존 runner/worker가 0이었다.

- 체크포인트 상태: **`COMPLETE`**
- 기록된 케이스: **357 / 357**, 열린 예약 0, `RUNNING 0`
- 상태 합계: `DONE 197`, `NO_VALID_INCUMBENT 87`, `DEADLINE_EXCEEDED 68`,
  `ORPHANED_ACKNOWLEDGED 5`
- 품질 합계: `QUALITY_PASS 168`, `BOUND_UNKNOWN 29`, `NOT_RECORDED 160`
- 독립 검증 실패 0, 사용 불가 솔버 0, 메모리 한도 중단 0
- 누적 활성 예산: `36,917.175초`(약 10시간 15분 17초)
- 원시 결과의 357개 `result.json`과 체크포인트 해시를 최종 HTML 보고서 생성 과정에서
  다시 검증했다.

오라클 21건은 세 솔버 모두 정답 대조와 독립 검증을 통과했다. 오라클을 제외한 112건씩의
기술 비교는 다음과 같다.

| 솔버 | DONE / 112 | 성공률 | 완료 건 중앙시간 | 품질 근거 | 잠정 해석 |
|---|---:|---:|---:|---|---|
| HiGHS | 79 | 70.5% | 6.61초 | 79건 `QUALITY_PASS` | 현재 모델의 잠정 기본 후보 |
| SCIP | 75 | 67.0% | 7.52초 | 75건 `QUALITY_PASS` | 200/40까지 강하지만 큰 문제는 시간 한도 위험 |
| CBC | 22 | 19.6% | 1.00초 | 22건 `BOUND_UNKNOWN` | 성공한 쉬운 건만 빨라 중앙값 비교에 생존 편향; 기본 후보 아님 |

규모별로는 HiGHS와 SCIP가 50/10, 100/20에서 각각 25/25를 완료했다. 200/40에서는
HiGHS 23/25, SCIP 25/25였지만, SCIP 완료 시간 중앙값은 약 234.6초로 240초 제한에 매우
가까웠다. 300/60에서는 HiGHS 6/37, CBC 3/37, SCIP 0/37만 완료했다. 이 9건은 모두
`availability_pressure` 시나리오였고, 300/60의 baseline·budget_pressure·dense_collaboration은
세 솔버 모두 완료 0이었다. 따라서 **HiGHS를 잠정 기본값으로 두되 300/60은 서비스 준비
완료로 보지 않고, 모델 축소·분해·warm start·운영 시간 예산을 먼저 실험한다.**

이 성공률은 같은 합성 입력과 현재 1스레드·cold process·240초 한도에서의 계산 결과다.
실제 인력 데이터와 고객 성과가 없으므로 사업 타당성은 계속 **`NOT_CALIBRATED`**다.
상용 Gurobi 비교는 아직 실행하지 않았고, 공식 가격은 공개 정가가 아니라 견적제다.
공식 30일 상용 평가판으로 동일 동결 입력을 먼저 비교한 뒤 배포 방식·동시성·사용자 수를
명시해 실제 견적을 받는 것이 다음 순서다.

복구 전 원본은 run 디렉터리의 `recovery-backups/orphan-178/`과
`recovery-backups/orphan-179/`, `recovery-backups/orphan-237/`,
`recovery-backups/orphan-273/`, `recovery-backups/orphan-331/`에 있고, 승인 사건과
버린 worker 결과 해시는 `events.jsonl`에 기록돼 있다. 다섯 건은 좋은 결과로 바꾸거나
재시도하지 않았으며, 최종
보고서에서도 정상 성공으로 세지 않는다.

### 안전한 재개 명령

아래 명령은 **macOS 프로세스 조회와 소유 프로세스 그룹 정리가 허용된 환경**에서 실행해야
한다. 제한된 샌드박스에서 직접 실행하면 `ps`/`killpg` 권한 오류로 새 orphan을 만들 수 있다.

```bash
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark \
  python -m experiments.phase1.runner \
  --run-id phase1-long-sweep-v2 --max-active-seconds 86400 --resume
```

실험 원본은 약 423MB의 큰 로컬 실행 상태라 Git에 추가하지 않았다. 실행 중에는 HEAD가
매니페스트의 고정 `source_commit`과 달라져 재개가 거부되지 않도록 문서 변경도 커밋하지
않았다. 스윕 완료 뒤 결과 HTML·ELI5 HTML·최종 체크포인트 문서를 함께 검증해 커밋한다.
현재 Git remote는 없으므로 원격 다운로드 가능 상태라고 주장하지 않는다.

## 현재 상태

- 브랜치: `feat/phase1-solver-benchmark`
- 작업 디렉터리: `/Users/honey/Dev/teamweaver/.worktrees/phase1-solver-benchmark`
- 분기 기준: Phase 0 `c22ca5a20754bee14841c4d2eb24f33c3104cbd2`
- Task 1~4: 엄격한 원시 해 계약, CBC/HiGHS/SCIP 어댑터, 고정 입력·357칸 일정,
  원자 체크포인트·시간/메모리 감독 구현 완료
- Task 5: 자기완결형 결과 보고서 구현 및 확장 시작 게이트 24개 실행 완료
- 전체 장기 스윕: v1은 29번째 파일럿 종료 정리 경합으로 중단·보존, v2 357개 완료
- 사업 성과 검증: **`NOT_CALIBRATED` 유지**
- 원격 주소/PR: 사용자 요청대로 보류 중이며 현재 Git remote가 없다.

설계는 `docs/superpowers/specs/2026-09-21-phase1-long-sweep-design.md`, 실행 계획은
`docs/superpowers/plans/2026-09-21-phase1-long-sweep-implementation.md`, 현재 실제 결과는
`outputs/phase1-solver-benchmark.html`에서 확인한다.

## 통과한 시작 게이트

- 최종 run ID: `phase1-start-gate-v3`
- 소스 커밋: `a4af4278d6aee7f4b9ebcb1ad6e5c2d6ff9f31de`
- 시작 시각: `2026-09-30T14:47:32.817282+00:00`
- 마지막 갱신: `2026-09-30T14:48:01.603043+00:00`
- 체크포인트: `experiments/results/phase1/phase1-start-gate-v3/checkpoint.json`
- 매니페스트: `experiments/results/phase1/phase1-start-gate-v3/manifest.json`
- 마지막 케이스: `024-compatibility-n50-p10-seed42-baseline-r1-scip` — `DONE`
- 사전 고정 입력: 52개, 전체 사전 등록 일정: 357개
- 게이트 기록: 작은 오라클 21개 + 50명/10프로젝트 호환성 3개 = 24개
- 활성 실행 시간: `33.39272241713479초` (입력 동결 `25.194220333127305초` 포함)
- 터미널 결과: `DONE 24`, 다른 상태 0
- 품질 상태: `QUALITY_PASS 16`, `BOUND_UNKNOWN 8`
- 사용 불가 솔버 0, 독립 검증 실패 0, 오라클 불일치 0

호환성 입력의 결과는 다음과 같다.

| 솔버 | 실행 | 독립 검증 | L | U | Gap | 품질 |
|---|---|---:|---:|---:|---:|---|
| CBC | DONE | 통과 | 16.82218815561508 | 미제공 | 계산 불가 | BOUND_UNKNOWN |
| HiGHS | DONE | 통과 | 16.822188156288163 | 16.822188156288163 | 0 | QUALITY_PASS |
| SCIP | DONE | 통과 | 16.82218815628816 | 16.82218815628812 | 허용오차 내 0 | QUALITY_PASS |

CBC의 `BOUND_UNKNOWN`은 해가 틀렸다는 뜻이 아니다. 독립 제약 검증과 작은 문제 정답 대조는
통과했지만 현재 PuLP CBC 연결이 신뢰할 수 있는 최고 가능 상한 `U`를 제공하지 않으므로,
“5% 이내 해”라는 품질 판정에는 포함하지 않는다는 뜻이다.

## 실제 실행 환경

- Python `3.12.13`, macOS `26.5.2`, arm64
- CBC: `PuLP 3.3.2 bundled CBC` — AVAILABLE
- HiGHS / highspy: `1.15.1` — AVAILABLE
- SCIP / PySCIPOpt: `6.2.1` — AVAILABLE
- numpy `2.5.1`, scipy `1.18.0`, pydantic `2.13.4`, tiktoken `0.13.0`
- 고정 옵션: 1 thread, 상대 gap 요청 0, 저장·검증 여유 10초
- 모든 케이스는 직렬·cold process로 실행한다.

## 실패 기록도 보존한다

첫 실행 `phase1-start-gate`는 24개 중 22개가 완료됐고, HiGHS·SCIP 50/10 후보가
약 `10^-15` 크기의 0/1 경계 부동소수점 잡음 때문에 엄격한 원시 해 검증에서 거부됐다.
검증기를 느슨하게 만들지 않고, 솔버 어댑터가 `1e-6` 이내의 경계 잡음만 0/1로
정규화하도록 수정했다. `2e-6` 위반은 계속 거부하는 회귀 테스트가 있다. 실패 당시 보고서는
`outputs/phase1-start-gate-v1-failed.html`에 보존한다.

두 번째 `phase1-start-gate-v2`는 52개 입력을 모두 만들었지만, macOS에서 이미 종료된
자식 프로세스 그룹을 정리할 때 발생한 `EPERM` 경합으로 준비 단계가 실패 처리됐다.
예약한 720초는 보수적으로 유지했고 같은 run ID를 재사용하지 않았다. 이미 관찰한 종료
증거를 재사용하되 모든 그룹 멤버가 좀비인지 확인하도록 수정한 뒤 v3를 새로 실행했다.

## 다음 실행

첫 장기 실행 `phase1-long-sweep`은 29번째 `n200-p40` SCIP 파일럿 워커가 유효한
`QUALITY_PASS` 결과를 저장한 직후, macOS가 종료된 그룹의 `ps` 목록을 비우거나 전이
상태로 보여 주는 구간에서 정리 안전 검사가 `EPERM`을 올려 중단됐다. 워커·pytest 잔존
프로세스는 0이었고, 체크포인트는 열린 예약을 그대로 보존했다. 당시 상태는
`outputs/phase1-long-sweep-v1-interrupted.html`에 기록했다.

수정은 이미 관찰된 리더 종료에 한해 그룹 목록이 비었거나 전부 좀비면 안전 종료로
인정하고, 살아 있는 멤버가 보이면 최대 0.5초 동안 전이 종료를 기다리는 방식이다.
0.5초 뒤에도 살아 있으면 계속 실패한다. 빈 목록을 안전하게 볼 수 있는 이유는 직접 자식을
아직 `wait()`로 수거하지 않아 PID/PGID가 다른 프로세스에 재사용될 수 없기 때문이다.
전체 기본 테스트는 **497 passed, 10 deselected**였고 테스트 뒤 잔존 워커는 없었다.

v1 매니페스트는 수정 전 소스 해시에 묶여 있으므로 억지로 재개하지 않는다. 아래 명령으로
새 `phase1-long-sweep-v2`를 시작하며, 이후 중단에는 같은 명령을 재사용한다.

```bash
TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache uv run --group benchmark \
  python -m experiments.phase1.runner \
  --run-id phase1-long-sweep-v2 --max-active-seconds 86400 --resume
```

장기 실행을 시작해도 현실의 인력 배치 효과가 증명되는 것은 아니다. 이 단계가 답하는 것은
“같은 수학 모델과 가상 입력에서 어떤 오픈소스 솔버가 제한 시간 안에 유효한 해와 상한을
얼마나 안정적으로 남기는가”뿐이다. 실제 고객 평가·인력 교체·후속 과제 같은 결과 데이터가
없으므로 서비스 효과는 계속 `NOT_CALIBRATED`다.

## 원자료 보관 (C4, 2026-10-05 claude-b)

`experiments/results/phase1/`(git 밖, run 6개, 파일 2,788개, 약 577MB)를 사용자 결정에 따라 **로컬**
`~/Dev/teamweaver-archive/phase1-results-20261005.tar.gz`(48MB)로 보관했다.
- 압축 파일 SHA-256: `99a65cd5cf17e6cb40a1813faec57f1df72a08f381d6ac627060bb809003eb38`
- 파일별 SHA-256 목록: `phase1-results-20261005.files.sha256`. 보관 직후 시험 해제해 2,788개 모두 일치를 확인했다.
- 무결성 확인·풀기·보고서 재생성 명령은 보관 폴더의 `README.md`에 있다.
- 원래 폴더(Codex worktree)는 지우지 않았다. 이 압축본은 그 폴더가 사라져도 남는 사본이다. 외부(클라우드) 백업은 하지 않았다.
