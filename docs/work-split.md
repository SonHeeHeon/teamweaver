# claude-a·claude-b·codex 작업 분담 (2026-10-04 합의 · 2026-10-05 3인 체제 확정)

두 에이전트가 같은 파일을 동시에 고치지 않도록 **파일 영역으로 소유권을 나눈다.**
작업을 시작하기 전에 이 문서, `docs/handoff-log.md`(서로 한 일), `docs/project-context.md`(맥락)를 읽는다.

## 에이전트와 작업 위치 (2026-10-05 사용자 확정)

| ID | 도구 | 작업 폴더 | 새 브랜치 이름 |
|---|---|---|---|
| `claude-a` | Claude Code 계정 A (`claude-a`) | 저장소 루트 체크아웃 | `feat/claude-a-<slug>` |
| `claude-b` | Claude Code 계정 B (`claude-b`) | `.worktrees/claude-b-<slug>` | `feat/claude-b-<slug>` |
| `codex` | Codex | `.worktrees/<slug>` (기존 관례) | `feat/<slug>` 또는 `feat/codex-<slug>` |

- Claude는 `echo $CLAUDE_AGENT_ID`로 자기 ID를 확인한다. 비어 있으면 편집 전에 사용자에게 묻는다.
- 기존 브랜치(`feat/claude-*`, `feat/phase*`)는 이름을 바꾸지 않고 그대로 쓴다.
- 한 작업 폴더에는 한 에이전트만 들어간다. 다른 에이전트의 브랜치·worktree 파일은 고치지 않고,
  그 커밋을 `reset`·`rebase`·`force push`로 건드리지 않는다.
- 기준 브랜치는 `main`이다.
<!-- base-branch: main -->
- worktree 만들기(맥에서 실행): `~/Dev/_claude-shared/new-worktree.sh ~/Dev/teamweaver <ID> <slug>`
  스크립트는 gitignore된 `.env`와 untracked `.claude/settings.local.json`도 루트에서 링크한다.
  직접 만들 때는 `git worktree add .worktrees/<ID>-<slug> -b feat/<ID>-<slug> main`.
- worktree 준비: `uv sync`(벤치가 필요하면 `--group benchmark`), 웹 작업이면 `cd web && npm ci`.
  `private/`와 `.omc/`는 gitignore라 루트에만 있다. 읽어야 하면 루트 경로를 직접 읽고, 복사하거나 커밋하지 않는다.
- 개발 서버 기본 포트는 API `:8000`, 웹 `:5173`이다. 사용 중이면(`lsof -i :8000`) 다른 포트를 쓰고 "진행 중"에 적는다.
- 사용자가 세션에서 영역 밖 작업을 명시적으로 지시하면 허용한다. 그때는 "요청"이나 handoff-log에 남긴다.

## 소유 영역

| 영역 | 소유 | 파일 |
|---|---|---|
| 최적화 모델·솔버·실험 | **Codex** | `core/optimize/**`, `experiments/**`, `outputs/phase*`, `docs/superpowers/**` |
| 제품 표면: API·웹·PDF·스크립트 | **claude-b** | `api/**`(단 `api/rag/**` 제외), `web/**`, `scripts/**` (`scripts/extract_intake_images.py`는 분담 전 claude-a가 작성) |
| 입력·평가·설명 근거: CSV 입력·배치 평가·RAG 브리핑 | **claude-a** | `core/ingest/**`, `core/evaluate/**`, `api/rag/**` |
| 공유 계약 | 양쪽 (변경 전 아래 "요청"에 기록) | `core/domain/models.py`, `core/scoring/**`, `core/graph/**`, `core/rag/**`, `core/datagen/**`, `core/config.py`, `fixtures/**`, `pyproject.toml`, `uv.lock`, `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/project-context.md`, `docs/handoff-log.md`, `docs/data-schema/**`, 이 문서 |
| 사용자 비공개 입력 | 사용자 (에이전트는 읽기만) | `private/**`(gitignore). 실데이터 스키마 답변 `private/schema-intake.json`. 커밋·push 금지 |

