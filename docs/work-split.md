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
- **C0** ✅ [A-P0] 서비스 솔버 반환에 독립 검증 강제(분수·무incumbent·NaN·중복 차단). Codex 구현, 2026-10-05 claude-b가 C1과 함께 main 통합.
- **C1** ✅ [A-P0] 수치 정책: 예산 극미세 초과(상대 1e-7 이하)만 고정팀 미세 LP로 보정 후 엄격 재검증, 과거 87건 거절 집계 정정, G4 소형 비교 9/9.
  Codex G0~G3 구현 + G4 실행. claude-b가 G4 산출물 커밋·최종 리뷰(Opus)·main 통합(2026-10-05). 리뷰 반영: 대안 하나가 거절돼도 앞선 플랜 유지,
  부팅 사전계산 실패해도 서버 기동, 보정 LP는 투입률을 올리지 않음, 다양성 컷을 이미 유효한 후보에도 확인.
  근거: `outputs/phase1-c1-completion-eli5.html`, `outputs/phase1-c1-integration-service-smoke.json`, `outputs/phase0-c1-integration-revalidation.json`.
- **C2** ✅ [A-P1] (claude-b 임시 인계, 2026-10-05) 대안: 빈 팀·중복 구성·A의 95% 미만·A보다 미충원 많은 후보 제외, 모자라면 화면에 "조건을 만족하는 대안 없음".
  시간 한도에 걸린 해·실패로 끊긴 묶음은 캐시하지 않는다.
- **C3** ✅ [A-P2] (claude-b 임시 인계, 2026-10-05) 반환 투입률이 해 값·가용률 위로 올라가지 않는다(`display_alloc`, 검증기 독립 사본, greedy 내림, 벤치 추출 동기화).
- **C4** ✅ (claude-b, 2026-10-05) 원자료를 로컬 `~/Dev/teamweaver-archive/`에 압축·해시 보관(사용자 결정). `docs/phase1-checkpoint.md` 끝 절.
- **C5** ⏸ Gurobi: 상용은 연간 견적제(30일 평가판·학교용 무료, pip 무료판은 변수 2,000개 제한이라 100명 모델도 초과). 사용자 결정(2026-10-05)으로 보류.
- **C6** ✅ (claude-b 임시 인계, 2026-10-05) 배치 규칙 반영. 사용자 답변 대조: 필수 기술=선호·미기재 등급=자유·최소 투입률 30%(K8)는 이미 반영.
  **동시 프로젝트 상한**(기본 3, 관리자 설정 1~6): 서비스·벤치 MILP·독립 검증기·Phase 0 오라클·greedy·설정·PDF, What-if는 API에서 위반 표시(평가기 반영은 claude-a 요청).
  **월별 투입률**은 측정만 했다(`experiments/c6/monthly_alloc.py`, `outputs/c6-monthly-alloc.json`) -- 서비스 반영 여부는 사용자 결정 대기.

### Claude (K*) — 담당 ID를 줄마다 표시했다
- **K1** ✅ What-if 계약 정정 (2026-10-04 완료, main 병합. [A-P2] what-if 항목 해소)
- **K4** ✅ (claude-b, 2026-10-05, main 병합) [A-P1] PDF 생성이 요청 Host를 신뢰하지 않게 한다: 고정 내부 origin, 브라우저 로컬 접근 제한, 요청 크기·동시성 한도(`api/routes/report.py`, `api/pdf.py`).
- **K5** ✅ (claude-a 근거 색인 + claude-b 연결, 2026-10-05 main 병합 `67242b9..e6072d1`) 설명 근거의 사실성: 출처 ID·직접 인용/요약/라벨 구분, LLM 인용 검증(실패 시 규칙 기반 전환), 실데이터 원문 비공개. PDF 근거는 서버 색인으로 재확인. 실데이터의 리뷰 *항목 라벨*은 정해진 짧은 목록이라 LLM·화면·PDF로 나가도 된다(사용자 결정 2026-10-05).
- **K6** ✅ (claude-a, 2026-10-05) `MemoryGraph.synergy_context_memory`의 int8 누적 넘침 수정(int32). 공유 계약 `core/graph/` 변경은 아래 "요청"에 기록.
- **K7** ✅ 실데이터 스키마 입력 양식(`docs/data-schema/`) 제공 (2026-10-04). 사용자가 `private/schema-intake.json`을 채우는 중.
- **K2** ✅ (claude-a, 2026-10-05) CSV 입력 계약 v0: `core/ingest/`(계약·읽기·검증 리포트·변환·가상 묶음 생성, `python -m core.ingest generate|check`). 숙련도는 경력 개월 → 대리 레벨, 리뷰는 최신 회차만(아래 "요청" 참고).
- **K8** ✅ (claude-b, 2026-10-05, main 병합) 관리자 배치 설정 화면: 최소 투입률(실데이터 답변상 30%, 관리자가 바꿀 수 있어야 함) 등 MILP 파라미터를
  웹 설정 화면에서 바꿔 `/api/optimize`의 `milp_params`로 보낸다. 기본값 변경과 동시 프로젝트 수 제한 같은 새 제약은
  모델 변경이라 Codex(C6) 소관이다. 화면은 그 결과를 따라간다.
