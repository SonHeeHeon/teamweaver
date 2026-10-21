# TeamWeaver — 세션 시작용 지도

SI 조직의 인력→프로젝트 배치 최적화 시제품. 기술 적합도(S)·협업 시너지(C)·월별 가용률·
월 예산·등급 정원을 MILP로 함께 풀고, A/B/C/D 대안·교체 검토(What-if)·LLM 설명·PDF를 제공.
**모든 데이터는 가상(seed 고정)이며 사업 효과는 `NOT_CALIBRATED`** — 계산이 맞다는 것과
현실 성과가 좋다는 것은 별개 주장이다. 문서·보고에서 둘을 섞지 않는다.

**범위 공지(사용자 2026-10-12): 이 프로젝트는 PoC이자 과제 제출용이다. 실제 인사 자료는 적재할 수 없고, 적재하지 않는다.**
개발·검증·시연·보고는 모두 가상 데이터로 한다. 실데이터용 장치(입력 계약·원문 비공개 모드·사내 판정 주소 등)는 설계로 남기되,
실데이터를 전제로 한 작업(첫 적재 준비, 실제 성과로 모델 보정, 현실 평가·Shadow·파일럿과 그 운영 준비)은 하지 않는다 -- 보고서에는 "실제 도입 시 필요한 것"으로만 적는다.

**Codex와 Claude 2개 계정(claude-a, claude-b)이 병행 개발 중이다.** 작업 전에 다음 순서로 확인한다.
1. `git worktree list`와 `git log --all`로 Codex의 새 브랜치나 커밋이 있는지 본다.
2. `docs/handoff-log.md`(서로 한 일)와 `docs/work-split.md`(파일 소유 영역·작업 목록·요청 기록)를 읽는다.
3. 내 ID(`echo $CLAUDE_AGENT_ID`: claude-a 또는 claude-b)의 영역 밖 파일은 고치지 않는다. 영역은 `docs/work-split.md`의 소유 영역 표가 정한다
   (claude-a: `core/ingest/`, `core/evaluate/`, `api/rag/` · claude-b: `api/`(rag 제외), `web/`, `scripts/`).

task를 끝내면 `docs/handoff-log.md` 맨 위에 항목을 추가한다.

상세 맥락(모델 수식·실험 결과·입력 계약·로드맵)은 `docs/project-context.md`를 먼저 읽는다.
`outputs/eli5-project-history-roadmap.html`(원본 전체 해설 20장, 10-11판, ~50k 토큰)은 그 요약으로 부족할 때만.

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

- `core/domain/models.py` 입력 계약(Pydantic). 계획 기간 6 또는 12개월(데이터마다, 2026-10-19), 레벨 1~5, 4등급.
- `core/graph/memory_graph.py` S/C 계산용 인메모리 구조 · `core/graph/sqlite_store.py` 근거 검색용 SQLite.
- `core/scoring/engine.py` S(요구 대비 레벨, 가중 평균) · C(0.4·협업개월 + 0.6·리뷰점수).
- `core/optimize/milp.py` 제품 MILP(PuLP+**HiGHS**, 2026-10-05부터. `MilpParams.solver="cbc"`는 비교용) · `highs_portfolio.py` 시드 포트폴리오 · `alternatives.py` 다양성 컷 대안 · `metrics.py` 지표
  · `validation.py` 원시 해 독립 검증 · `greedy.py` 기준선.
- `core/ingest/` 실데이터 CSV 묶음 → `Dataset`(계약·검증 리포트·가상 묶음 생성, `python -m core.ingest generate|check`). 인사 시스템 API 받기 `hr_api.py`(서버 `api/hr_source.py`), 진행 중 사업 기술 경력 늘리기 `aging.py`.
  숙련도는 원천에 레벨이 없어 경력 개월을 대리 레벨로 바꾼다(경계 12/36/60/96개월). 개월 비율 직접 쓰기는 채택 안 함(사용자 결정 2026-10-12, 근거 `docs/skill-score-decision.md`).
- `api/` FastAPI: `/api/meta`, `/api/optimize`(SSE), `/api/whatif`, `/api/report`(Playwright PDF).
- `web/` Vite+React+TS+Tailwind. 메뉴(`components/Shell.tsx`, 2026-10-18)는 3상황 중심 -- 신규 사업 편성(첫 화면)·긴급 투입·연간 계획(① 조건·계산 `annual` → ② 편성안 비교·확정 `plans` = 예전 What-if 대시보드), 지금 배치 › What-if(`whatif`, 지금 배치에서 바꿔 보기, `web/src/whatif/`), 공통 부품 `web/src/ui/`. `experiments/phase0/`, `experiments/phase1/` 검증·벤치 도구.

## 코드만 봐서는 모르는 함정

- **MILP 정식이 두 벌이고, 같은 식을 다시 쓰는 곳이 셋 더 있다**(claude-b 제안 2026-10-05): `core/optimize/milp.py::solve_milp_diagnostic`(서비스, HiGHS)와
  `experiments/phase1/solvers.py::_build_model`(벤치, 3솔버 공용), 그리고 C1 보정 LP `core/optimize/numerics.py::_allocation_lp`(가용률·예산 행)와
  독립 검증기 `core/optimize/validation.py`(목적값 재계산), 평가기 `core/evaluate/plan_eval.py`. 목적식·제약을 바꾸면 모두
  고치고 Phase 0 검증(`uv run --group benchmark python -m experiments.bench.phase0_model`)을 다시 돌려야 한다.
- **전일 투입 `allocation_mode="full"`**(2026-10-19 사용자 결정, claude-a 설계 `.omc/plan/2026-10-19-concurrency.md`): 투입률 변수 없음(a = z, 100%),
  사업 기간 가용률 1.0인 사람만 자격(`milp.full_eligible`, 문턱 `FULL_TIME_MIN_AVAILABILITY` -- 검증기·오라클은 같은 값을 따로 적는다), 같은 달 사업 수 ≤ `Person.max_concurrent`
  (기본 1, 부서장이 사람 단위로 허용한 **프로젝트 동시 수행**만 2~3, 사업마다 예산 전액). 용어는 "프로젝트 동시 수행"(동시 수행)으로 통일한다(사용자 지시 2026-10-19 -- 회사 규정상 다른 이름을 쓰지 않는다).
  예전 fixed·monthly는 그대로(시험·옛 기록). full에서만 탐욕 초기해(`greedy.warm_start_full` → HiGHS `setSolution`), C1 보정은 건너뛴다.
- **HiGHS 경계 잔차**: HiGHS는 z=1.0000000000000007 같은 값을 돌려주고, 독립 검증기(C0)는 범위를 조금이라도
  넘으면 거절한다. `milp._snap_bounds`가 1e-9 안쪽 잔차만 경계로 옮긴다(원본은 native_capture에 보존).
  이 처리가 없으면 HiGHS 해가 전부 "검증 실패"가 된다(2026-10-05 리허설에서 실측).
- **협업 보상 쌍 상한 200**(`MilpParams.max_pairs`, 2026-10-05): 쌍 변수는 (쌍 수 × 사업 수)라 예전 5000에서는 200명부터
  HiGHS가 600초에도 미충원 57석, 300명은 해가 없었다(`rehearsal/results/n*/pairs.json`). "협업 시너지"는 |C| 상위 200쌍만 보상한다.
  규모를 키우거나 보상 쌍을 늘리면 반드시 쌍 수 측정(`python -m rehearsal.run --size N --stage pairs`)으로 확인한다.
- **정답 비교는 gap=0**: Phase 0 오라클 사례·시험은 `gap=0.0`으로 푼다. 기본 5% gap이면 솔버가 최적 전에
  멈춰도 정상인데, 예전에는 CBC가 우연히 최적을 내서 통과하고 있었다.
