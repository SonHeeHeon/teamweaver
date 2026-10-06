# TeamWeaver — 세션 시작용 지도

SI 조직의 인력→프로젝트 배치 최적화 시제품. 기술 적합도(S)·협업 시너지(C)·월별 가용률·
월 예산·등급 정원을 MILP로 함께 풀고, A/B/C/D 대안·교체 검토(What-if)·LLM 설명·PDF를 제공.
**모든 데이터는 가상(seed 고정)이며 사업 효과는 `NOT_CALIBRATED`** — 계산이 맞다는 것과
현실 성과가 좋다는 것은 별개 주장이다. 문서·보고에서 둘을 섞지 않는다.

**Codex와 Claude 2개 계정(claude-a, claude-b)이 병행 개발 중이다.** 작업 전에 다음 순서로 확인한다.
1. `git worktree list`와 `git log --all`로 Codex의 새 브랜치나 커밋이 있는지 본다.
2. `docs/handoff-log.md`(서로 한 일)와 `docs/work-split.md`(파일 소유 영역·작업 목록·요청 기록)를 읽는다.
3. 내 ID(`echo $CLAUDE_AGENT_ID`: claude-a 또는 claude-b)의 영역 밖 파일은 고치지 않는다. 영역은 `docs/work-split.md`의 소유 영역 표가 정한다
   (claude-a: `core/ingest/`, `core/evaluate/`, `api/rag/` · claude-b: `api/`(rag 제외), `web/`, `scripts/`).

task를 끝내면 `docs/handoff-log.md` 맨 위에 항목을 추가한다.

상세 맥락(모델 수식·실험 결과·입력 계약·로드맵)은 `docs/project-context.md`를 먼저 읽는다.
`outputs/eli5-project-history-roadmap.html`(원본 전체 해설, ~35k 토큰)은 그 요약으로 부족할 때만.

## 브랜치·작업 위치 (2026-10-04 기준)

| 브랜치 | 위치 | 내용 |
|---|---|---|
| `main` | 저장소 루트 | Plan 1~5 + Phase 0/1(10-03 병합) + K1 What-if 정정(10-04 병합) |
| `feat/phase1-solver-benchmark` | `.worktrees/phase1-solver-benchmark` | Codex 작업 브랜치. 이후 커밋은 `docs/handoff-log.md`에 기록된다 |
| `feat/phase0-model-validation` | `.worktrees/phase0-model-validation` | 과거 브랜치(main에 포함됨) |

- 작업 위치: 루트 체크아웃은 claude-a가 쓴다. claude-b는 `.worktrees/claude-b-<slug>`, Codex는 `.worktrees/`를 쓴다(`docs/work-split.md`).
- 원격 `origin`(GitHub)이 있다. **push·PR은 사용자가 요청할 때만 한다.**
- 개발 이력: Claude는 Plan 1~5와 K*, Codex는 Phase 0~1과 C*를 맡았다. 브랜치별 최신 상태는 `docs/handoff-log.md`에서 확인한다.
- 기록 위치가 다르다: Plan 1~5는 루트 `.omc/plan`·`.omc/reports`(gitignore, 루트에만 존재).
  Phase 0~1은 Git 추적 `docs/superpowers/{specs,plans}/`, `docs/reviews/`, `docs/phase1-checkpoint.md`,
  `outputs/*.html`.
- `experiments/results/phase1/`(약 423MB 원시 결과)은 비추적 로컬 자료. **커밋하지 않는다.**

## 코드 지도

- `core/domain/models.py` 입력 계약(Pydantic). 6개월 고정 horizon, 레벨 1~5, 4등급.
- `core/graph/memory_graph.py` S/C 계산용 인메모리 구조 · `core/graph/sqlite_store.py` 근거 검색용 SQLite.
- `core/scoring/engine.py` S(요구 대비 레벨, 가중 평균) · C(0.4·협업개월 + 0.6·리뷰점수).
- `core/optimize/milp.py` 제품 MILP(PuLP+**HiGHS**, 2026-10-05부터. `MilpParams.solver="cbc"`는 비교용) · `highs_portfolio.py` 시드 포트폴리오 · `alternatives.py` 다양성 컷 대안 · `metrics.py` 지표
  · `validation.py` 원시 해 독립 검증 · `greedy.py` 기준선.
- `core/ingest/` 실데이터 CSV 묶음 → `Dataset`(계약·검증 리포트·가상 묶음 생성, `python -m core.ingest generate|check`).
  숙련도는 원천에 레벨이 없어 경력 개월을 대리 레벨로 바꾼다(경계 12/36/60/96개월).
- `api/` FastAPI: `/api/meta`, `/api/optimize`(SSE), `/api/whatif`, `/api/report`(Playwright PDF).
- `web/` Vite+React+TS+Tailwind. `experiments/phase0/`, `experiments/phase1/` 검증·벤치 도구.

## 코드만 봐서는 모르는 함정

- **MILP 정식이 두 벌이다**: `core/optimize/milp.py::solve_milp_diagnostic`(서비스, HiGHS)와
  `experiments/phase1/solvers.py::_build_model`(벤치, 3솔버 공용). 목적식·제약을 바꾸면 둘 다
  고치고 Phase 0 검증(`uv run --group benchmark python -m experiments.bench.phase0_model`)을 다시 돌려야 한다.
