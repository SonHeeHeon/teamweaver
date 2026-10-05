# Claude·Codex 작업 분담 (2026-10-04 합의, 같은 날 감사 반영 갱신)

두 에이전트가 같은 파일을 동시에 고치지 않도록 **파일 영역으로 소유권을 나눈다.**
작업을 시작하기 전에 이 문서, `docs/handoff-log.md`(서로 한 일), `docs/project-context.md`(맥락)를 읽는다.

## 소유 영역

| 영역 | 소유 | 파일 |
|---|---|---|
| 최적화 모델·솔버·실험 | **Codex** | `core/optimize/**`, `experiments/**`, `outputs/phase*`, `docs/superpowers/**` |
| 제품 API·웹·PDF·입력 수집·배치 평가 | **Claude** | `api/**`, `web/**`, `core/ingest/**`, `core/evaluate/**`, `scripts/` |
| 공유 계약 | 양쪽 (변경 전 아래 "요청"에 기록) | `core/domain/models.py`, `core/scoring/**`, `core/graph/**`, `core/rag/**`, `core/datagen/**`, `core/config.py`, `fixtures/**`, `pyproject.toml`, `uv.lock`, `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/project-context.md`, `docs/handoff-log.md`, `docs/data-schema/**`, 이 문서 |
| 사용자 비공개 입력 | 사용자 (에이전트는 읽기만) | `private/**`(gitignore). 실데이터 스키마 답변 `private/schema-intake.json`. 커밋·push 금지 |

- 상대 영역은 **읽기와 import만** 한다. 수정이 필요하면 "요청"에 적고 상대가 처리한다.
- 공유 계약을 바꿀 때는 "요청"에 변경 내용과 이유를 먼저 적는다.
  상대 쪽 테스트(`uv run --group benchmark pytest -q` 전체)가 통과해야 병합한다.
- 작업 브랜치: Codex는 `.worktrees/` 아래 기능 브랜치, Claude는 `feat/claude-<slug>` 브랜치를 쓴다.
  `main` 병합은 사용자 승인으로 한다. **작업 시작 전과 병합 전에 `main`을 merge하거나 rebase해** 상대 작업을 반영한다.
- 장기 실험이 진행 중이면 Codex가 "진행 중" 절에 run ID를 적는다. 그동안 Claude는 공유 계약을 바꾸지 않는다.
- task를 끝내면 `docs/handoff-log.md` 맨 위에 항목을 추가한다. 착수하면 "진행 중"에 적고, 끝나면 지운다.

## 작업 목록
`[A-P0]` 같은 표시는 Codex 중간 품질 감사(`outputs/eli5-mid-project-quality-audit.html`, `bf50a7e`)의 심각도다.
순서는 제안이며 사용자가 바꿀 수 있다.

### Codex (모델·솔버 축) — 제안 순서
- **C0** 🟡 [A-P0] 안전 관문 구현 체크포인트 `2edd3f7`(2026-10-04), **통합 검증 보류 / C1 필요**.
  서비스 솔버 반환에 독립 검증을 강제하고 분수·무incumbent·NaN·중복 결과를 차단한다.
  관련 테스트 64 passed. 전체 539 passed / 6 failed / 10 deselected; 기본 동결 데모 warm-up도 budget 잔차로 거절된다.
  허용오차 정책과 기본 부팅을 해결하기 전 main 병합하지 않는다. 근거: `outputs/phase0-c0-eli5.html`.
- **C1** 🟡 [A-P0] 설계·구현 계획 작성 및 최고 역량 독립 설계 리뷰 PASS(2026-10-04), **구현 전 / 사용자 계획 승인 대기**.
  근거: `docs/superpowers/specs/2026-10-04-c1-numerical-evidence-design.md`, `docs/superpowers/plans/2026-10-04-c1-numerical-evidence.md`, `outputs/phase1-c1-design-eli5.html`.
  Phase 1 보고서가 `payload.error`의 거절 87건(CBC 예산 85, CBC 이진 1, HiGHS 예산 1)을 집계하고 분류하게 한다.
  예산 허용오차·단위·반올림을 분석한다. 분모를 핵심 108·파일럿 3·호환성 1로 분리한다([A-P2]).
  C0 체크포인트의 6개 회귀와 기본 API 부팅 거절도 이 수치 정책 분석의 우선 확인 대상이다.
- **C2** [A-P1] 대안 생성: 구성 서명으로 중복을 제거하고, 빈 계획을 제외하고, 최소 품질·미충원 한도를 두고, 부족하면 "대안 없음"을 반환한다(`core/optimize/alternatives.py`).
- **C3** [A-P2] 표시 투입률 반올림: `max(min_alloc, floor)`가 가용률 위로 올리는 경로를 막는다(`milp.py`, `greedy.py`).
- **C4** Phase 1 원시 증거(`experiments/results/phase1/`, 약 423MB) 보존: 아카이브, 해시, 재현 명령. 보관 경로는 사용자가 정한다.
- **C5** Gurobi 어댑터 계약과 mock 테스트. 평가판을 확보하면 같은 동결 입력으로 비교한다.
- **C6** (배치 규칙 확정 후 — 사용자 답변 `private/schema-intake.json`의 `parts.rules.answers.*.answer`가 입력)
  필수 기술·등급 정책 반영([A-P1] 미기재 등급 선발 포함) → 두 MILP 정식 동기화
  → Phase 0 재검증 → 300/60 실험. **목적식이나 쌍 범위 함수가 바뀌면 `core/evaluate/plan_eval.py`(Claude) 동기화를 "요청"에 적는다.**