- **익숙한 쌍 = 최근 `clique_window_months`(서비스 36) 중 `clique_threshold_months`(서비스 12) 이상**(2026-10-06 사용자 결정 · **지금 같은 사업에 함께 있는 쌍은 그 사업에서 감점 안 함**(2026-10-19 사용자 결정, `MemoryGraph.current_teams` ← 현재 배치, `milp.familiar_exempt` -- MILP·독립 검증기·평가기·C1 확인(numerics)·Phase 0 오라클·긴급 투입 거르기·운영 중 기여 분해가 같은 규칙, 벤치 정식·factor_lab에는 없음),
  `rehearsal/results/rule-compare.html`). 달 정보는 `CoworkRecord.months_ago`(ingest가 채움) → `MemoryGraph.cowork_within`.
  협업 점수 C는 10년 전체를 쓴다. 독립 검증기·Phase 0 오라클은 서비스 함수를 빌리지 않고 원자료로 센다. 달 정보 없는 fixture는 전체 이력 기준.
  모델 기본값(`MilpParams`)은 None·6(예전과 동일)이라 시험은 그대로다 -- 서비스 기본은 `api/settings.PlacementSettings`.
- **시드 포트폴리오**: `TEAMWEAVER_SOLVER_SEEDS`(기본 1, 시연 `run_poc.sh` 4)개 spawn 프로세스가 같은 모델을 시드만 바꿔 푼다.
  조밀한 데이터에서 1시드면 대안이 품질 하한에 걸려 안 A만 나올 수 있다(실측). 모델은 시작 뒤 전용 파이프로 넘긴다 --
  시작 인자로 넘기면 자식이 시작 중 죽을 때 부모가 영원히 멈춘다(main 가드 없는 스크립트에서 실측). HiGHS 증명 상한은 `SolverEvidence.best_bound`.
- **운영 중 편성은 갭 0으로 푼다**(`core/evaluate/operating.exact`, 2026-10-06): 빈자리 감점(자리당 100)이 섞인 목적에서 5% 갭은
  1~2명 이동의 개선(1~2점)을 통째로 가린다(실측: 빼 오기 허용이 더 나쁜 모순). 변경 예산 제약은 `core/optimize/incremental.py` --
  옮긴 사람은 "원래 없던 사업"에 들어가야 한다(동시 수행자가 기존 동시 수행으로 조건을 채워 대기되던 빈틈, 리뷰 MUST).
  K는 **옮긴 사람 수**다(사람마다 0/1 보조 변수, 2026-10-12 -- 예전엔 빠진 배치 수라 동시 수행자 한 명이 2로 셌다).
  **추가 제약 콜백이 새 변수를 만들면 정수형(0/1)만** 쓴다: `milp`가 그 풀이 값을 고정해 `numerics.project_additive_constraints`에 넘겨야
  C1 보정·추가 행 독립 검증이 켜진다. 연속 보조 변수나 기존 변수를 바꾸면 "모델 변형"으로 보정·검증이 조용히 꺼진다(실측: 추가 행 0개).
  시연 묶음은 둘: 연초 계획 `demo/org-n100`(전원 배치)·운영 중 `demo/org-n100-operating`(생성기 v4: 87명 배치 중·대기 10명·육아휴직 3명·신규 제안 2 + 추가 과제 1), 같은 사람.
  생성기 v2(2026-10-11): 가상 이름(`core/ingest/names.py`, ID별 고정·중복 없음), 아쉬운 점 항목마다 문장·직설형 평가자 8%·어긋난 글 5%(manifest `review_style`) --
  고른 항목의 난수열은 그대로라 글만 바뀐다. 글을 바꾸면 `python -m rehearsal.make_demo`(+`make_broken_demo`) → 판정·미리 계산·판정 동봉을 한 데이터 폴더에서 이어서 만든다.
  운영 중 묶음을 "전부 다시 짜기"(안 A~D)로 풀면 200·300명은 시간 한도 안에 빈자리가 남는다(정원·예산이 현재 명단에 맞춰짐) -- K 비교로 쓴다.
- **편성 기록(JSON)이 시연 데이터 고르기를 대체했다**(2026-10-18 사용자 지시, `api/records.py`·`api/routes/records.py`): 데이터 탭의 시연 고르기·
  `/api/datasets/demo(s)`·부팅/전환 때의 미리 계산 자동 설치는 없앴다. 시연은 `demo/records/*.json`을 화면에서 불러와 **열기**.
  기록 = 원천 CSV 원문 + 평가 글 판정값(리뷰 순서) + 계산 요청(가중치·**설정 화면 모양**의 milp_params·대안 수) + 편성안 + 운영 중 K 행.
  열면 데이터를 바꾸고(`source="record"`, 재기동해도 `records/active.json`으로 다시 연다) 편성안을 결과 캐시에 넣는다 -- 화면이 기록의 요청 그대로
  `/api/optimize`를 부르면 캐시에서 나오고 서명은 그 경로가 붙인다. **기록의 솔버 증명(종료 사유·상한·허용 차이)은 버리고 목적값·빈자리는 평가기로
  다시 계산한다**(termination="record" -- 서버가 남의 주장을 다시 서명해 "최선 증명"으로 세탁하지 않게, 폴백 리뷰 M3). 위반이 있으면 받지 않고, 점수만
  다르면 받고 알린다. 불러오기·열기는 LLM을 부르지 않는다(판정값 없으면 항목 점수). milp_params에 설정 화면에 없는 칸(보상 쌍 수 등)을 담으면
  PDF 요청이 422다 -- `records.request_fields`가 설정 모양으로만 쓴다. `demo/records/`는 `python -m rehearsal.make_demo_records`가
  `demo/precomputed/`(+ 동봉 판정값)에서 만든다 -- 미리 계산을 다시 만들면 이것도 다시 돌린다. `api/demo_precomputed`의 검증 함수·`install`은 기록이 쓴다.
  **리뷰 글 판정은 `demo/review_judgments.json`에 동봉한다**(2026-10-11) -- 미리 계산을 다시 만들면 `uv run python scripts/export_demo_judgments.py`도 돌려 함께 커밋한다.
- **앱 전체 로그인**(2026-10-18, `api/admin.AppLoginGate`): 로그인하지 않으면 `/api/admin`·로그인·로그아웃 밖의 모든 `/api/*`가 401. 기본 계정 admin/admin1234
  (비밀번호 env가 있으면 그것, 스크립트 토큰만 있으면 기본 계정을 열지 않음). **시험은 `TEAMWEAVER_LOGIN=off`**(tests/api/conftest, rehearsal/run 서버) --
  새 리허설·스크립트 서버에도 넣거나 로그인한다. 관문은 root_path를 뗀 경로로 본다. 화면은 `web/src/auth.ts`가 fetch를 감싸 쿠키를 싣고 401이면 로그인 화면으로.
  PDF(`/report`)는 데이터를 주입받아 API를 부르지 않고, 시스템 한글 글꼴로 찍는다(웹 글꼴이면 PDF 글자 추출이 한 자씩 떨어진다).
- **실험 G 파트너 다양성 하한**(`MilpParams.partner_floor`, 기본 0 = 꺼짐): 서비스 MILP·독립 검증기·평가기·예산 보정에만 있고
  벤치 정식·Phase 0 오라클·HTTP 계약·운영 중 기여 분해에는 없다. 측정(`rehearsal/results/partner-compare.html`)상 SI 팀 크기에서는 거의 걸리지 않고
  200명에서 풀이를 망쳤다 -- **사용자 결정(2026-10-07): 채택 안 함, 쌍 감점 유지**. 켜려면 위 빠진 곳을 모두 맞춘다.