- **HiGHS 경계 잔차**: HiGHS는 z=1.0000000000000007 같은 값을 돌려주고, 독립 검증기(C0)는 범위를 조금이라도
  넘으면 거절한다. `milp._snap_bounds`가 1e-9 안쪽 잔차만 경계로 옮긴다(원본은 native_capture에 보존).
  이 처리가 없으면 HiGHS 해가 전부 "검증 실패"가 된다(2026-10-05 리허설에서 실측).
- **협업 보상 쌍 상한 200**(`MilpParams.max_pairs`, 2026-10-05): 쌍 변수는 (쌍 수 × 사업 수)라 예전 5000에서는 200명부터
  HiGHS가 600초에도 미충원 57석, 300명은 해가 없었다(`rehearsal/results/n*/pairs.json`). "협업 시너지"는 |C| 상위 200쌍만 보상한다.
  규모를 키우거나 보상 쌍을 늘리면 반드시 쌍 수 측정(`python -m rehearsal.run --size N --stage pairs`)으로 확인한다.
- **정답 비교는 gap=0**: Phase 0 오라클 사례·시험은 `gap=0.0`으로 푼다. 기본 5% gap이면 솔버가 최적 전에
  멈춰도 정상인데, 예전에는 CBC가 우연히 최적을 내서 통과하고 있었다.
- **익숙한 쌍 = 최근 `clique_window_months`(서비스 36) 중 `clique_threshold_months`(서비스 12) 이상**(2026-10-06 사용자 결정,
  `rehearsal/results/rule-compare.html`). 달 정보는 `CoworkRecord.months_ago`(ingest가 채움) → `MemoryGraph.cowork_within`.
  협업 점수 C는 10년 전체를 쓴다. 독립 검증기·Phase 0 오라클은 서비스 함수를 빌리지 않고 원자료로 센다. 달 정보 없는 fixture는 전체 이력 기준.
  모델 기본값(`MilpParams`)은 None·6(예전과 동일)이라 시험은 그대로다 -- 서비스 기본은 `api/settings.PlacementSettings`.
- **시드 포트폴리오**: `TEAMWEAVER_SOLVER_SEEDS`(기본 1, 시연 `run_poc.sh` 4)개 spawn 프로세스가 같은 모델을 시드만 바꿔 푼다.
  조밀한 데이터에서 1시드면 대안이 품질 하한에 걸려 안 A만 나올 수 있다(실측). 모델은 시작 뒤 전용 파이프로 넘긴다 --
  시작 인자로 넘기면 자식이 시작 중 죽을 때 부모가 영원히 멈춘다(main 가드 없는 스크립트에서 실측). HiGHS 증명 상한은 `SolverEvidence.best_bound`.
- 필수 기술은 **하드 제약이 아니다**(S 점수로만 유도). 프로젝트에 기재되지 않은 등급은 정원식
  대상이 아니어서 예산·가용률 안에서 자유롭게 선택될 수 있다.
- What-if `objective_delta`는 교체 전후를 `core/evaluate/plan_eval.py`로 현행 MILP 전체 목적(4항)과
  제약으로 재평가한 **참고값**이다(재최적화 아님, 2026-10-04 K1). 평가기의 쌍 범위는 `milp.py`의
  `pruned_pairs`/`_overfamiliar_pairs`를 import하므로, MILP 정식이 바뀌면 평가기도 함께 확인한다.
- 숫자 세 개를 혼동하지 않는다: 최적화율(~0.93, 기술항/LP 완화 상한) · 기술 충족률(~0.55, 보조 지표)
  · 솔버 gap(≤5%, 현행 절삭 목적 기준). 분모가 모두 다르다.
- 결과 맞추기 금지: 목표 수치를 위해 datagen·계수를 조정하지 않는다(초기 설계 원칙). 실패·중단·
  미측정도 결과에서 빼지 않고 기록한다.
- Phase 1 run 재개는 매니페스트의 `source_commit`·의존성 해시에 묶인다. **장기 run 진행 중에는
  커밋하지 않는다**(재개 거부). 재개는 macOS `ps`/`killpg`가 허용된 환경(샌드박스 밖)에서만.

## 명령과 검증 기준

- 설정 `uv sync` · 테스트 `uv run pytest -q` (기준선: 2026-10-06 **1288 passed, 19 deselected**, `--group benchmark` 포함 · slow 19 passed, 웹 153)
- 서비스 솔버 HiGHS(`highspy`)는 기본 의존성(2026-10-06 이전엔 benchmark 그룹에만 있어 `uv sync`가 지웠다). SCIP 포함 실행 `uv run --group benchmark ...`, tiktoken 캐시는
  `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache`.
- 느린 E2E `uv run pytest -m slow` · API 개발 시 `TEAMWEAVER_SKIP_WARM=1`(부팅 시 ~30초 사전계산 생략).
- 웹 `cd web && npm test` · 타입체크는 **`npx tsc -b`**(`tsc --noEmit`은 프로젝트 참조 구조라 항상 통과하는 no-op).
- PDF는 `web/dist` 빌드와 `uv run playwright install chromium`이 선행돼야 한다.