- 다른 에이전트의 영역은 **읽기와 import만** 한다. 수정이 필요하면 "요청"에 적고 그 영역 소유자가 처리한다.
- 공유 계약을 바꿀 때는 "요청"에 변경 내용과 이유를 먼저 적는다.
  상대 쪽 테스트(`uv run --group benchmark pytest -q` 전체)가 통과해야 병합한다.
- 작업 브랜치와 작업 폴더는 위 "에이전트와 작업 위치" 표를 따른다.
  `main` 병합은 사용자 승인으로 한다. **작업 시작 전과 병합 전에 `main`을 merge하거나 rebase해** 상대 작업을 반영한다.
- 장기 실험이 진행 중이면 Codex가 "진행 중" 절에 run ID를 적는다. 그동안 Claude는 공유 계약을 바꾸지 않는다.
- task를 끝내면 `docs/handoff-log.md` 맨 위에 항목을 추가한다. 착수하면 "진행 중"에 적고, 끝나면 지운다.

## 작업 목록
`[A-P0]` 같은 표시는 Codex 중간 품질 감사(`outputs/eli5-mid-project-quality-audit.html`, `bf50a7e`)의 심각도다.
순서는 제안이며 사용자가 바꿀 수 있다.

### Codex (모델·솔버 축) — 제안 순서
- **C0** [A-P0] 모든 솔버 반환 경로가 독립 검증기를 통과하게 강제한다. 분수·무incumbent 해는 반환하지 않는다.
  (`core/optimize/milp.py`; 0초·초단기 time limit 재현 테스트)
- **C1** [A-P0] Phase 1 보고서가 `payload.error`의 거절 87건(CBC 예산 85, CBC 이진 1, HiGHS 예산 1)을 집계하고 분류하게 한다.
  예산 허용오차·단위·반올림을 분석한다. 분모를 핵심 108·파일럿 3·호환성 1로 분리한다([A-P2]).
- **C2** [A-P1] 대안 생성: 구성 서명으로 중복을 제거하고, 빈 계획을 제외하고, 최소 품질·미충원 한도를 두고, 부족하면 "대안 없음"을 반환한다(`core/optimize/alternatives.py`).
- **C3** [A-P2] 표시 투입률 반올림: `max(min_alloc, floor)`가 가용률 위로 올리는 경로를 막는다(`milp.py`, `greedy.py`).
- **C4** Phase 1 원시 증거(`experiments/results/phase1/`, 약 423MB) 보존: 아카이브, 해시, 재현 명령. 보관 경로는 사용자가 정한다.
- **C5** Gurobi 어댑터 계약과 mock 테스트. 평가판을 확보하면 같은 동결 입력으로 비교한다.
- **C6** (배치 규칙 확정 후 — 사용자 답변 `private/schema-intake.json`의 `parts.rules.answers.*.answer`가 입력)
  필수 기술·등급 정책 반영([A-P1] 미기재 등급 선발 포함) → 두 MILP 정식 동기화
  → Phase 0 재검증 → 300/60 실험. **목적식이나 쌍 범위 함수가 바뀌면 `core/evaluate/plan_eval.py`(Claude) 동기화를 "요청"에 적는다.**