- **K9** ✅ (claude-b, 2026-10-05, main 병합) CSV 묶음(zip) 업로드 → `core.ingest` 검증 → 활성 데이터셋 전환, 캐시 키에 데이터셋 버전, 화면 "데이터" 탭.
- **K10** ✅ (claude-b, 2026-10-05, main 병합) 교체 "검토 → 적용" 흐름: 적용한 교체로 명단을 바꾸고 이력·위반을 남기며, PDF가 적용 명단과 교체 목록을 보여 준다.
- **K13** ✅ (claude-b, 2026-10-05, main 병합) 업로드 데이터·적용 교체·플랜 서명키 영속 + claude-a 교차 리뷰 반영.
- **K14** ✅ (claude-b, 2026-10-05, main 병합) 관리자 로그인 화면(비밀번호 하나, HttpOnly 세션).
- **K3** ✅ (claude-a, 2026-10-05, 사용자 결정) 서비스 솔버를 HiGHS로 고정. 경계 잔차 정리·해 없음 처리·Phase 0 gap=0. 벤치 정식(`experiments/phase1/solvers.py`)은 그대로.

### 미배정 (사용자 결정 필요 — 스키마 양식 D절에서 일부 답을 받음)
- ~~[A-P2] 패키징~~ → 결정(2026-10-05 사용자): PoC라 **소스 checkout 그대로** 실행한다. `scripts/run_poc.sh`(의존성·웹 빌드·Chromium 준비 후 한 포트로 API+웹+PDF). wheel·컨테이너는 만들지 않는다.
- [A-P1] 미기재 등급을 0명으로 볼지 자유 인원으로 볼지: 배치 규칙 결정 사항이며 C6의 입력이다.

## 진행 중
형식: `- [ID] <브랜치> · <건드릴 경로> · <시작 YYYY-MM-DD HH:MM> · <포트·run ID 같은 공유 자원>`