- **기술 이름 사전·하위 기술 부분 인정이 입력 단계 기본값이다**(2026-10-09 사용자 결정, `core/ingest/skills.py`·`skill_dictionary.json`): `to_dataset`이 보유·요구 기술 이름을 대표 이름으로 바꾸고
  하위 기술 경력의 `0.7^깊이`를 상위 기술에 인정한다(2026-10-11 사용자 결정 "70%로 고정", 이전 0.5 · 근거 없는 가정값, NOT_CALIBRATED, 설정 노출 안 함). 지식 그래프(`build_kg`)는 같은 함수로 같은 경력을 만든다 -- 둘에 같은 인자를 넘긴다.
  시연 org-n200·n300의 S가 바뀐다(100은 그대로) -- 미리 계산 6개는 70%·생성기 v2 기준으로 2026-10-12에 다시 만들었다. 사전에 없는 이름은 합치지 않고 경고만 한다.
  **계산 버전(`api/datasets.rules_version`)에 사전 버전·인정 비율이 들어간다**(2026-10-13, claude-b `d05ee7f`를 되살림) -- 사전·비율을 바꾸면 캐시·미리 계산·저장된 교체가 옛 값으로 쓰이지 않는다.
  사전·비율을 바꾸면 미리 계산을 다시 만든다. 10-13에는 입력(묶음·판정값·사전·70%)이 미리 계산 때와 같아 다시 풀지 않고 버전 필드만 옮겼다(파일의 `rekeyed`, 서버 재채점 통과).
  인정분이 경력을 정한 기술은 `Person.skill_credit`(공유 계약, 2026-10-11)에 출처·개월이 남고, SQLite 근거 색인은 별도 표 `skill_credit`(재수화는 읽지 않는다 -- S/C에 안 쓰임), 교체 설명 문맥은 `credited_from`을 붙인다.
  교체 설명은 그 출처를 매번 괄호로 쓰게 지시하고, 빠뜨리면 서버가 `참고:` 문장을 덧붙인다(`api/rag/briefing._credit_notes`, 규칙 설명 폴백도 따로 적음).
  위반 교체는 첫 문장이 보류 결론이 아니면 버려진다(규칙 설명으로 대체) -- 지시문을 바꾸면 외부 AI로 다시 잰다(2026-10-12: 괄호 9/10, 위반 교체 채택 6/10 → 18/20).
  보류 판정(`briefing._is_hold`)은 허용 어미 목록이 아니라 막을 표현 + "마지막 절이 교체를 지지하면 아님"이다 -- 바꾸면 `experiments/jev/results/e5_*.json`의
  첫 문장 291개를 다시 돌려 본다(허용 목록 방식은 정상 보류 42개를 거절했다, 지금 방식은 2개만 다름 -- 둘 다 "보류는 아니고").
- **인사팀 소명 글은 자리표시 + 연결 말 허용 목록**(2026-10-09, `core/kg/justify.py`·`api/rag/justification.py`): AI는 사실 `{F#}`를 고르고 정해진 문법(주어·`LABELS`·연결어·맺음말)으로만 엮는다.
  숫자·충족/미달은 서버가 사실 문구로 채운다 -- 자유 문장·판단어 금지 목록은 폴백 리뷰마다 뚫렸다. 프롬프트와 검사기가 같은 `LABELS`를 쓴다.
  AI가 빠뜨린 불리한 사실(`Fact.adverse`)은 서버가 정해진 마지막 문단으로 덧붙인다(`adverse_addendum`, 사용자 결정 2026-10-10 · 사람별 줄 2026-10-11 -- 형식을 바꾸면 `python -m rehearsal.addendum_by_person`, AI 호출 없음).
  사실 문구·문법·라벨·지시문을 바꾸면 `python -m rehearsal.justify_scale_e7c`(약 $0.7, 처음 보는 데이터 포함)를 다시 돌린다. 측정 E7c: 채택 99.4%·처음 보는 데이터 99.6%(10-12 재측정 — 부정 평가 모두 사실로·라벨 머리 "각각" 검사 뒤: 99.0%·100%, 탈락 6건 모두 S2), 글은 템플릿보다 조금 짧다. 사내 LLM 대리 GLM-5.3-Flash high(E7c-F, `python -m rehearsal.justify_flash`, 75건 약 $0.22):
  채택 97.3%·무결성 73/73이지만 한 편 p50 60초 -- 화면·PDF의 AI 대기 상한(`api/routes/justification.AI_TIMEOUT_S` 40초) 안은 16%뿐이다.
- **`/api/optimize`는 새 풀이마다 슬롯을 잡는다**(2026-10-12, `TEAMWEAVER_OPTIMIZE_SLOTS` 기본 2, 바쁘면 429 -- 캐시·미리 계산은 슬롯 없이). 슬롯은 풀이 스레드가 끝날 때 놓고,
  화면이 끊기면 풀던 안까지 바로 중단한다(아래 "계산 멈추기·끊김 중단"). `time_limit`은 900초(`api.settings.TIME_LIMIT_MAX`)를 넘으면 422다 -- 깎아서 풀면 서명된 설정과 달라진다.
  리허설·시험에서 최적화를 동시에 여럿 돌리면 429가 날 수 있다. /api JSON 쓰기 요청 본문은 2 MiB(`TEAMWEAVER_MAX_JSON_BODY_BYTES`, PDF는 따로).
- **계산 멈추기·끊김 중단**(2026-10-12, `core/optimize/interrupt`): 새 계산은 SSE `start {run_id}` → `POST /api/runs/stop`(finish: 풀던 안은 그때까지의 해, 종료 사유 `user_interrupt` -- 시간 한도와 구분).
  연결이 끊기면 abort(시드 프로세스까지 즉시 정리). 멈춤 뒤에는 `HighsPortfolio`가 새 풀이를 시작하지 않고 `SolveCancelled`(RuntimeError 아님) --
  대안 생성기·운영 K 비교가 이것을 "실패"로 삼키지 않게. 풀이 경로에 새 `except Exception`을 넣으면 멈춤이 실패로 바뀐다.
  PDF 계산 신뢰도는 결과·K 행의 서버 서명 `evidence`+`evidence_token`(plan_token에 묶임)이 맞을 때만 찍는다 -- 화면은 `evidence`를 그대로 돌려보낸다(다시 조립하면 서명이 어긋난다).