### Claude (K*) — 담당 ID를 줄마다 표시했다
- **K1** ✅ What-if 계약 정정 (2026-10-04 완료, main 병합. [A-P2] what-if 항목 해소)
- **K4** ✅ (claude-b, 2026-10-05 `feat/claude-b-pdf-origin`, main 병합은 사용자 승인 대기) [A-P1] PDF 생성이 요청 Host를 신뢰하지 않게 한다: 고정 내부 origin, 브라우저 로컬 접근 제한, 요청 크기·동시성 한도(`api/routes/report.py`, `api/pdf.py`).
- **K5** (claude-a, 2026-10-05 claude-a 쪽 완료) 설명 근거의 사실성: 출처 ID·직접 인용/요약/라벨 구분, LLM 인용 검증(실패 시 규칙 기반 전환), 실데이터 원문 비공개. 연결·표시는 claude-b(위 "요청").
- **K6** ✅ (claude-a, 2026-10-05) `MemoryGraph.synergy_context_memory`의 int8 누적 넘침 수정(int32). 공유 계약 `core/graph/` 변경은 아래 "요청"에 기록.
- **K7** ✅ 실데이터 스키마 입력 양식(`docs/data-schema/`) 제공 (2026-10-04). 사용자가 `private/schema-intake.json`을 채우는 중.
- **K2** ✅ (claude-a, 2026-10-05) CSV 입력 계약 v0: `core/ingest/`(계약·읽기·검증 리포트·변환·가상 묶음 생성, `python -m core.ingest generate|check`). 숙련도는 경력 개월 → 대리 레벨, 리뷰는 최신 회차만(아래 "요청" 참고).
- **K8** ✅ (claude-b, 2026-10-05 `feat/claude-b-milp-settings`, main 병합은 사용자 승인 대기) 관리자 배치 설정 화면: 최소 투입률(실데이터 답변상 30%, 관리자가 바꿀 수 있어야 함) 등 MILP 파라미터를
  웹 설정 화면에서 바꿔 `/api/optimize`의 `milp_params`로 보낸다. 기본값 변경과 동시 프로젝트 수 제한 같은 새 제약은
  모델 변경이라 Codex(C6) 소관이다. 화면은 그 결과를 따라간다.
- **K9** ✅ (claude-b, 2026-10-05 `feat/claude-b-dataset-upload`, K8 브랜치 위, main 병합은 사용자 승인 대기) CSV 묶음(zip) 업로드 → `core.ingest` 검증 → 활성 데이터셋 전환, 캐시 키에 데이터셋 버전, 화면 "데이터" 탭.
- **K10** ✅ (claude-b, 2026-10-05 `feat/claude-b-swap-apply`, K9 위, main 병합은 사용자 승인 대기) 교체 "검토 → 적용" 흐름: 적용한 교체로 명단을 바꾸고 이력·위반을 남기며, PDF가 적용 명단과 교체 목록을 보여 준다.
- **K3** (claude-a·claude-b·codex 합의, C0·C5·C6 이후) 검증된 솔버를 서비스에 연결한다. 인터페이스는 Codex와 합의한다.

### 미배정 (사용자 결정 필요 — 스키마 양식 D절에서 일부 답을 받음)
- [A-P2] 패키징: wheel에 fixture와 웹 산출물이 없다. 배포 단위(소스 checkout, 컨테이너, wheel)를 먼저 정해야 한다.
- [A-P1] 미기재 등급을 0명으로 볼지 자유 인원으로 볼지: 배치 규칙 결정 사항이며 C6의 입력이다.

## 진행 중
형식: `- [ID] <브랜치> · <건드릴 경로> · <시작 YYYY-MM-DD HH:MM> · <포트·run ID 같은 공유 자원>`
- (없음)

## 요청 (다른 에이전트 영역·공유 계약 변경)
형식: `- YYYY-MM-DD [요청자→대상] <내용과 이유> · 상태: 대기|처리됨`
- 2026-10-05 [claude-a→claude-b] K5 연결(설계 `.omc/plan/2026-10-05-k5-evidence-provenance.md` 5절, claude-a 쪽은 `feat/claude-a-k5-evidence`에 완료):
  (1) `api/datasets.py::build_active` — `ActiveDataset`에 `evidence = build_evidence_index(ds, parsed, reveal_text=(synthetic is True))`(`api.rag.evidence`). 실데이터(synthetic이 true가 아님)는 원문을 색인에도 두지 않는다(사용자 결정).
  (2) `api/routes/whatif.py` — **같은 색인을 두 곳에**: `swap_context(..., evidence=dataset.evidence)`와 `generate_briefing(..., evidence=dataset.evidence)`. 앞의 것만 넘기면 `generate_briefing`이 ValueError(code `missing_index`)로 규칙 기반 전환한다.
  (3) `api/schemas.py` — `EvidenceOut{source_id, reviewer_id, kind: quote|summary|label, text}`, `BriefingOut.evidence: list[EvidenceOut] = []`(선택 칸, 기존 클라이언트 호환). 두 브리핑 함수는 이미 `evidence` 키를 낸다.
  (4) 화면·PDF(`BriefingPanel`, `ReportPage`) — quote는 "직접 인용"+따옴표, summary는 "요약", label은 "원문 비공개(실데이터)"+항목 라벨, 출처 ID는 작게. 본문 `[rv:…]`는 "관련 출처"이지 문장 전체가 검증됐다는 뜻이 아님을 안내 · 상태: 대기