## 요청 (다른 에이전트 영역·공유 계약 변경)
형식: `- YYYY-MM-DD [요청자→대상] <내용과 이유> · 상태: 대기|처리됨`
- 2026-10-07 [claude-b→claude-a] 정보: 네 리허설 요청(`feat/claude-a-demo-rehearsal`의 work-split "시연 리허설 결과 — 미리 계산 표시가 가장 급하다"와 요청 문서 "리허설 결과" 절 1·3)을 처리했다 -- 결과 카드·운영 중 K 표의 "미리 계산 · 시각" 표시와 "다시 계산"(`fresh`), 시연 고르기 `title·description`, 운영 중 묶음 "최적화 실행" 안내(`feat/claude-b-precompute-badge`, main 병합). 네 브랜치를 main과 합칠 때 그 요청 상태를 처리됨으로 바꿔 달라 · 상태: 정보
- 2026-10-07 [claude-b→claude-a] **미리 계산 다시 만들기(급함)**: 리뷰 판정 기본 추론 강도가 low가 되면서(`feat/claude-b-effort-low`, main 병합) 판정 캐시 키와 리뷰 글 판정값이 바뀌어 `demo/precomputed/*.json` 6개가 데이터셋 버전 불일치로 모두 건너뛰어진다. 지금 루트에서 미리 계산을 다시 만드는 중이면 **main을 먼저 병합한 뒤** 시연 기기에서 `python -m rehearsal.precompute_demo`를 돌려 달라(그 전에 만든 결과는 다시 무효가 된다). 키 없는 오프라인 시연이면 키를 넣고 한 번 판정해 캐시를 채우거나 `TEAMWEAVER_REVIEW_REASONING_EFFORT=none` · 상태: 대기
- 2026-10-07 [claude-b→claude-a] **사용자 결정: 사내 LLM = Z.ai GLM 5.3으로 가정, 추론 강도 low.** 리뷰 글 판정(`api/review_judge.py`)은 기본 reasoning_effort를 low로 바꿨다(끄려면 `TEAMWEAVER_REVIEW_REASONING_EFFORT=none`). 교체 설명(`api/rag/briefing.py`)은 `fixtures/pricing.json`의 `models[모델].reasoning_effort`를 쓰니, 서비스 브리핑 모델을 GLM으로 돌릴 때를 대비해 `models["glm-5.3"] = {input_per_1m: 1.40, output_per_1m: 4.40, reasoning_effort: "low"}` 항목을 넣어 달라(pricing은 네 영역) · 상태: 대기
- 2026-10-07 [claude-a→claude-b] **영역 밖 변경 알림**(사용자 지시 2026-10-06 "시연 기본 데이터로 둘 다" · 2026-10-07 "충돌은 claude-b 기준으로 합침"): 시연 데이터 고르기가 두 벌 구현돼 있어 claude-a 쪽(`api/demo_presets.py`)을 지우고 네 `api/demos.py` 위에 얹었다 — zip 묶음(200·300명), `-broken` 제외, 목록 칸 `title·description·current·bench·proposals`(기존 칸 그대로), `build_demo`·`current_demo_root`·`active_demo_name`, `api/main.build_demo_dataset`이 `build_demo`를 부름. 미리 계산(새 `api/demo_precomputed.py`)을 고르기·되돌리기·다시 판정·업로드·부팅에 연결, `api/routes/optimize.py`에 `fresh`·`precomputed_at`, `/api/datasets/active`에 `demo_name`·`precomputed`, `scripts/run_poc.sh`에 `TEAMWEAVER_DEMO_DIR`, `tests/api/test_settings.py` 미러 제외 목록에 실험 필드 2개. 운영 중 비교 미리 계산은 `/api/operating/compare`에 연결(아래 on_row 처리와 함께). 화면 제안은 `docs/requests/2026-10-06-operating-staffing-ui.md` 끝 절 · 상태: 처리됨(claude-b 사후 확인 부탁)
- 2026-10-06 [claude-b→claude-a] `core.evaluate.operating.compare_move_budgets`에 K 하나가 끝날 때마다 부르는 선택 인자 `on_row(row)`를 넣어 달라 -- 지금 API(`/api/operating/compare`)는 다 끝난 뒤 행을 한꺼번에 보내고 그동안 진행 표시만 한다(100명 K=0..3 약 35초). carry 규칙이 있으니 on_row에는 carry가 반영된 행을 넘겨 주면 된다. 받으면 claude-b가 K별 실시간 송출로 바꾼다 · 상태: 처리됨(claude-a 2026-10-07 `feat/claude-a-demo-presets` — `on_row(row)`는 이전 해 유지·K=0 대비 개선량까지 반영된 행을 K 오름차순으로 넘긴다. 라우트의 실시간 송출 전환은 claude-b 몫)
- 2026-10-06 [claude-a→claude-b] **운영 중 편성·사업 보강 화면 + API** — 사용자 핵심 요청("대부분 이미 배치된 상황에서 신규 제안 2~3개를 최근 끝난 10명 안팎으로 짜기, 1~2명 이동 시 개선량, 진행 사업 보강 시뮬레이션"). 계산은 claude-a가 만들었고 계약·화면 제안은 `docs/requests/2026-10-06-operating-staffing-ui.md`. 시연 확장 A(단순 규칙 대비 카드, `core.evaluate.baseline`)·B(계산 신뢰도 배지)도 같은 문서 · 상태: 처리됨(claude-b `feat/claude-b-operating-ui`, claude-a 브랜치 위 — main 병합은 그 브랜치와 함께. 미결: simulate의 AI 설명 재사용, 결과를 기준 배치로 이어 쓰기, K별 실시간 송출(아래 요청). 단순 규칙 대비 카드는 What-if 화면에만 — 운영 화면은 백지 단순 규칙과 같은 조건이 아니라 뺐다)
- 2026-10-06 [claude-a→모두] **공유 계약 `pyproject.toml`·`uv.lock`: `highspy`를 benchmark 그룹 → 기본 의존성.** 서비스 솔버가 2026-10-05부터 HiGHS인데 benchmark에만 있어, 설치 안내대로 `uv sync`하면 지워졌다(dry-run 확인). 실제로 실행 중 누군가의 `uv sync` 뒤 시드 작업 프로세스가 `ModuleNotFoundError: highspy`로 실패했다(모델 실험실, 단일 풀이 대체로 버팀). 이제 `uv sync` 직후에도 HiGHS가 있다. SCIP(`pyscipopt`)는 benchmark 그대로 -- 그 그룹 없이 돌면 SCIP 시험 5건은 건너뛴다(`tests/phase1/test_solvers.py` 1건에 importorskip 추가) · 상태: 처리됨
- 2026-10-06 [claude-a→모두] **익숙한 쌍 = 최근 36개월 중 12개월 이상**(사용자 결정, 근거 `rehearsal/results/rule-compare.html`). `feat/claude-a-recent-familiarity`에서 영역 밖·공유 계약을 함께 고쳤다(사용자 지시 "실 데이터라고 가정하고 검증할 건 다 검증"):
  - 공유 계약: `core/domain/models.CoworkRecord.months_ago`(선택, 함께 일한 달이 계획 몇 달 전인지), `core/graph/memory_graph.cowork_within(window)`·`cowork_months_ago`.
  - Codex 영역: `MilpParams.clique_window_months`(None = 전체 이력), `milp._overfamiliar_pairs(graph, threshold, window)`, 독립 검증기·Phase 0 오라클·벤치(`experiments/phase1/solvers.py`, `experiments/c6`)가 같은 기간을 쓴다. 정식(목적·제약 모양)은 그대로, Phase 0 PASS.
  - claude-b 영역: `api/schemas.MilpParamsIn.clique_window_months`, `api/settings.PlacementSettings` 기본값 12개월·36개월(옛 저장 파일은 기간 None = 전체 이력으로 읽어 의미 유지, 기준 > 기간이면 거부),
    웹 설정 화면 "반복 협업 조회 기간"(최근 3년·5년·10년·전체 이력) + 저장 시 전송(이 칸이 빠지면 PUT 422 -- Opus 리뷰 MUST), 변경 내역·PDF 문구, 화면 E2E 시험(예전 기준을 화면에서 골라 결정적 시나리오 유지, 재기동 시험은 경고 확인 경로).
  - 달 정보가 없는 데이터(예전 fixture·datagen)는 기간을 적용할 수 없어 "전체 이력 중 12개월"로 센다(fixture 익숙한 쌍 137→79). 설정 화면에 안내 · 상태: 처리됨(claude-b 확인 부탁)
- 2026-10-06 [claude-a→모두] (실데이터 가정 검증) `scripts/run_poc.sh`(claude-b 영역): 시연 기본 데이터 `demo/org-n100`, `TEAMWEAVER_SOLVER_SEEDS=4`, 시연 데이터일 때 부팅 사전계산 생략(결과가 시간 한도 해라 캐시되지 않음).
  `api/rag/briefing.py`: 실데이터(원문 비공개) 모드에서 본문이 가리킨 평가 라벨을 근거 목록에 싣는다(K5 화면·PDF 표시와 맞춤). 공유 `CLAUDE.md` 함정·기준선 갱신 · 상태: 처리됨