- **3가지 핵심 상황**(2026-10-19, claude-b, 설계 `.omc/plan/2026-10-18-three-scenarios.md`): 신규 사업 편성(`web/src/scenarios/NewProjectsScreen`)·긴급 투입(`EmergencyScreen`)·연간 계획(기존 요건 설정).
  **작업 데이터 판**(`api/workspace.py`·`workspace_edits.py`·`routes/workspace.py`): 신규 사업 등록·단계(예정/제안/수주)·떨어짐·고정(사업·사람 = `current_assignments.locked`)·확정은
  모두 원천 CSV 묶음을 고쳐 **업로드와 같은 경로**(`validate_and_build` → `_activate` → `DatasetStore.save`)로 켠다 -- 계산 버전이 바뀐다. 단계·이전 사업은 manifest `pipeline`(CSV 계약은 그대로),
  "적힌 0명 = 그 등급 금지"(자동 편성 기준 -- 손 편성은 다른 등급 빈자리를 메울 때 등급 대체로 사유와 함께 바뀐다, 2026-10-21)라 양식은 네 등급을 다 보낸다. 이력·되돌리기는 `<데이터 폴더>/workspace/`(켜기 **전에** 쓴다). `mapping.json` 묶음은 편집 불가.
  확정은 평가기로 **새로 생기거나 커진 위반**(한도와의 거리)·잠긴 배치 빼기를 거절한다. 신규 사업 후보는 등록한 정원·예산 그대로 다시 채점(`rescore_unchanged` -- 보강 계산은 자리를 늘린다),
  "끝나 가는 인력" = 지금 사업이 모두 신규 사업 **시작 전**에 끝나는 사람. 긴급 투입 확정은 서버가 계산한 증원(`apply_hint`: 정원·예산·요구 기술)을 데이터에 적는다.
  시연은 `demo/org-n100-yearend`(`scripts/make_yearend_demo.py`: 운영 중 묶음 + 4개 사업 12월 종료, 사람·평가 글 같음 → 판정 동봉 그대로).
  후보 점수는 나눠 보인다(사용자 2026-10-19 "점수 변화는 나눠 보이도록"): `scenarios.split_unfilled`가 `unfilled_seats_delta`(메운 자리, 운영 중 보강과 같은 이름·부호)·`delta_excl_unfilled`(빈자리 몫 뺀 변화)를 붙이고 `delta_total`·순위는 그대로.
  **신규 사업 후보는 "넣기"로 고른 사람을 팀원으로 친다**(2026-10-20 사용자 요청 2·4·5, claude-b): `POST /api/pipeline/{id}/candidates {picks}`가 고른 사람을 넣은(빼 오는 사람은 원래 사업에서 뺀) 배치로
  나머지 후보를 다시 채점하고 `draft`(등급별 자리·사업별 정원 변화·점수 나눔·새 위반 문장·팀 안 쌍)를 준다 -- 데이터는 확정 때만 바뀐다. 후보 행은 `seat_effects`(평가기 `shortfalls` 전후, 사업별 채움+/빔−),
  `skill_delta`·`collab_delta`(협업 시너지 + 익숙한 쌍 감점, 모든 사업), `partners`(이 사업 팀원과의 쌍 중 계산에 들어가는 것 -- 평가기와 같은 `pruned_pairs`·익숙한 쌍·감점 예외)를 단다.
  협업 점수 C는 **두 사람 사이 값**이라 사람 자체의 협업 점수는 없다 -- 화면은 "누구와 +0.17"로 보인다. 긴급 투입 후보도 같은 칸(`pair_view`).
  **긴급 투입 손 편성 미리 보기**(2026-10-20 사용자 "긴급 투입에도 미리 보기", claude-b): `POST /api/emergency/preview {picks(grant), skills, pull_budget}` -- 지금 배치 − 빼 오는 배치 + 고른 사람에
  확정 때 적을 증원(`_apply_hint`)을 적용한 그래프로 확정과 같은 판정(`new_or_worse`), 동시 수행 지원(grant)은 그 사람만 같은 달 2곳. 응답 `draft.entries`·`apply_hint`를 그대로 확정에 쓴다.
  고른 사람을 팀원으로 친 후보 다시 매기기를 함께 준다(최선 조합은 다시 풀지 않음). 요구 기술을 더한 그래프는 (판·사업·기술·인원) 키로 4개까지 재사용(`_augmented_graph`).
  **확정 취소**(2026-10-21 사용자 요청 3, claude-b -- 결정: 확정 한 건 통째 · 빼 온 사람은 원래 사업으로 · 긴급 투입 포함): `GET|POST /api/workspace/cancel`은 되돌리기(마지막 판부터)와 달리
  그 확정만 **지금 판 위에서 거꾸로** 적용한 새 판(kind `cancel`, summary `target_version`+`target_at` -- 같은 명단을 다시 확정하면 판 버전이 같아 시각까지 본다)이다.
  `workspace_edits.revert_ops`가 확정 직전(B)·확정(A)·지금(N) 판을 칸마다(배치·등급 정원·예산·요구 기술·동시 수행 허용) 견줘 N==A인 칸만 B로, 그 뒤 바뀐 칸·고정한 배치·없어진 사람/사업은 건너뛴다.
  원래 사업 복귀가 위반이면(위반 종류별 `_implicates`) 그 복귀만 빼 대기로 둔다. 되돌릴 것이 모두 빠지면 422(확정을 "취소됨"으로 잠그지 않게). 계산 뒤 판이 바뀌었으면 409(옛 판 덮어쓰기 방지).
  취소는 `review_docs/cancelled.json`에도 남아(20판 밖에서도 검토 자료 잠금) 취소 판을 되돌리면 지운다. 되돌리기는 확정을 꺼낼 때만 `record_undone`(취소 판은 앞 판과 버전이 같을 수 있다).
  확정 취소 버튼은 변경 이력 + 신규 사업 "지금 팀" 줄 오른쪽(사용자 후속 요청) -- 후자는 그 확정이 넣은 사람(이력 항목 `joined`)이 **지금 팀에 남아 있는** 가장 최근 확정을 고른다.
  **자동 편성 K 행 설명**(사용자 요청 1): 둘째 줄 = 신규 사업마다 `4/4명 · 대기 · 이동 · 원래 · 동시 수행` + 소명 버튼, "명단" 펼치기 = 이름·옮김 사슬·K−1보다 새로 옮긴 사람 -- `web/src/scenarios/autoView.ts`가 K 행의 `entries`·`diff`만으로 만든다(서버 계산 그대로).
  **기다림 표시**(사용자 요청 2): 공통 `ui.Spinner` -- 소명 미리보기는 창을 바로 열고 "쓰는 중"(멈출 수 있음), 판을 바꾸는 버튼·검토 자료 AI 초안·PDF에도.
  **설정 화면 이름은 화면 말로**(2026-10-20 사용자 "설정 화면 용어 정리"): 계산 멈춤 기준(gap)·계산 시간(안 하나당)·익숙한 쌍 기준/감점/볼 기간·협업 반영 정도·동시에 풀어 볼 갈래 수·같은 달 최대 사업 수·"나눠 투입 · …" -- 저장 키는 그대로(이름은 `settingsFields.FIELDS`·`MODE_LABEL` 한 곳).
  **운영 중 비교 K는 0~5**(`api/routes/operating.OPERATING_KS` -- 화면 기본·밤 배치 `DEFAULT_KS`·시연 미리 계산이 같은 목록, 2026-10-20 사용자 "k는 최대 5까지"). 미리 계산·밤 행은
  요청 K 목록이 저장된 목록의 **앞부분**이면 잘라 준다(`demo_precomputed.operating_rows` -- K 행은 앞 K만 이어받는다). 시연 운영 중 묶음 4개는 0~5로 다시 풀었다(0~3 행은 예전과 같았다).
  긴급 투입 최선 조합(갭 0)은 운영 중 보강·밤 배치처럼 `OPERATING_TIME_LIMIT_MAX`(600초)에서 자른다 -- 12개월 자동 한도(최대 900초)를 그대로 쓰면 가벼운 계산 슬롯을 15분 잡는다.
