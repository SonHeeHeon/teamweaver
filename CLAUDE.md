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

- `core/domain/models.py` 입력 계약(Pydantic). 6개월 고정 horizon, 레벨 1~5, 4등급.
- `core/graph/memory_graph.py` S/C 계산용 인메모리 구조 · `core/graph/sqlite_store.py` 근거 검색용 SQLite.
- `core/scoring/engine.py` S(요구 대비 레벨, 가중 평균) · C(0.4·협업개월 + 0.6·리뷰점수).
- `core/optimize/milp.py` 제품 MILP(PuLP+**HiGHS**, 2026-10-05부터. `MilpParams.solver="cbc"`는 비교용) · `highs_portfolio.py` 시드 포트폴리오 · `alternatives.py` 다양성 컷 대안 · `metrics.py` 지표
  · `validation.py` 원시 해 독립 검증 · `greedy.py` 기준선.
- `core/ingest/` 실데이터 CSV 묶음 → `Dataset`(계약·검증 리포트·가상 묶음 생성, `python -m core.ingest generate|check`).
  숙련도는 원천에 레벨이 없어 경력 개월을 대리 레벨로 바꾼다(경계 12/36/60/96개월). 개월 비율 직접 쓰기는 채택 안 함(사용자 결정 2026-10-12, 근거 `docs/skill-score-decision.md`).
- `api/` FastAPI: `/api/meta`, `/api/optimize`(SSE), `/api/whatif`, `/api/report`(Playwright PDF).
- `web/` Vite+React+TS+Tailwind. 메뉴(`components/Shell.tsx`, 2026-10-18)는 3상황 중심 -- 신규 사업 편성(첫 화면)·긴급 투입·연간 계획(예전 요건 설정), 공통 부품 `web/src/ui/`. `experiments/phase0/`, `experiments/phase1/` 검증·벤치 도구.

## 코드만 봐서는 모르는 함정

- **MILP 정식이 두 벌이고, 같은 식을 다시 쓰는 곳이 셋 더 있다**(claude-b 제안 2026-10-05): `core/optimize/milp.py::solve_milp_diagnostic`(서비스, HiGHS)와
  `experiments/phase1/solvers.py::_build_model`(벤치, 3솔버 공용), 그리고 C1 보정 LP `core/optimize/numerics.py::_allocation_lp`(가용률·예산 행)와
  독립 검증기 `core/optimize/validation.py`(목적값 재계산), 평가기 `core/evaluate/plan_eval.py`. 목적식·제약을 바꾸면 모두
  고치고 Phase 0 검증(`uv run --group benchmark python -m experiments.bench.phase0_model`)을 다시 돌려야 한다.
- **전일 투입 `allocation_mode="full"`**(2026-10-19 사용자 결정, claude-a 설계 `.omc/plan/2026-10-19-concurrency.md`): 투입률 변수 없음(a = z, 100%),
  사업 기간 가용률 1.0인 사람만 자격(`milp.full_eligible`, 문턱 `FULL_TIME_MIN_AVAILABILITY` -- 검증기·오라클은 같은 값을 따로 적는다), 같은 달 사업 수 ≤ `Person.max_concurrent`
  (기본 1, 부서장이 사람 단위로 허용한 **프로젝트 동시 수행**만 2~3, 사업마다 예산 전액). 용어는 "동시 수행" -- 회사 겸직 금지 조항 때문에 그 단어를 쓰지 않는다(사용자 지시).
  예전 fixed·monthly는 그대로(시험·옛 기록). full에서만 탐욕 초기해(`greedy.warm_start_full` → HiGHS `setSolution`), C1 보정은 건너뛴다.
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
- **운영 중 편성은 갭 0으로 푼다**(`core/evaluate/operating.exact`, 2026-10-06): 빈자리 감점(자리당 100)이 섞인 목적에서 5% 갭은
  1~2명 이동의 개선(1~2점)을 통째로 가린다(실측: 빼 오기 허용이 더 나쁜 모순). 변경 예산 제약은 `core/optimize/incremental.py` --
  옮긴 사람은 "원래 없던 사업"에 들어가야 한다(동시 수행자가 기존 동시 수행으로 조건을 채워 대기되던 빈틈, 리뷰 MUST).
  K는 **옮긴 사람 수**다(사람마다 0/1 보조 변수, 2026-10-12 -- 예전엔 빠진 배치 수라 동시 수행자 한 명이 2로 셌다).
  **추가 제약 콜백이 새 변수를 만들면 정수형(0/1)만** 쓴다: `milp`가 그 풀이 값을 고정해 `numerics.project_additive_constraints`에 넘겨야
  C1 보정·추가 행 독립 검증이 켜진다. 연속 보조 변수나 기존 변수를 바꾸면 "모델 변형"으로 보정·검증이 조용히 꺼진다(실측: 추가 행 0개).
  시연 묶음은 둘: 연초 계획 `demo/org-n100`(전원 배치)·운영 중 `demo/org-n100-operating`(90명 배치 중·대기 10명·신규 제안 2), 같은 사람.
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
  "적힌 0명 = 그 등급 금지"라 양식은 네 등급을 다 보낸다. 이력·되돌리기는 `<데이터 폴더>/workspace/`(켜기 **전에** 쓴다). `mapping.json` 묶음은 편집 불가.
  확정은 평가기로 **새로 생기거나 커진 위반**(한도와의 거리)·잠긴 배치 빼기를 거절한다. 신규 사업 후보는 등록한 정원·예산 그대로 다시 채점(`rescore_unchanged` -- 보강 계산은 자리를 늘린다),
  "끝나 가는 인력" = 지금 사업이 모두 신규 사업 **시작 전**에 끝나는 사람. 긴급 투입 확정은 서버가 계산한 증원(`apply_hint`: 정원·예산·요구 기술)을 데이터에 적는다.
  시연은 `demo/org-n100-yearend`(`scripts/make_yearend_demo.py`: 운영 중 묶음 + 4개 사업 12월 종료, 사람·평가 글 같음 → 판정 동봉 그대로).