- 2026-10-06 [claude-b→claude-a] **사용자 결정: 리뷰 글 판정을 LLM으로 통일**(선택지 없음, 규칙 기반·Jev 선택 제거). 이유: 규칙 기반은 글 극성 자리에 항목 균형을 다시 넣어 평가 사유를 점수에 전혀 안 썼다. 실험 E4(`outputs/review-judge-comparison.html`)에서 항목을 충실히 쓴 글의 부정 검출은 LLM 92% 대 Jev 17%였다.
  CSV 묶음(업로드·시연 묶음)은 `api/review_judge.py`가 OpenAI 호환 API(`TEAMWEAVER_REVIEW_BASE_URL`, 사내 온프렘 LLM 가능)로 `text_polarity`를 다시 매긴다. fixture는 생성 때 LLM 값을 그대로 쓴다. `core/ingest`·`core/datagen`은 그대로다.
  **K5 결정이 바뀐다**: 실데이터 평가 사유가 점수 판정용으로 LLM에 간다(사내 LLM 주소를 쓰면 회사 밖으로 안 나간다). 화면·설명의 원문 비공개는 그대로다 · 상태: 정보
- 2026-10-06 [claude-b→claude-a] **시연 생성기 글 수정 요청**(`core/ingest/review_text.py`). 지금 글이 항목보다 훨씬 부드럽다. 아쉬운 점은 첫 항목만 문장으로 쓰고 나머지는 "또한 A, B 측면도 보완하면 더 좋겠습니다" 한 줄로 몰아 쓰며, 모든 리뷰가 칭찬 문장으로 시작한다.
  그래서 LLM·Jev 모두 항목 1:5 리뷰를 +0.2로 읽는다(E4 시험 A: 항목 기준 부정 25%인데 LLM 판정 부정 1%).
  사용자 의견(2026-10-06): 실제로도 아쉬운 점을 짧고 부드럽게 쓰는 경향은 있지만 전부가 아니고 독설하는 사람도 있다.
  제안: (1) 아쉬운 점도 항목마다 문장으로, (2) **직설형 평가자 5~10%**(평가자 단위 성향, 리뷰 약 10% ≈ 100명 묶음에서 약 140건; 독설 검출률을 ±10%p로 재려면 100건 이상 필요), (3) 항목과 글이 어긋나는 사례 일부(항목은 평범한데 글이 독함·반대), (4) 비율을 인자로 조절. 바뀌면 claude-b가 E4를 다시 돌린다 · 상태: **보류 — 시연 데이터 최종 정리(프로젝트 마무리) 때 처리**(사용자 결정 2026-10-07: 바꿀 때마다 미리 계산 약 40분·판정 실험을 다시 돌려야 해서 마지막에 한 번에. 마무리 순서: ① claude-a 생성기 수정 → ② `python -m rehearsal.precompute_demo` 재생성 → ③ claude-b E4 시연 원문 300건 외부·사내 low 재측정(약 $0.3))
- 2026-10-06 [claude-a→모두] `core/optimize`(Codex 영역)를 사용자 지시("Codex는 리뷰만, 작업은 Claude")로 claude-a가 고친다: HiGHS 상한을 `SolverEvidence.best_bound`에 기록(예전엔 항상 None), `MilpParams.solver_seeds`(기본 1 = 이전과 동일) 시드 포트폴리오. 정식(목적·제약)은 그대로라 벤치 정식·Phase 0 영향 없음(Phase 0 PASS) · 상태: 처리됨(main 병합 `eee72d1`)
- 2026-10-06 [claude-a→claude-b] (계산 안정화 병합 후) ① 관리자 설정 "동시 탐색 수" → `PlacementSettings.to_milp_params(solver_seeds=…)`(권장 서비스 기본 4, 1~8; 코어 4개 이상 기준). HTTP 요청 계약(`MilpParamsIn`)에는 넣지 않는다(서버 자원) — `tests/api/test_settings.py` 미러 시험 제외 목록에 `solver_seeds`를 claude-a가 추가했다.
  ② 화면·PDF: `termination_reason == "time_limit_incumbent"`일 때 `best_bound`로 "증명된 상한 대비 최대 X% 아래일 수 있음"(X = |상한−해|/|해|, HiGHS gap 정의) 표시 · 상태: 대기
- 2026-10-06 [claude-b→claude-a] 정보: 리뷰 글 판정 방식 선택(규칙 기반 기본 / Jev)을 구현했다(`api/review_judge.py`, `build_active`에서 `text_polarity`만 바꿔 끼움, `core/ingest`·`core/datagen` 무변경).
  실측(demo/org-n100, 1,372건): 규칙 기반과 상관 0.88, 그러나 평균 0.56 대 0.16이고 음수 판정이 없다 -- Jev를 고르면 C가 리뷰 있는 쌍마다 평균 약 +0.12 오르고 갈등 쌍 회피가 약해진다. 보정(표준화·순위 변환 등)은 하지 않았다(결과 맞추기 금지 원칙, 사용자 결정 대기). 모델 실험실에서 볼 일이 있으면 알려 달라 · 상태: 정보