- **등급 대체**(2026-10-21 사용자 결정 "위 아래 모두 허용하고, 사유는 전부 받고, 예산을 넘으면 증액을 함께 적자", claude-a `core/evaluate/grade_swap.py`): 손 편성 확정(신규 사업 넣기·긴급 투입 세 경로)에서
  **이 확정으로 새로 넘친 등급 × 확정 뒤 모자란 자리**를 짝지어(가까운 등급부터, 같으면 위 자리) 데이터 정원을 자리 −n·들어간 등급 +n으로 바꾼다 -- 막지 않고 사유를 받는다.
  사유는 확정 이력 summary `grade_swaps`(검토 자료 사실 `MOVE_GRADE`, 아래 등급 대체는 확인할 사실), 대체가 있으면 넘친 만큼 `budget_add`. 화면 미리 계산(`routes/scenarios.swap_graph`·`_apply_hint`)과
  확정 검증(`routes/workspace._check_grade_swaps`)이 같은 함수라 다르면 422. 원래 넘쳐 있던 등급·정원 표에 없는 등급은 대체가 아니다. 확정 취소·되돌리기가 정원·예산을 함께 돌린다.
  계산 모델(MILP·평가기·검증기)은 그대로 -- 자동 편성은 대체하지 않고 빈자리로 보인다(부족 신호를 묻지 않게). 후보 목록은 같은 등급이 먼저(`classify`). 긴급 투입은 모자란 자리가 있으면 증원 대신 대체(예전엔 넘친 등급 정원만 늘려 빈자리가 남았다).
- **연간 계획·What-if 분리**(2026-10-21 사용자 "what if는 현재 프로젝트 인력 배치에서 부서장이 이렇게 바꿔봤을때 … 지금의 화면은 연간 계획 화면과 맞물려있는 개념", claude-a): 계산한 안 A~D 위에서 바꿔 보는 화면은 연간 계획 ② 편성안 비교·확정(`plans`),
  What-if(`whatif`, `web/src/whatif/CurrentWhatif`)는 **작업 데이터의 지금 배치**에서 바꿔 보고(`/api/whatif`·`/api/plans/apply-swap`에 `grade_swap: true` -- 지금 배치 대비 등급 대체·예산 증액을 적용해 평가, 확정과 같은 `core.evaluate.grade_swap.substitution`) 담은 변경을 한 번에 확정한다(손 편성 `apply_manual` → 검토 자료).
  확정 창 `components/ConfirmChanges`는 연간 계획 "안 X로 확정"(source `plan`, 검토 자료 없음)과 What-if가 함께 쓰고, 서버 `POST /api/workspace/preview`(옮김·새로·빠짐·등급 대체·예산 증액·새 위반·고정 충돌, 데이터 그대로)로 미리 계산한다.
  지금 배치가 있으면 안 카드·배치 명단에 "지금 대비 옮김"(`components/rosterDiff`, 서버 preview와 같은 정의). ② 다듬기(plans)는 `grade_swap` 없이 예전처럼 등급이 바뀐 교체를 경고로 본다.
  **기술 가중치 칸은 없앴다**(화면 요청 `weights` = 빈 값 = 기본, API 칸은 옛 기록 재현용) -- ① 조건·계산은 `/api/meta` 사업 `requirements`(기술·최소 레벨·인원)를 보인다.
  **고정 선택**(2026-10-21 사용자 "고정된 사람이 있으면 팝업을 띄워서 … 계속 고정할지 아니면 고정 풀고 … 다시 배치를 할지 물어보고"): 화면은 고정 배치가 있으면 계산 전에 묻고(`components/LockChoiceDialog`, `/api/meta locked_assignments`),
  `/api/optimize keep_locked`면 모든 안에서 고정 z=1(`alternatives.generate_plans_streaming(fixed=)`, 지킬 수 없는 고정은 계산 전 422). **캐시 키에는 참일 때만 들어간다**(`ResultCache.key(keep_locked=)`) -- 풀고 짜기 = 예전 계산·키·미리 계산·기록 그대로.
  풀고 짠 안의 확정은 확정 창 "고정을 풀고 확정"(`ApplyIn.release_locks`, source plan만 → 이력 `locks_released`). 편성 기록·밤 배치 저장본도 선택을 담고 고정이 빠진 안은 거절한다.
  연초 6개월 시연 `org-n100`(고정 4건)의 기록 안은 풀고 짠 안이다(유지하면 안 A 59.47 → 56.81, 안 4개 약 65초 -- 2026-10-21 측정). 시연 연간 계획 장면은 묻는 창이 없는 백지 연간 기록(`org-n100-annual`)으로 한다(`docs/demo-script.md` D2~D5, 숫자 `python -m rehearsal.whatif_scene_numbers`).
- **빼 온 자리 결정**(2026-10-21 사용자 "빼간 자리를 매꿔야한다는게 있어야 … 못메꾼다면 메꾸지 않는다라는 표기를 통해 더이상 그건 컨트롤 안해도 되는걸로", claude-a `core/evaluate/seat_holes.py`): 모든 확정(`/api/workspace/apply`)은
  **이 확정으로 진행 사업에 새로 생긴 빈자리**(팀·정원이 바뀐 사업 × 등급, 등급 대체·증원을 적용한 정원 기준 -- `new_holes`)를 같은 확정에서 메우거나 `seat_waivers`("메우지 않음" + 사유)로 받아야 한다(다르면 422).
  메우지 않음 = 그 사업 그 등급 정원 −n(`workspace_edits.waive_seats`, 예산 그대로, 정원은 사업 전체 기간 단위) → 그 뒤 모든 계산이 빈자리로 세지 않는다. **편성 중인 신규 사업**(manifest pipeline·proposals, `api.seat_fill.staffing_ids`)은 빼고 센다 -- 시작 전 빈자리는 정상이다.
  미리 계산(`/api/workspace/preview`·긴급 투입 preview·신규 사업 draft·최선 조합)이 `holes`·`fill_options`(빼 오기 없는 후보, 같은 등급 먼저)·`fills`(같은 확정에서 메운 사람)를 주고, 손 편성은 `fills`로 메울 사람을 넣는다.
  빼 온 사업을 다른 등급으로 메우면 그 사업 등급 대체도 확정에 들어간다(`_apply_hint`·draft `side_grade_swaps` -- 예전엔 대상 사업 대체만 적어 확정이 422였다). 확정 취소는 정해지지 않은 빈자리를 만들지 않게 그 정원 되살리기·메운 사람 빼기를 덜어 낸다(`routes/workspace._keep_seats_decided`).
  검토 자료 MOVE_FILL은 "메우지 않기로 함 — 사유"(확인할 사실). 자동 편성 K 행은 보통 다 메워 예전 확인 창 그대로, 빈자리가 남으면 확정 창(`ConfirmChanges` source auto)으로 연다.
- **야간 배치**(2026-10-18 · 10-19 재정비, claude-b `api/nightly.py`): 매일 `TEAMWEAVER_NIGHTLY_WINDOW`(기본 00:00-05:00) 시작에 **지금 데이터 한 묶음**만, **AI를 쓰지 않는다**.
  긴급 대기 명단(진행 사업마다 위반 없는 상위 5명, 설정 지문에 묶임 → `GET /api/emergency/standby`), 연간 계획(백지 데이터일 때만)·운영 중 비교(신규 사업 있을 때만)·낮 요청 기록을
  **시간 한도만 늘려** 풀고(키·서명은 낮 설정) 캐시에 넣는다 -- 이미 캐시에 있는 요청(편성 기록에서 연 결과 등)은 다시 풀지 않고, 끝까지 풀지 못한 묶음은 저장하지 않는다.
  소명 글은 확정 때 화면에서 쓰고 `api/justification_cache`(키 = 데이터 버전·명단·설정·AI·소명 재료 해시·생성기 코드 지문)가 저장해 다시 돈 내지 않는다. 점검 보고서 `GET /api/nightly`.
  `TEAMWEAVER_NIGHTLY=off`로 끈다 -- `tests/conftest.py`가 시험 전체에서 끈다. **시험에서 밤 실행·소명을 돌릴 때는 설명 AI를 막는다**(`tests/nightly/conftest.py` -- lifespan의 `load_env`가 `.env` 키를 올린다).
  시험 전체에서 실제 OpenAI 채팅 호출은 막혀 있다(`tests/conftest._no_real_ai_calls`, 2026-10-20 -- 올라간 키는 세션 끝까지 남는다). 설명 AI를 바꿔 끼울 때는 `ai_client` 픽스처(`tests/conftest.py`, 모듈 단위 픽스처·도우미는 `tests/ai_override.override_ai` -- 라우트가 쥔 함수를 찾아 바꾸고 끝나면 직전 override로 되돌림)를 쓰고, `with ai_client(fake) as used:`의 `used`로 가짜가 정말 쓰였는지 확인한다 -- `api.deps` 이름을 열쇠로 쓰면 다른 시험이 그 이름을 바꿔 끼운 뒤엔 빗나간다(claude-b 확인 권장). 이름을 열쇠로 쓴 `dependency_overrides[...get_openai_client_or_none]`는 `tests/test_no_real_ai.py`가 실패시킨다(10-20 모두 옮김).
