# Phase 1 checkpoint — 2026-09-30

## 현재 상태

- 브랜치: `feat/phase1-solver-benchmark`
- 작업 디렉터리: `/Users/honey/Dev/teamweaver/.worktrees/phase1-solver-benchmark`
- 분기 기준: Phase 0 `c22ca5a20754bee14841c4d2eb24f33c3104cbd2`
- Task 1~4: 엄격한 원시 해 계약, CBC/HiGHS/SCIP 어댑터, 고정 입력·357칸 일정,
  원자 체크포인트·시간/메모리 감독 구현 완료
- Task 5: 자기완결형 결과 보고서 구현 및 확장 시작 게이트 24개 실행 완료
- 전체 장기 스윕: v1은 29번째 파일럿 종료 정리 경합으로 중단·보존, v2 시작 준비 완료
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