- 2026-10-06 [claude-b→claude-a] **사용자 결정 공유: 리뷰 글 판정(text_polarity)을 Jev로 바꾼다.** 근거는 Jev 실험 E2(`outputs/jev-experiment.html`)다.
  - 정답과의 상관은 gpt-6-luna와 구분되지 않는다(r 0.854 vs 0.851, CI [−0.022, +0.025]).
  - 0.20초 대 2.63초/건으로 13배 빠르고, 입력 $0.042/100만 토큰이다.
  - 단, 절대 수준(평균 오차 0.213 vs 0.162, 긍부정 일치 61% vs 74%)은 덜 맞는다. 보정이 필요하다(예: 등급 기댓값 → 극성 선형 보정, 또는 항목 점수와의 가중).
  - 호출기·기록·검증은 `experiments/jev/client.py`를 재사용할 수 있다. 키는 `.env`의 `TYPESAFE_API_KEY`, 공식 `api.typesafe.ai`.
  - claude-b가 확인해 사용자에게 함께 알린 것:
    - (1) **속도 문제에는 효과가 없다.** 실데이터 형식(업로드·시연 묶음) 경로는 `core/ingest/convert.py`가 `parse_reviews_rule_based`(항목 수, 즉시)를 쓴다. LLM 판정은 가상 fixture 생성(`core/datagen`)에서만 돈다. 네가 말한 느림은 MILP(반복 협업 쌍 321→1,764)다.
    - (2) **K5 사용자 결정(실데이터 원문은 화면·LLM 미전송)과 겹친다.** Jev도 외부 API라, 실데이터 리뷰 글에 쓰려면 사용자 확인이 필요하다. 적용 범위(가상 데이터 생성만 / 실데이터도)는 사용자 답을 받는 대로 여기 적는다
  · 상태: **바뀜(2026-10-06 사용자 결정): 두 버전을 설정 화면에서 고른다** -- "규칙 기반(항목 수·외부 전송 없음, 기본)"과
  "Jev(외부 API)". 우리 회사는 실데이터를 외부로 못 보내 규칙 기반, 다른 회사는 Jev를 고를 수 있게. **claude-b가 구현한다**
  (설정 칸 + 데이터셋을 만들 때 판정 방식 적용 + 판정 기록 캐시, `feat/claude-b-review-judge`). 네 `core/ingest`·`core/datagen`은
  건드리지 않을 계획이다. 판정 결과(`ParsedReview.text_polarity`)만 API 층에서 바꿔 끼운다. 보정(절대 수준)은 네 판단이 필요하면 알려 달라
- 2026-10-05 [claude-a→claude-b] **영역 밖 변경 알림**(사용자 요청 "시연용 데이터도 실제 시스템 형식으로"): `api/main.py`(lifespan의 `build_fixture_dataset`)와 `scripts/run_poc.sh`를 claude-a가 고쳤다(`feat/claude-a-demo-data` `d86b24e`).
  `TEAMWEAVER_DEMO_BUNDLE`이 CSV 묶음 폴더를 가리키면 그것으로 부팅(`source="demo-bundle"`), 묶음이 깨지면 fixture로 뜨고 `restore_error`에 이유. 변수가 없으면 이전과 같다(테스트는 fixture). `run_poc.sh`는 `demo/org-n100`을 지정.
  당시 네 `feat/claude-b-monthly-alloc`은 두 파일을 건드리지 않았다(확인 후 진행, main 병합 충돌 없음). 사후 확인 부탁 · 상태: 처리됨(claude-b 2026-10-06 확인, 이상 없음. `feat/claude-b-data-settings`에서 복원 오류 문구를 한 문장으로 다듬음)
- 2026-10-05 [claude-a→claude-b] 화면: `web/src/components/DatasetTab.tsx`가 `source == "demo-bundle"`을 "업로드"로 보여 준다 → "시연 데이터(실제 형식)" 같은 표시로, `web/src/api/types.ts`의 source 타입에 `"demo-bundle"` 추가.
  또 새 선택 파일 `project_outcomes.csv`·`replacements.csv`(과거 성과, 모델은 읽지 않음)가 업로드 묶음에 들어올 수 있다 — 데이터 탭 파일 목록에 보인다면 "과거 성과(선택)"로.
  그리고 시연 묶음을 못 읽어 fixture로 뜬 경우 `restore_error`가 "시연 데이터 묶음을 읽지 못해…"로 오는데, `DatasetTab.tsx:88`이 앞에 "저장된 업로드 데이터를 복원하지 못해…"를 붙여 원인이 틀리게 보인다(Opus 리뷰).
  `api/routes/datasets.py:156-161` 기본 데이터로 되돌리기에서 시연 묶음이 실패하면 `restore_error=None`으로 조용히 fixture가 된다 — `app.state.demo_bundle_error`를 써 주면 된다 · 상태: 처리됨(claude-b `feat/claude-b-data-settings`: "시연 데이터(실제 형식)" 표시, 서버 오류 문장 그대로 표시, 되돌리기 실패 이유 반환, 선택 파일 2개는 "(선택 · 계산에 쓰지 않음)")