- **인사팀 검토 자료**(2026-10-19, claude-a `api/review_docs.py`·`routes/review_docs.py`·`core/evaluate/review_moves.py`·`web/src/review/`): 확정(`apply_auto|manual|emergency`) × 사람이 새로 들어간 사업마다 한 편.
  전 = 확정 직전 판 zip의 현재 배치, 후 = 확정 판 -- 옮겨 온 사람의 출처·원래 사업이 잃는 것·메움을 평가기로 잰다. **시스템 사실은 만들 때 계산해 자료에 저장**하고(그래서 그 확정이 지금 판일 때만 만든다),
  PUT은 사실을 받지 않는다(extra=forbid). 결론 문장은 서버가 쓰지 않는다(사용자 결정 -- 부서장 판단 칸). 해명·이의는 사실 지문 `core.kg.justify.fact_key`에 붙는다.
  "되돌려짐"은 `api.workspace.undo`가 남기는 기록(`review_docs/undone.json`)으로만 판정하고, "이력에 있음"은 확정(apply_*) 항목만 센다(시작 판 base는 재확정이 아니다) -- 다른 데이터로 바꿨거나 20판 밖으로 밀린 것은 "이력 밖"(열고 고치고 내보낼 수 있다). 긴급 투입 AI 재료는 `focus_input`(새로 온 사람만).
  승인·반려는 만들지 않았다 -- 보고서 8.4 "결재 연결(실제 도입 시)".
  긴급 투입 화면의 "무슨 일인지"는 확정 요청 `situation`(300자, 긴급 투입만 이력 summary에 남김) → `confirms()`의 `situation` → 긴급 투입 자료 `situation_memo` 처음 값이다
  (2026-10-19 부서장 점검 f6, claude-b). 저장된 자료의 `confirm`에는 넣지 않는다(고친 메모와 원문이 둘로 남지 않게). AI 재료가 아니다 -- note를 잘라 쓰지 않는다.
  **AI 초안은 짧은 요약 + 사실 검사**(2026-10-20 사용자 요청 6, claude-b `routes/review_docs.draft_summary`): What-if 소명 요약과 같은 코드(claude-a `api/rag/justification.generate_summary` -- 자리표시 문법,
  `verify_summary` S1·S2·3문장·근거 2~4개·불리한 사실 포함, 걸리면 규칙 요약)를 AI 한 번으로 부르고(긴 본문은 부르지 않는다), 저장하는 글을 검사기로 다시 돌려 `body_ai_check`(통과·방식·인용 사실)를 남긴다.
  요약을 못 만들면 예전 규칙 본문(`_rule_body`, AI 없음). 화면은 본문 아래 "사실 검사 통과 · 인용한 사실 n개"를 펼쳐 보인다. 이 브랜치는 claude-a `bd8332d`를 cherry-pick(`c2df966`)했다.
- **계획 기간 6|12개월**(2026-10-19 사용자 "계획기간 1년 확장도 하자", claude-b): `Dataset`이 모든 사람의 가용률 길이가 같고(`HORIZON_CHOICES` 6·12) 사업이 그 안에
  있는지 본다 -- 기간은 저장 필드가 아니라 가용률 길이(`Dataset.horizon_months`)라 기존 데이터 버전·캐시 키는 그대로. manifest `horizon_months` 6|12, 계산 모델은 원래 달 수 일반형.
  연간 시연 `demo/org-n100-annual`(`scripts/make_annual_demo.py`: org-n100과 같은 사람·평가 글, 상반기 사업 그대로 + 하반기 신규 사업, 휴직 가정 가용률, 현재 배치 없음 = 밤 배치 연간 계획 대상).
  화면 달 표시는 `/api/meta`의 `horizon_start`로 실제 달("27.4월", `allocFormat.monthLabel`) -- 12개월에서 순번 "1~12월"은 달력 달처럼 읽힌다.
- **생성기 v3 = 휴직·휴가 가정 + 등급별 정원 + 전일 비용 예산**(2026-10-19 사용자 가정·결정, claude-a 재설계 -- b 요청 `docs/requests/2026-10-19-availability-redesign.md`, 설계 루트 `.omc/plan/2026-10-19-concurrency.md`):
  가용률은 **모든 묶음**(연초·운영 중·연말형·연간)이 같은 창 -- `core/ingest/leave.py`(대부분 100%, 3% 육아휴직 12개월 해를 걸침 → 그 달 0, 2% 한 달 휴가는 가용률 그대로·manifest `leave`에만).
  사업 정원은 등급·달마다 (가용률 1.0인 그 등급 인원)의 85% 이하(`org_profile.shrink_to_supply` -- 예전 전체 85%는 등급을 안 봐 쪼개 넣어야 채워졌다), 예산 = 기준 단가 × 정원 × 1.0~1.25(`budget_of`),
  연초 현재 배치 100%·겹침 없음, 운영 중은 휴직 달이 걸린 사람을 진행 사업·대기에서 빼고 신규 제안 정원 = 대기 인력의 등급 구성(한 명 여유).
  **사람·이력·평가 글 난수열은 그대로**라 판정 동봉이 그대로 쓰인다(`export_demo_judgments.py --check`). 연간·연말형 도구(`scripts/make_{annual,yearend}_demo.py`)가 같은 함수를 쓴다.
  **12개월 데이터는 자동 시간 한도 ×4**(`api/settings.HORIZON_TIME_FACTOR`) -- `to_milp_params(n_people, months=)`. 전일 투입은 12개월 실측 하한(`MEASURED_FULL_12`, v5 100·200·300명 가장 느린 시드 52·186·312초 × 여유 2, 상한 900초)과
  큰 값이라 120·390·630초(claude-b 규칙 2026-10-19, claude-a v5 재측정, `scripts/measure_annual_time_budget.py` → `rehearsal/results/time-budget-annual.json` -- 사용자 "1년 계획 계산 시간 리밋은 더 널널하게"). 사업 구성을 바꾸면 다시 잰다.