- **야간 배치**(2026-10-18 · 10-19 재정비, claude-b `api/nightly.py`): 매일 `TEAMWEAVER_NIGHTLY_WINDOW`(기본 00:00-05:00) 시작에 **지금 데이터 한 묶음**만, **AI를 쓰지 않는다**.
  긴급 대기 명단(진행 사업마다 위반 없는 상위 5명, 설정 지문에 묶임 → `GET /api/emergency/standby`), 연간 계획(백지 데이터일 때만)·운영 중 비교(신규 사업 있을 때만)·낮 요청 기록을
  **시간 한도만 늘려** 풀고(키·서명은 낮 설정) 캐시에 넣는다 -- 이미 캐시에 있는 요청(편성 기록에서 연 결과 등)은 다시 풀지 않고, 끝까지 풀지 못한 묶음은 저장하지 않는다.
  소명 글은 확정 때 화면에서 쓰고 `api/justification_cache`(키 = 데이터 버전·명단·설정·AI·소명 재료 해시·생성기 코드 지문)가 저장해 다시 돈 내지 않는다. 점검 보고서 `GET /api/nightly`.
  `TEAMWEAVER_NIGHTLY=off`로 끈다 -- `tests/conftest.py`가 시험 전체에서 끈다. **시험에서 밤 실행·소명을 돌릴 때는 설명 AI를 막는다**(`tests/nightly/conftest.py` -- lifespan의 `load_env`가 `.env` 키를 올린다).
- **인사팀 검토 자료**(2026-10-19, claude-a `api/review_docs.py`·`routes/review_docs.py`·`core/evaluate/review_moves.py`·`web/src/review/`): 확정(`apply_auto|manual|emergency`) × 사람이 새로 들어간 사업마다 한 편.
  전 = 확정 직전 판 zip의 현재 배치, 후 = 확정 판 -- 옮겨 온 사람의 출처·원래 사업이 잃는 것·메움을 평가기로 잰다. **시스템 사실은 만들 때 계산해 자료에 저장**하고(그래서 그 확정이 지금 판일 때만 만든다),
  PUT은 사실을 받지 않는다(extra=forbid). 결론 문장은 서버가 쓰지 않는다(사용자 결정 -- 부서장 판단 칸). 해명·이의는 사실 지문 `core.kg.justify.fact_key`에 붙는다.
  "되돌려짐"은 `api.workspace.undo`가 남기는 기록(`review_docs/undone.json`)으로만 판정하고, "이력에 있음"은 확정(apply_*) 항목만 센다(시작 판 base는 재확정이 아니다) -- 다른 데이터로 바꿨거나 20판 밖으로 밀린 것은 "이력 밖"(열고 고치고 내보낼 수 있다). 긴급 투입 AI 재료는 `focus_input`(새로 온 사람만).
  승인·반려는 만들지 않았다 -- 보고서 8.4 "결재 연결(실제 도입 시)".
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

- 설정 `uv sync` · 테스트 `uv run pytest -q` (기준선: 2026-10-19 검토 자료·야간 화면 + claude-b `1ca26dc` **1908 passed, 1 skipped, 20 deselected**, `--group benchmark` 포함 · slow 20 passed, 웹 231 -- 브라우저 E2E는 `web/dist`가 최신이어야 한다(`cd web && npm run build`))
- 서비스 솔버 HiGHS(`highspy`)는 기본 의존성(2026-10-06 이전엔 benchmark 그룹에만 있어 `uv sync`가 지웠다). SCIP 포함 실행 `uv run --group benchmark ...`, tiktoken 캐시는
  `TIKTOKEN_CACHE_DIR=/private/tmp/teamweaver-tiktoken-cache`.
- 느린 E2E `uv run pytest -m slow` · API 개발 시 `TEAMWEAVER_SKIP_WARM=1`(부팅 시 ~30초 사전계산 생략).
- 웹 `cd web && npm test` · 타입체크는 **`npx tsc -b`**(`tsc --noEmit`은 프로젝트 참조 구조라 항상 통과하는 no-op).
- PDF는 `web/dist` 빌드와 `uv run playwright install chromium`이 선행돼야 한다.