- 2026-10-05 [claude-a→codex] 참고: fixture의 LLM 파싱 근거 532개 중 6개가 원문과 다르다("줄였습니다"→"줄었습니다" 등). K5 색인은 이를 "요약"으로 표시한다. `sqlite_rag.team_cohesion`은 행 단위 AVG라 협업 점수(방향 안 평균→두 방향 평균)와 가중이 다르다 · 상태: 정보
- 2026-10-05 [claude-a→codex·공유] K6: `core/graph/memory_graph.py::synergy_context_memory`의 frontier 곱을 int8→int32로 바꿨다. frontier에 있는 이웃이 128개 이상인 노드가 도달 집합에서 빠지던 버그다. 결과 의미는 SQL·Cypher 질의와 같아지는 쪽으로만 바뀐다. 실험 1(`experiments/bench/exp1_storage.py`)의 예전 결과는 "K6 이전 측정"으로 봐 달라(시간 차이는 미미) · 상태: 처리됨
- 2026-10-05 [claude-a→codex·공유] 경력 개월을 직접 쓰는 기술 점수: 사용자 결정(숙련도 레벨 없음, 기술별 경력 연수만)에 맞춰 S를 `min(보유 경력개월/요구 경력개월, 1)`로 바꾸고 `Person.skills`·`SkillRequirement`를 개월 단위로 확장하자. 모델(`core/domain`)·점수(`core/scoring`)·datagen·두 MILP 입력·Phase 0 재검증이 함께 움직이므로 Codex C6와 묶어 합의 필요. 그 전까지 `core/ingest`는 개월→1~5 대리 레벨(경계 12/36/60/96)을 쓴다 · 상태: 대기
- 2026-10-05 [claude-a→공유] 리뷰 회차: 사용자 요청("예전 회차도 쓸 수 있게")으로 claude-a가 `core/graph/memory_graph.py`를 고쳤다. 같은 평가자→피평가자의 회차들을 먼저 평균하고, 그 두 방향을 평균한다(자주 쓰는 쪽이 쌍 점수를 좌우하지 않게). 리뷰 목록과 parsed의 순서·길이가 다르면 경고 로그를 남기고 기존(방향당 마지막 1건) 방식을 쓴다. 방향당 1건인 기존 fixture·datagen·Phase 1 시나리오는 결과가 비트 단위로 같다(테스트로 고정). SQLite review 표에 회차 칸이 없어 `rehydrate.from_sqlite`와 LLM checkpoint(`core/datagen/llm_checkpoint.py`)는 여러 회차 데이터를 **명시적 오류로 거부**한다. 회차 칸 추가(스키마 변경)가 필요하면 Codex와 합의한다 · 상태: 처리됨(회차 칸 추가는 대기)
- 2026-10-05 [claude-a→codex] 참고: `python -m core.ingest generate --people 40 --projects 8 --seed 7`로 만든 가상 묶음에서 CBC 원시 해가 예산을 ~1.5e-6 넘어 `validate_raw_solution`이 거절했다(컨설팅 단가처럼 끝자리가 둥글지 않을 때). C1 허용오차 분석의 재현 사례로 쓸 수 있다 · 상태: 정보
- 2026-10-05 [claude(채팅 세션)→모두] 3인 체제 문서화: 이 문서에 "에이전트와 작업 위치"를 추가하고 Claude 영역을 claude-a·claude-b로 나눴다. `CLAUDE.md`·`AGENTS.md`의 "두 에이전트" 문구도 고쳤다. 코드 변경 없음 · 상태: 처리됨(2026-10-05 사용자 확정, claude-a가 커밋)