- **생성기 v4·v5 = 사업 수명 주기 섞기 + 진행 사업 경력 잇기**(2026-10-19 사용자 원문 -- b 요청 6번 · 사용자 "기존 사업 20개는 여전히 전부 하반기 시작 … 잘못", "진행해야겠어"(선택 "갈아 끼우고 평가도 새로"), claude-a 설계 루트 `.omc/plan/2026-10-19-lifecycle-mix.md`·`-v5.md`):
  연초 계획은 실행 사업의 약 75%(`RUNNING_SHARE`)가 계획 전에 시작한 진행 사업이고, 운영 중은 진행 사업마다 수명 주기를 붙인다. 비율은 둘 다 운영 30%·고도화 20%·개발(2025) 20%·개발(2026 상반기) 30%이고, 운영 = 내부 2~3명·계획 뒤까지(사용자 "16개 중 6개면 적절", "2-3명이 평균").
  운영 사업의 `[추가 과제]`(내부 2명·6개월·외주 참고값 -- 연초 100명 1·200·300명 2, 운영 중은 신규 제안 중 1개, **b의 `pipeline` 모양** parent·external_headcount)를 붙인다.
  실제 시작 달은 `projects.csv start_month`(계획 전이면 로더가 **경고 없이** 자름), 단계는 manifest `lifecycle`(이름 머리말은 종류 `[운영]`·`[고도화]`·`[추가 과제]`만 -- 단계 말 `[실행]`·`[제안]`은 2026-10-20 사용자 "[제안]은 빼자"로 뺐다, 화면에서 단계가 바뀌면 이름이 낡는다)(`core/ingest/lifecycle.py`, 경고만, 계산에 안 씀). 시작 달은 계획 시작 기준 몇 개월 전으로 뽑는다(연도를 박으면 다른 시작 달 묶음이 깨졌다 -- Codex).
  연초 계획의 진행 사업은 빠짐없이 현재 팀이 있다(자리의 80~100%, 사용자 결정 10-19 -- 계획 첫 달 새 사업만 예전 70% 규칙).
  **v5 `_link_ongoing`**: 진행 사업 현재 팀원은 합류 달(계획 3~30개월 전, 마지막 교체 뒤, 첫 이력 뒤)부터 업무 이력이 그 사업이다. 지난 이력은 끊고, 기술 경력은 다시 세며, 끊긴 이력과 맞지 않는 평가는 지우고 진행 사업 팀원끼리 평가를 새로 만든다.
  원래 평가를 **먼저** 만든 뒤 고치므로 남는 글은 그대로이고, 새 글만 서비스 AI로 판정해 `demo/review_judgments.json`에 동봉한다(10-19 약 2,300건 · 1달러 미만). 생성기를 바꾸면 `build_demo`(키 있는 데이터 폴더)로 판정 → `export_demo_judgments.py` → `--check`.
  사업 번호: 연초 100명 사업 21개(운영·고도화 진행 사업에 머리말, 추가 과제 J021 -- 상위 J010), 연간 첫 하반기 신규 J022. 운영 중·연말형은 v4와 같다 -- 신규 제안 J018(제안)·J019(예정) + 추가 과제 J020(상위 J011), 연말형 끝나는 사업 J010·J007·J013·J016.
  전일 투입 권장표 `time_budget.MEASURED_FULL`(v5 100/200/300명 12/30/45초 → 권장 30/60/90초, 12개월 ×4). 사업 구성을 바꾸면 다시 잰다(v3 30/90/90, v4 60/90/120).
- **프로젝트 동시 수행 허용**(2026-10-19 사용자 결정 "사람 단위로, 사유와 함께"): manifest `concurrency`(`core/ingest/concurrency.py` 검사 → `Person.max_concurrent`),
  `POST /api/workspace/concurrency`(사유 필수 · 지금 겹침보다 낮게 다시 걸기·허용 없는 해제는 거절), 확정 `concurrency_add`(긴급 투입 "동시 수행으로 지원" -- 같은 판, 되돌리면 함께),
  `POST /api/concurrency/candidates`(빈자리마다 허용 후보, 다시 풀지 않음), 긴급 투입 `rank_candidates(offer_concurrency=True)`는 **허용이 필요한 후보만** 따로(섞으면 빼 오기·대기 후보가 밀려났다),
  검토 자료 사실 `MOVE_CONCURRENT`. 시연 데이터는 허용 0명. 예전 실험(experiments/jev·c6)은 fixed로 고정.
- **투입 불가 인력 표시**(2026-10-19 사용자 지시, `api/unavailable.py`·`web/src/components/availability.ts`·`scenarios/UnavailableList`): 사업 기간에 가용률 0인 달(육아휴직 등)이 있으면
  교체 검토·신규 사업 후보·긴급 투입에서 **보이되 고를 수 없게**, 사유를 빨간 글씨로("육아휴직 — 27.4~27.7월 투입 불가"). 계산(MILP·평가기·보강 계산)은 원래 달마다 가용률을 지킨다 -- 화면 표시만 더했다.
  `/api/meta` 사람에 `availability`·`leave`, 후보·긴급 응답에 `unavailable`.
- **추가 과제**(2026-10-19 사용자 예 "운영 중 사업에 클라우드 마이그레이션 추가 과제, 6달만 회사 인력 2명·외주 여러 명", `api/workspace_edits`): 진행 중 사업에 붙는 과제는
  **별도 사업 행** + manifest `pipeline[id].parent`·`external_headcount`(외주, 참고값 -- 계산에 안 씀). 고도화 과제(`predecessor`, 끝난 이전 사업)와 함께 못 고른다.
  상위 사업은 등록·상위 변경 때 "현재 배치가 있는 진행 사업", 과제 기간은 상위 기간 안(`_check_parent`, 원문 달 공백 정규화 `_period`). 추가 과제 칸은 추가 과제에만 쓴다 --
  일반 등록의 manifest 모양(데이터 버전)이 그대로. 후보는 상위 사업 팀원 `parent_team` 맨 위. 시연 데이터의 사업 수명 주기는 생성기 v4가 같은 모양으로 만든다(아래 생성기 v4 -- 연말형 J020이 J011에 붙은 추가 과제).
- **사업 수명 주기 표시**(2026-10-19, claude-b `api/lifecycle.py`·`web/src/components/{lifecycle.ts,LifecycleChips.tsx}`): manifest `lifecycle`(생성기 v4)과 `pipeline` 추가 과제를 합쳐
  `/api/meta` 사업 `lifecycle`·`period`(계획 밖까지 이어지는 사업의 원문 기간 -- `start_month`·`end_month`는 계획 안으로 자른 달 순번), `/api/workspace` 항목 `lifecycle`, PDF "사업 이력"에 싣는다.
  신규 사업은 **pipeline이 원본**(화면에서 고친 상위·외주가 이긴다, pipeline에서 상위를 풀면 생성 기록의 연결은 무시). 신규 사업 수정·떨어짐은 생성 기록도 맞춘다(`workspace_edits._sync_lifecycle` --
  안 하면 로더가 "없는 사업"·"시작 달이 다르다" 경고를 남긴다), 기록이 없는 사업엔 칸을 만들지 않는다. 계산(MILP·평가기)은 읽지 않는다. 꼬리표는 이름 머리말("[운영]")과 같은 단계를 다시 쓰지 않는다.
- **데이터 원천 = 인사 시스템 API(기본) · CSV(대체)**(2026-10-19 사용자 요청, claude-a, 계약 `docs/hr-api-contract.md`): `TEAMWEAVER_HR_API_URL`이 있으면 밤 배치 맨 앞(`Nightly._hr_sync` → 서버 루프에 맡김)과
  관리자 "지금 받기"(`POST /api/hr-source/sync`)가 인사 표만 받아 **지금 작업 데이터 판에 갈아 끼우고**(사업·정원·요구 기술·현재 배치·manifest 단계 등은 이 서비스 것) 업로드와 같은 검사·켜기
  (`api.workspace.commit`, 이력 `hr_sync`)로 켠다 -- 원문이 같으면 새 판 없음, 검사·연결 실패면 지금 데이터 그대로. 표마다 설정 JSON으로 `null`(지금 데이터 = CSV) 또는 끝점·칸 이름을 준다.
  진짜 인사 시스템은 없다 -- 가짜 서버 `scripts/mock_hr_api.py`로만 시험했고 시연은 CSV다. 받을 때 manifest `as_of`는 기술 경력 표를 받았을 때만 받은 달, 파일 해시 목록(`files`)은 뺀다.
  켜기(`hr_source._apply`)는 시작하면 바깥 취소와 상관없이 끝까지 간다(shield) -- 밤 배치는 시간 초과 때 **취소하지 않고** 기다리기만 그만둔다(Codex high, 켜기·저장본·이력 어긋남).