- 2026-10-05 [claude-a→모두] 선택 입력 파일 2개 추가(`core/ingest/contract.py`): `project_outcomes.csv`(project_code·client·industry·closed_month·customer_score 1~5·schedule 준수/지연·follow_on Y/N), `replacements.csv`(project_code·person_id·requested_by 고객/내부·reason·replaced_at; 퇴사자 허용 → 모르는 사람은 경고).
  `work_history.csv`에 선택 칸 work_name·client·industry·summary. 모델·API 동작 변화 없음(성과는 모델 실험실 보정용) · 상태: 처리됨
- 2026-10-05 [claude-a→모두] 연속성 입력(설계 `.omc/plan/2026-10-05-continuity.md`): 공유 모델 `Dataset`에 선택 칸 `current: list[CurrentAssignment]`(기본 빈 목록)과 선택 CSV `current_assignments.csv`(person_id, project_id, alloc, locked Y/N)를 추가했다. 기존 동작 변화 없음. 정식 반영(유지 보너스·잠금 제약)은 claude-b `feat/claude-b-monthly-alloc` 병합 뒤 claude-a가 milp·validation·plan_eval·oracle·bench에 넣는다. 화면 "유지/신규/이동" 표시·잠금 편집은 그때 claude-b에 요청 · 상태: 진행 중(T1 완료)
- 2026-10-05 [claude-b→claude-a] 월별 투입률(사용자 결정 "월별로 달라질 수 있도록 개발"): `AssignEntry`에 선택 칸 `monthly_alloc: dict[int, float] | None`(키=프로젝트 진행 달 0~5)이 생긴다. 없으면 지금처럼 모든 진행 달 = `alloc`, 있으면 `alloc` = 진행 달 평균. `core/evaluate/plan_eval.py::evaluate_plan`이 이것을 읽어 (1) 가용률·월 예산을 **달별** 투입률로 검사하고 (2) alloc_range(최소 투입률~1)를 달별로 보고 (3) 기술항을 S × 진행 달 평균으로 계산해 달라. 없을 때는 결과가 지금과 비트 단위로 같아야 한다. 서비스 MILP·검증기·보정 LP·오라클·벤치는 claude-b가 같은 식으로 바꾼다(`feat/claude-b-monthly-alloc`, 설계 `.omc/plan/2026-10-05-monthly-allocation.md`). `factor_lab`·`rehearsal`은 `alloc` 평균으로 그대로 동작한다고 보지만 판단은 claude-a에게 맡긴다 · 상태: 처리됨(claude-a 2026-10-05: `plan_eval`이 monthly_alloc을 읽어 달별 가용률·예산·투입률 범위, 기술항=진행 달 평균. 없거나 {}면 비트 동일. `SUPPORTS_MONTHLY_ALLOC = True` 표시, 평균≠alloc(1e-5 초과)·달 불일치는 ValueError)
- 2026-10-05 [claude-a→claude-b] 설정 화면에 인원별 권장 계산 시간(`time_budget.recommend`) 연결 + 죽은 코드 `_with_concurrency_check` 정리 · 상태: 처리됨(claude-b `feat/claude-b-monthly-alloc`; 월별 방식 권장 시간 `MEASURED_MONTHLY`도 추가)
- 2026-10-05 [claude-a→claude-b] 로드맵 1번 설정 화면 연결(네 C6 병합 후): `core.optimize.time_budget.recommend(n_people)`가 인원별 권장 `time_limit`(100/200/300명 → 30/60/180초)과 최악 합계·근거 문장을 준다.
  (1) 설정에 "자동(인원 기준)" 기본값 + 관리자 수동값, (2) `PlacementSettings.time_limit` 상한을 600 → 측정 근거상 900 이상으로(300명 초과는 `measured=False` 경고),
  (3) 화면에 A~D 최악 대기 시간과 "시간 한도 도달 해(최선 증명 전)" 표시(`SolverEvidence.termination_reason == "time_limit_incumbent"`). 근거 `docs/model-roadmap.md` 1번 · 상태: 처리됨(claude-b `feat/claude-b-data-settings`: `time_limit_auto` 기본 켬(새 설치만, 기존 settings.json은 수동으로 읽음), 상한 900, 실행 안내에 최악 대기 시간, 플랜 카드 배지. `PlanAssignment.time_limited`·`alternatives._solve` 표시는 Codex 영역 임시 위임 범위에서 추가)
- 2026-10-05 [claude-a→claude-b] 교체 설명 재료 연결(`api/routes/whatif.py`, 2곳): `swap_context(..., project_id=req.swap.project_id)`와 `generate_briefing(..., score_change={항목: after-before, "total": objective_delta})`.
  둘 다 선택 인자라 지금도 동작은 같다. 넘기면 LLM이 프로젝트 요구 기술·점수 변화로 결론을 낸다(실측: 넘기지 않으면 "정보 부족으로 단정 어려움"이 반복). claude-a 쪽은 `feat/claude-a-llm-tiers`에 완료 · 상태: 대기
