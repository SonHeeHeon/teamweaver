# Phase 1 checkpoint — 2026-09-21

## 현재 상태

- 브랜치: `feat/phase1-solver-benchmark`
- 작업 디렉터리: `/Users/honey/Dev/teamweaver/.worktrees/phase1-solver-benchmark`
- 분기 기준: Phase 0 `c22ca5a20754bee14841c4d2eb24f33c3104cbd2`
- 완료 범위: 장기 스윕 설계 문서 및 ELI5 HTML 작성. runner 구현·추가 솔버 설치·장기 실행은 아직 하지 않았다.
- 원격 주소 연결/PR은 사용자 요청으로 보류. 기존 main의 미추적 HTML은 수정하지 않았다.
- 설계: `docs/superpowers/specs/2026-09-21-phase1-long-sweep-design.md`
- 설명: `outputs/phase1-long-sweep-design.html`

## 고정된 결정

같은 MILP에서 CBC/HiGHS/SCIP, single thread/serial/cold process. 최대 357회,
슬롯 예산 79,650초 + 준비·복구 여유 6,750초 = 총 활성 실행 예산 24시간.
본평가 1회 240초에는 입력 읽기부터 모델 생성·독립 검증·결과 저장까지 포함한다.
48개 평가 입력, seed 100/101/102, 4규모와 4조건. 300명만 세 번, 나머지는 두 번 반복.
사업 성과는 계속 `NOT_CALIBRATED`. 상용 솔버/유료 API/클라우드 실행 없음.

## 다음 개발자가 시작할 지점

1. 위 설계와 Phase 0 설계를 읽고 구현 계획을 작성한다.
2. `core/optimize/audit_types.py`, `milp.py`, `validation.py`의 원시 해 계약부터 보강한다.
   현재 누락값의 `or 0.0` 치환과 PuLP 문자열 상태만으로는 다중 솔버 성공을 판정할 수 없다.
3. 누락/NaN/Infinity/분수해/범위 이탈/native 종료 상태 회귀 테스트를 먼저 작성한다.
4. 공통 모델 builder와 어댑터를 구현한 뒤 소형 정답 대조를 통과시킨다.
5. 강제 중단·복구·24시간 예산 보존을 가짜 worker로 검증한 뒤 장기 실행한다.

**아직 장기 실행/재개 CLI가 없다.** 구현하지 않은 명령을 실행하지 않는다.
runner가 생기면 이 파일에 실제 명령·run_id·checkpoint 경로를 추가한다.
대화 내용이나 토큰 잔량을 실행 상태 저장소로 사용하지 않는다.

## 이번 설계 작업의 검증 기록

- 기존 Phase 0 가상환경을 사용해 새 worktree에서 실행:
  `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache ../phase0-model-validation/.venv/bin/python -m pytest -q`
- 결과: **329 passed, 10 deselected, 46.83초**. slow 테스트는 기본 설정으로 제외.
  기존 PuLP/Starlette deprecation 경고가 있으며 이번 문서 작업에서 의존성은 변경하지 않았다.
- 첫 실행은 토큰 사전 다운로드 DNS 실패로 collection이 중단되었다.
  기존 로컬 캐시 경로를 지정한 재실행으로 통과; 소스 수정이나 API 호출 없음.
- HTML 검사: ELI5 checker 통과, 421 visible word units.
  기존 요청의 상세 설명 범위를 반영해 기본 120 대신 `--max-words 421` 사용.
- Chromium 화면 확인: 1280px/390px × light/dark, 모두 가로 넘침 없음.
  로컬 파일만 렌더링했고, 기본 실행 제한으로 실패한 첫 시도 후 권한 승인을 통해 확인했다.
- 시간 산술: 357슬롯 / 79,650초 / 여유 6,750초 / 평가 입력 48개 확인.
- `git diff --check` 통과. 애플리케이션 구현 코드는 이번 작업에서 변경하지 않았다.