- **진행 중 사업 기술 경력은 이 서비스가 늘린다**(2026-10-19 사용자 질문 "달이 지나가면 … 기술 개월수를 업데이트가 돼야하지않나?", `core/ingest/aging.py`): 로더가 검증 뒤
  진행 중 업무에 등록된 기술(선택 표 `work_skills.csv`)만 그 경력의 `last_used_month` 다음 달 ~ 계획 직전 달(업무 기간 안)을 더한다 -- 원천이 이미 갱신했으면 두 번 세지 않는다.
  별칭 행은 행마다 따로(합칠 때 최대). 업무별 기술 표가 없으면 데이터가 계획 직전 달보다 오래됐을 때만 경고.
  어디까지 셌는지(마지막 사용 달·as_of) 모르는 행은 늘리지 않고 경고(기록 `held`), 10년 창 밖에서 마지막으로 쓴 행(행 자신의 마지막 사용 달로 판정)은 최근 달만 경력으로 남긴다
  (Codex 종료 리뷰 -- 이중 계산·옛 경력 되살림). **늘리는 규칙을 바꾸면 `AGING_RULES`를 바꾸고 미리 계산 버전을 옮긴다**(시연은 늘어난 달 0이라 `rekeyed`).
  manifest `skill_months_owner`="source"면 늘리지 않는다(설정 화면 "데이터 원천"에서 관리자가 바꿈 → 새 판). 규칙 표지 `aging.AGING_RULES`가 계산 버전에 들어간다.
  계획 기간 안에서 달마다 올리지는 않는다. 시연 묶음은 `as_of` = 계획 직전 달·경력이 그 달까지 세어져 늘어나는 달 0(숫자 그대로, 미리 계산은 버전만 옮김).
- **What-if 대시보드 정리·화면 친절하게**(2026-10-20 사용자 점검, claude-a -- b 영역 포함, 설계 `.omc/plan/2026-10-20-ui-friendly.md`):
  배치 명단은 사업 카드(머리 한 줄 + 이름표 줄, 2~3열, 정렬·사람 찾기, 100%는 숨김, 이름표를 누르면 기술 창 -- `AssignmentTable`). 시작·끝 정렬은 절대 달 번호(`lifecycle.absoluteMonth` -- 원래 기간 `period`와 계획 안 순번을 글자로 섞어 비교하면 계획 안에서만 도는 사업이 늘 맨 앞에 온다, 10-21 사용자 지적). 사업 코드는 코드 그대로 + 마우스를 올리면 이름(`ui.ProjectCode`·`PersonName.ProjectCodes`).
  **교체 검토 데려오기·맞바꾸기**: `SwapIn.in_from_project_id`(그 사업에서 뺌)·`exchange`(나간 사람이 그 자리로) -- `whatif._swapped_entries` 한 곳이 whatif·적용·저장 재생·PDF 재생을 모두 맡고,
  기본값 칸은 저장 모양에 나오지 않는다(예전 교체 서명 그대로). 원래 사업의 빈자리는 서버가 위험 항목 맨 앞에 정해진 문장으로 붙인다(`source_loss_notes` -- AI 지시문은 안 바꿈).
  AI 설명에 넘기는 점수 변화는 소수 둘째 자리로 반올림한다. 끌어 놓기는 섹션 밖으로 못 나가고(DragOverlay + 경계 modifier) 못 놓는 칸이면 이유를 띄운다.
  오른쪽 협업 관계망은 **팀원과의 관계**(`TeamRelations`)로 바꿨다 -- 10-21부터 관계 그림(왼쪽 빠질 사람·가운데 그 사업 팀원·오른쪽 들어올 사람, 선 굵기 = 개월·색 = 평가, 숫자 목록은 "목록으로 보기"). 색·굵기 규칙은 PDF 팀별 협업 지도와 공용(`components/relationTone.ts`·`RelationLegend.tsx`). `/api/meta` coworks `review`(두 사람 평가 극성)는 **가상 데이터만**(`build_meta(reveal_reviews=)`, 실데이터 K5).
  **인사팀 소명 요약**(`core/kg/justify.verify_summary`·`rule_summary_raw`, `api/rag/justification.generate_summary`): 본문과 같은 자리표시 문법·검사 + 맺음말 빼고 3문장·자리표시 2~4개·불리한 사실 하나 이상(Y1~Y4),
  걸리면 규칙 요약. 화면·PDF는 요약을 먼저, 본문은 "전체 보기". 측정 `python -m rehearsal.justify_summary`(229건 AI 93.0% · 규칙 7.0% · 무결성 실패 0 · $0.12, `rehearsal/results/justify-summary.json`) --
  요약 지시문·검사를 바꾸면 다시 잰다. 본문 측정(E7c)은 그대로(`generate_justification(summary=False)` 기본).
  단순 규칙 비교는 탭이 보일 때 저절로 계산(`BaselineCard active`), PDF 소명 포함은 내려받을 때 창으로 묻는다(하위 탭 옆 체크박스 없앰).
  교체 설명 이름은 **"XAI 브리핑" 그대로**(사용자 2026-10-20 "표현이 멋있어서" -- 한때 "AI 교체 설명"으로 바꿨다가 되돌림), 뜻은 화면 제목의 ⓘ. 버튼은 "교체 효과 보기".
  PDF 협업 그림은 **팀별 협업 지도**(`web/src/report/TeamNetworks`, 사용자 "pdf에 들어갈 괜찮은 협업 네트워크 그림"): 사업마다 팀원을 원 위에 놓고 팀 안 협업선만(굵기 = 개월, 색 = 평가, 13명부터 한 줄 카드).
  리포트 meta 협업선은 **같은 사업 쌍만**·평가 극성은 가상 데이터만(`report._report_meta(reveal_reviews=)`) -- 예전엔 배치된 사람끼리 전부(시연 100명 약 4,000개, 300명은 PDF 데이터 상한 압박). 힘 시뮬레이션 그림 `StaticNetwork`와 `d3-force`·`react-force-graph-2d`는 지웠다.
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

- 설정 `uv sync` · 테스트 `uv run pytest -q` (기준선: 2026-10-21 고정 선택·빼 온 자리 결정(claude-a `feat/claude-a-locks-seat-holes`) **2106 passed, 1 skipped, 20 deselected**, `--group benchmark` 포함 · slow 20 passed(10-21 분리 때), 웹 315 -- 브라우저 E2E는 `web/dist`가 최신이어야 한다(`cd web && npm run build`))
- 서비스 솔버 HiGHS(`highspy`)는 기본 의존성(2026-10-06 이전엔 benchmark 그룹에만 있어 `uv sync`가 지웠다). SCIP 포함 실행 `uv run --group benchmark ...`, tiktoken 캐시는
  `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache`.
- 느린 E2E `uv run pytest -m slow` · API 개발 시 `TEAMWEAVER_SKIP_WARM=1`(부팅 시 ~30초 사전계산 생략).
- 웹 `cd web && npm test` · 타입체크는 **`npx tsc -b`**(`tsc --noEmit`은 프로젝트 참조 구조라 항상 통과하는 no-op).
- PDF는 `web/dist` 빌드와 `uv run playwright install chromium`이 선행돼야 한다.