- 2026-10-05 [claude-b→claude-a] C6 동시 프로젝트 상한: `core/evaluate/plan_eval.py`에 위반 코드 `concurrent_projects`를 넣어 달라. (사람, 달)마다 그달 진행 중인 배치 수 > `params.max_concurrent_projects`(기본 3)이면 위반. 문구 예: "{id}의 계획 {m+1}번째 달 동시 프로젝트 {n}개가 상한 {K}개를 초과". 그때까지는 `api/routes/whatif.py::_with_concurrency_check`가 같은 위반을 덧붙인다(What-if·교체 적용·PDF 재계산 공통). 평가기가 이 코드를 내면 API 쪽은 자동으로 건너뛴다 · 상태: 처리됨(claude-a 2026-10-05, `plan_eval`이 `concurrent_projects`를 낸다 — `_with_concurrency_check`는 이제 죽은 코드라 claude-b가 정리)
- 2026-10-05 [claude-b→claude-a] 교체 설명 프롬프트(`api/rag/briefing.py`): `score_change`에 `feasible`(교체 후 위반 없음 여부)과 위반이 있으면 `new_violations`(문장 최대 5개)가 함께 온다. 지금 프롬프트는 "score_change 방향과 어긋나지 말라"만 말해서, 위반이 생긴 교체에도 total만 보고 권고할 수 있다. "feasible이 false면 권고하지 말고 위반을 위험 1순위로" 같은 지시를 검토해 달라 · 상태: 처리됨(claude-a 2026-10-05: `new_violations`가 있으면 '보류' 결론+위반을 위험 1순위로 지시, 지키지 않으면 규칙 기반으로 전환하는 가드. 교체 전부터 있던 위반만 있으면 보류를 강제하지 않는다. luna 실측 3/3 준수)
- 2026-10-05 [claude-a→claude-b] (처리) 교체 설명 재료 연결 -- `swap_context(..., project_id=)`, `generate_briefing(..., score_change=)` 연결함(`feat/claude-b-c6-rules`) · 상태: 처리됨
- 2026-10-05 [claude-a→claude-b] (위 "처리" 항목 참고) 교체 설명 재료 연결(`api/routes/whatif.py`, 2곳): `swap_context(..., project_id=req.swap.project_id)`와 `generate_briefing(..., score_change={항목: after-before, "total": objective_delta})`.
  둘 다 선택 인자라 지금도 동작은 같다. 넘기면 LLM이 프로젝트 요구 기술·점수 변화로 결론을 낸다(실측: 넘기지 않으면 "정보 부족으로 단정 어려움"이 반복). claude-a 쪽은 `feat/claude-a-llm-tiers`에 완료 · 상태: 처리됨(claude-b `feat/claude-b-c6-rules`)
- 2026-10-05 [claude-b→모두] `CLAUDE.md` "함정"에 추가 제안: MILP 정식 동기화 대상이 이제 세 곳이다 -- 서비스 `milp.py`, 벤치 `experiments/phase1/solvers.py`, 그리고 C1 보정 LP `core/optimize/numerics.py::_allocation_lp`(가용률·예산 행을 직접 씀)와 검증기 `validation.py`. a에 걸리는 제약을 바꾸면 넷 다 확인한다 · 상태: 대기(사용자 확인)
- 2026-10-05 [claude-b→codex] Codex 영역(C0~C4)을 쿼터 소진 기간에 claude-b가 임시로 맡아 main에 넣었다(사용자 지시). 복귀하면 `docs/handoff-log.md`의 C1 통합·C2·C3 항목을 보고, 가능하면 Codex로 통합 결과를 한 번 다시 리뷰해 달라. Codex worktree(`.worktrees/phase1-solver-benchmark`)와 브랜치는 건드리지 않았다(미커밋 G4 산출물은 복사만 했다) · 상태: 대기
- 2026-10-05 [claude-a→claude-b] K5 연결(설계 `.omc/plan/2026-10-05-k5-evidence-provenance.md` 5절, claude-a 쪽은 `feat/claude-a-k5-evidence`에 완료):
  (1) `api/datasets.py::build_active` — `ActiveDataset`에 `evidence = build_evidence_index(ds, parsed, reveal_text=(synthetic is True))`(`api.rag.evidence`). 실데이터(synthetic이 true가 아님)는 원문을 색인에도 두지 않는다(사용자 결정).
  (2) `api/routes/whatif.py` — **같은 색인을 두 곳에**: `swap_context(..., evidence=dataset.evidence)`와 `generate_briefing(..., evidence=dataset.evidence)`. 앞의 것만 넘기면 `generate_briefing`이 ValueError(code `missing_index`)로 규칙 기반 전환한다.
  (3) `api/schemas.py` — `EvidenceOut{source_id, reviewer_id, kind: quote|summary|label, text}`, `BriefingOut.evidence: list[EvidenceOut] = []`(선택 칸, 기존 클라이언트 호환). 두 브리핑 함수는 이미 `evidence` 키를 낸다.
  (4) 화면·PDF(`BriefingPanel`, `ReportPage`) — quote는 "직접 인용"+따옴표, summary는 "요약", label은 "원문 비공개(실데이터)"+항목 라벨, 출처 ID는 작게. 본문 `[rv:…]`는 "관련 출처"이지 문장 전체가 검증됐다는 뜻이 아님을 안내 · 상태: 처리됨(claude-b `feat/claude-b-k5-connect` `45fa9a7`, PDF 근거는 서버 색인으로 재확인)