### Claude (제품·입력 축)
- **K1** ✅ What-if 계약 정정 (2026-10-04 완료, main 병합. [A-P2] what-if 항목 해소)
- **K4** [A-P1] PDF 생성이 요청 Host를 신뢰하지 않게 한다: 고정 내부 origin, 브라우저 로컬 접근 제한, 요청 크기·동시성 한도(`api/routes/report.py`, `api/pdf.py`).
- **K5** [A-P1·P2] 설명 근거의 사실성: 리뷰 근거 자리에 숫자 대신 원문 인용이나 출처 ID를 넣고, "직접 인용"과 "요약"을 구분한다.
  테스트는 원문에 포함된 인용인지 검사하게 한다(`api/rag/**`. 공유 계약 `core/rag/sqlite_rag.py`·`core/datagen` 변경은 "요청"에 기록한다).
- **K6** [A-P2] `MemoryGraph.synergy_context_memory`의 int8 누적 넘침(128경로 이상에서 도달 노드 누락)을 수정한다(공유 계약 `core/graph/`. "요청"에 기록한다).
- **K7** ✅ 실데이터 스키마 입력 양식(`docs/data-schema/`) 제공 (2026-10-04). 사용자가 `private/schema-intake.json`을 채우는 중.
- **K2** CSV 입력 계약 v0(`core/ingest/`): 파일 묶음 → `Dataset`, 검증 리포트, 컬럼 매핑 계층, 가상 CSV, manifest.
  `private/schema-intake.json`이 오면 실제 항목에 맞춰 매핑을 정한다.
- **K3** (C0·C5·C6 이후) 검증된 솔버를 서비스에 연결한다. 인터페이스는 Codex와 합의한다.

### 미배정 (사용자 결정 필요 — 스키마 양식 D절에서 일부 답을 받음)
- [A-P2] 패키징: wheel에 fixture와 웹 산출물이 없다. 배포 단위(소스 checkout, 컨테이너, wheel)를 먼저 정해야 한다.
- [A-P1] 미기재 등급을 0명으로 볼지 자유 인원으로 볼지: 배치 규칙 결정 사항이며 C6의 입력이다.

## 진행 중
- Codex · C1 G4 예정 run `c1-g4-20261005-small-v1`: G3 체크포인트 커밋 후 원본 보존 별도 새 run, 3솔버×(oracle one_slot + baseline25/5 seed2 + budget_pressure25/5 seed2)=9cases. 케이스60초(최대240초 이하), 전체활성900초, threads1/gap0 동일. 소스·의존성·정책·입력해시 동결. 실험 동안 공유계약 변경/커밋하지 않음.
- Codex · C1 구현(2026-10-04 사용자 승인). 보고서 정정→진단/비활성 복구→G1 리뷰→연결→C0 회귀 검증 순서. 기존 `feat/phase1-solver-benchmark` worktree, main f87309c 반영. 원본 v2 보존, main 병합/push 없음.

## 요청 (상대 영역·공유 계약 변경)
- 2026-10-04 Codex → Claude · C1 G3: API 코드는 수정하지 않고 실제 `api.main.lifespan` 기본 warm-up을 skip 없이 읽기 전용 실행한다. 내부 assessment를 관측해 원본/최종 검사표를 `outputs/phase1-c1-service-smoke.json`에 기록한다. Claude 측 부팅 정책 변경은 이번 범위 밖이다.
- 2026-10-04 Codex · C1 구현 승인: Codex 소유 영역과 계획에 명시한 관련 tests를 수정하고 진행/인계 기록을 갱신한다. 목적식·쌍 함수·공유 도메인 타입·API는 유지한다. G3에서 실제 API lifespan을 읽기 전용으로 smoke 검사하되 API 변경이 필요하면 Claude에 요청한다.
- 2026-10-04 Codex · C1 문서 작업: 이 문서의 착수/완료 상태와 `docs/handoff-log.md`를 갱신한다. 구현 계획에는 관련 `tests/` 회귀 테스트를 포함한다. 현재는 설계 문서만 작성하며 공유 타입·목적식·쌍 함수·API를 변경하지 않는다. 향후 공유 타입 변경이 필요하면 구현 전에 별도 요청한다.
- 2026-10-04 Codex · C0 기록: 이 문서의 착수/완료 상태와 `docs/handoff-log.md` 완료 항목을 갱신한다. 코드 공유 계약·MILP 목적식·쌍 범위 함수의 시그니처/의미 변경은 없다. 승인된 설계에 따라 관련 `tests/` 회귀 테스트와 Codex 영역 `outputs/phase0-c0-eli5.html`을 작성한다.
- 2026-10-04 Codex → Claude · C0 통합 영향: 기본 동결 fixture의 CBC Plan A가 예산 잔차 2~2.5e-6로 독립 검증(tol=1e-6)에 거절되어 `api/main.py` warm-up이 실패한다. C1에서 수치 정책을 해결하기 전 C0를 main에 통합하면 정상 부팅이 막힌다. API warm-up 오류 처리 정책은 Claude 영역이므로 수정하지 않았으며 상대 확인을 요청한다.
