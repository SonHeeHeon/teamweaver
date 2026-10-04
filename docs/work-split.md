# Claude·Codex 작업 분담 (2026-10-04 합의)

두 에이전트가 같은 파일을 동시에 고치지 않도록 **파일 영역으로 소유권을 나눈다.**
작업을 시작하기 전에 이 문서와 `docs/project-context.md`를 읽는다.

## 소유 영역

| 영역 | 소유 | 파일 |
|---|---|---|
| 최적화 모델·솔버·실험 | **Codex** | `core/optimize/**`, `experiments/**`, `outputs/phase*`, `docs/superpowers/**` |
| 제품 API·웹·PDF·입력 수집·배치 평가 | **Claude** | `api/**`, `web/**`, `core/ingest/**`, `core/evaluate/**`, `scripts/` |
| 공유 계약 | 양쪽 (변경 전 아래 "요청"에 기록) | `core/domain/models.py`, `core/scoring/**`, `core/graph/**`, `core/datagen/**`, `core/config.py`, `fixtures/**`, `pyproject.toml`, `uv.lock`, `README.md`, `CLAUDE.md`, `AGENTS.md`, 이 문서 |

- 상대 영역은 **읽기와 import만** 한다. 수정이 필요하면 "요청"에 적고 상대가 처리한다.
- 공유 계약을 바꿀 때는 "요청"에 변경 내용과 이유를 먼저 적는다.
  상대 쪽 테스트(`uv run --group benchmark pytest -q` 전체)가 통과해야 병합한다.
- 작업 브랜치: Codex는 `.worktrees/` 아래 기능 브랜치, Claude는 `feat/claude-<slug>` 브랜치를 쓴다.
  `main` 병합은 사용자 승인으로 한다. 병합 전에 `main`을 rebase하거나 merge해 상대 작업을 반영한다.
- 장기 실험이 진행 중이면 Codex가 "진행 중" 절에 run ID를 적는다. 그동안 Claude는 공유 계약을 바꾸지 않는다.

## 작업 목록

### Codex (모델·솔버 축)
- **C1** Phase 1 원시 증거(`experiments/results/phase1/`, 약 423MB) 보존: 아카이브, 해시 목록, 재현 명령.
  보관 경로는 사용자가 정한다.
- **C2** Gurobi 어댑터 계약 설계와 mock 테스트(라이선스 없이 가능). 평가판을 확보하면 같은 동결 입력으로 비교한다.
- **C3** (배치 규칙 확정 후) 필수 기술·등급 정책 반영 → 두 MILP 정식(`core/optimize/milp.py`,
  `experiments/phase1/solvers.py::_build_model`) 동기화 → Phase 0 재검증 → 300/60 병목 실험.

### Claude (제품·입력 축)
- **K1** What-if 계약 정정: 교체 전후를 현행 MILP 전체 목적(4항)으로 재평가하고, 가용률·예산·등급 위반을 검사한다.
  API·웹·PDF 표시도 정정한다. `core/optimize`의 함수는 import로만 재사용한다.
- **K2** CSV 입력 계약 v0(`core/ingest/`): 파일 묶음을 읽어 `Dataset`으로 변환하고, 검증 리포트, 컬럼 매핑 계층,
  가상 CSV 생성, manifest를 만든다. v0에서는 `core/domain/models.py`를 바꾸지 않는다.
- **K3** (C2·C3 이후) 검증된 솔버를 서비스에 연결한다. 인터페이스는 Codex와 합의한다.

## 진행 중
- (없음)

## 요청 (상대 영역·공유 계약 변경)
- (없음)