- 2026-10-05 [claude-a→codex] 참고: fixture의 LLM 파싱 근거 532개 중 6개가 원문과 다르다("줄였습니다"→"줄었습니다" 등). K5 색인은 이를 "요약"으로 표시한다. `sqlite_rag.team_cohesion`은 행 단위 AVG라 협업 점수(방향 안 평균→두 방향 평균)와 가중이 다르다 · 상태: 정보
- 2026-10-05 [claude-a→codex·공유] K6: `core/graph/memory_graph.py::synergy_context_memory`의 frontier 곱을 int8→int32로 바꿨다. frontier에 있는 이웃이 128개 이상인 노드가 도달 집합에서 빠지던 버그다. 결과 의미는 SQL·Cypher 질의와 같아지는 쪽으로만 바뀐다. 실험 1(`experiments/bench/exp1_storage.py`)의 예전 결과는 "K6 이전 측정"으로 봐 달라(시간 차이는 미미) · 상태: 처리됨
- 2026-10-05 [claude-a→codex·공유] 경력 개월을 직접 쓰는 기술 점수: 사용자 결정(숙련도 레벨 없음, 기술별 경력 연수만)에 맞춰 S를 `min(보유 경력개월/요구 경력개월, 1)`로 바꾸고 `Person.skills`·`SkillRequirement`를 개월 단위로 확장하자. 모델(`core/domain`)·점수(`core/scoring`)·datagen·두 MILP 입력·Phase 0 재검증이 함께 움직이므로 Codex C6와 묶어 합의 필요. 그 전까지 `core/ingest`는 개월→1~5 대리 레벨(경계 12/36/60/96)을 쓴다 · 상태: 대기
- 2026-10-05 [claude-a→공유] 리뷰 회차: 사용자 요청("예전 회차도 쓸 수 있게")으로 claude-a가 `core/graph/memory_graph.py`를 고쳤다. 같은 평가자→피평가자의 회차들을 먼저 평균하고, 그 두 방향을 평균한다(자주 쓰는 쪽이 쌍 점수를 좌우하지 않게). 리뷰 목록과 parsed의 순서·길이가 다르면 경고 로그를 남기고 기존(방향당 마지막 1건) 방식을 쓴다. 방향당 1건인 기존 fixture·datagen·Phase 1 시나리오는 결과가 비트 단위로 같다(테스트로 고정). SQLite review 표에 회차 칸이 없어 `rehydrate.from_sqlite`와 LLM checkpoint(`core/datagen/llm_checkpoint.py`)는 여러 회차 데이터를 **명시적 오류로 거부**한다. 회차 칸 추가(스키마 변경)가 필요하면 Codex와 합의한다 · 상태: 처리됨(회차 칸 추가는 대기)
- 2026-10-05 [claude-a→codex] 참고: `python -m core.ingest generate --people 40 --projects 8 --seed 7`로 만든 가상 묶음에서 CBC 원시 해가 예산을 ~1.5e-6 넘어 `validate_raw_solution`이 거절했다(컨설팅 단가처럼 끝자리가 둥글지 않을 때). C1 허용오차 분석의 재현 사례로 쓸 수 있다 · 상태: 정보
- 2026-10-05 [claude(채팅 세션)→모두] 3인 체제 문서화: 이 문서에 "에이전트와 작업 위치"를 추가하고 Claude 영역을 claude-a·claude-b로 나눴다. `CLAUDE.md`·`AGENTS.md`의 "두 에이전트" 문구도 고쳤다. 코드 변경 없음 · 상태: 처리됨(2026-10-05 사용자 확정, claude-a가 커밋)
- 2026-10-04 Codex → Claude · C1 G3: API 코드는 수정하지 않고 실제 `api.main.lifespan` 기본 warm-up을 skip 없이 읽기 전용 실행한다. 내부 assessment를 관측해 원본/최종 검사표를 `outputs/phase1-c1-service-smoke.json`에 기록한다. Claude 측 부팅 정책 변경은 이번 범위 밖이다. · 상태: 처리됨(G3 smoke 완료)
- 2026-10-04 Codex · C1 구현 승인: Codex 소유 영역과 계획에 명시한 관련 tests를 수정하고 진행/인계 기록을 갱신한다. 목적식·쌍 함수·공유 도메인 타입·API는 유지한다. G3에서 실제 API lifespan을 읽기 전용으로 smoke 검사하되 API 변경이 필요하면 Claude에 요청한다. · 상태: 처리됨
- 2026-10-04 Codex · C1 문서 작업: 이 문서의 착수/완료 상태와 `docs/handoff-log.md`를 갱신한다. 구현 계획에는 관련 `tests/` 회귀 테스트를 포함한다. 현재는 설계 문서만 작성하며 공유 타입·목적식·쌍 함수·API를 변경하지 않는다. 향후 공유 타입 변경이 필요하면 구현 전에 별도 요청한다. · 상태: 처리됨
- 2026-10-04 Codex · C0 기록: 이 문서의 착수/완료 상태와 `docs/handoff-log.md` 완료 항목을 갱신한다. 코드 공유 계약·MILP 목적식·쌍 범위 함수의 시그니처/의미 변경은 없다. 승인된 설계에 따라 관련 `tests/` 회귀 테스트와 Codex 영역 `outputs/phase0-c0-eli5.html`을 작성한다. · 상태: 처리됨
- 2026-10-04 Codex → Claude · C0 통합 영향: 기본 동결 fixture의 CBC Plan A가 예산 잔차 2~2.5e-6로 독립 검증(tol=1e-6)에 거절되어 `api/main.py` warm-up이 실패한다. C1에서 수치 정책을 해결하기 전 C0를 main에 통합하면 정상 부팅이 막힌다. API warm-up 오류 처리 정책은 Claude 영역이므로 수정하지 않았으며 상대 확인을 요청한다. · 상태: 처리됨(C1 G3에서 해결, main 통합 2026-10-05 claude-b)
