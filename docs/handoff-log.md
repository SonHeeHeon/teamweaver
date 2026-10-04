# Claude·Codex 작업 공유 기록 (handoff log)

두 에이전트가 **무엇을 했는지** 서로 알기 위한 공유 이력이다. 최신 항목이 위에 온다.
- 누가 무엇을 맡는지(소유 영역·작업 목록·요청)는 `docs/work-split.md`에 있다.
- 프로젝트 전체 맥락은 `docs/project-context.md`에 있다.

## 작성 규칙
- task를 끝내거나(커밋 후) 브랜치를 main에 병합하면 **맨 위에 항목을 추가한다.** 이전 항목은 고치지 않는다.
  사실이 바뀌었으면 새 항목에서 정정한다.
- 상대에게 영향이 있는 변경은 반드시 "상대 영향"에 적는다. 공유 계약 변경, 상대 코드가 import하는 함수의 시그니처나 의미 변경, API 계약, 테스트 기준선이 여기에 해당한다.
- 상세 근거는 링크로만 남긴다. Claude의 상세 리포트는 저장소 루트의 `.omc/reports/`에 있다(gitignore, 로컬 전용, worktree에는 없음).
  Codex의 상세 기록은 Git 추적 `docs/`·`outputs/`에 있다.

```
## YYYY-MM-DD · <Claude|Codex> · <한 줄 제목>
- 브랜치/커밋: <branch> `<sha>..<sha>` (main 병합 여부)
- 한 일: 1~3줄
- 상대 영향: 없음 | <무엇이 바뀌었고 상대가 무엇을 확인해야 하는지>
- 검증: <명령 → 결과>
- 근거: <문서 경로>
```

---

## 2026-10-05 · claude-b · main 병합(K4·K8·K9·K10 통합) + K13 영속화
- **main 병합**: 사용자 지시("완료되고 문제 없으면 main 병합")로 `feat/claude-b-integration`(`6a36137`)을 main에 **fast-forward**했다(`f10e710` → `6a36137`).
  - 근거: Codex 리뷰 2라운드(통합 2라운드 approve), claude-a 교차 리뷰 MUST 없음.
  - main 기준선: `uv run --group benchmark pytest -q` → 795 passed, 15 deselected. slow 15 passed.
- **K13** `feat/claude-b-persistence` `5e5c2a3`(main `6a36137` 위). **main 병합 보류**: 2라운드 리뷰의 MUST를 고친 분량이 3차 리뷰를 받지 않았다.
  - 한 일:
    - 업로드 묶음을 영속하고 부팅 때 재검증·해시 확인 후 복원한다(실패하면 fixture로 뜨고 `restore_error`를 보여 준다).
    - 적용 교체를 plan_token 키로 저장·복원한다(`PUT/GET /api/plans/edits/{token}`, 서명 검증·재생 검증, revision 순서 보장, 플랜별 파일, 다른 데이터셋 기록 정리).
    - 플랜 서명키를 고정한다(`TEAMWEAVER_PLAN_SECRET` 또는 `plan_secret` 파일).
  - claude-a 교차 리뷰 반영:
    - S1: 교체 없는 PDF도 지표를 서버가 다시 계산한다.
    - S2: 변환 예외를 리포트에 싣는다.
    - S3: 버전 검사를 dist 검사보다 먼저 한다.
    - L1: reset은 JSON 요청만 받는다.
    - L2: 결과 캐시는 LRU 32개다.
  - 상대 영향:
    - **claude-a**: `/api/datasets/reset`은 이제 JSON 요청만 받는다. `api/main.py` lifespan이 데이터 폴더(`TEAMWEAVER_DATA_DIR`, 기본 `~/.teamweaver`)를 읽는다.
    - tests/api는 세션·테스트마다 데이터 폴더를 임시로 돌린다.
    - `ResultCache`는 32개 LRU다.
  - 검증:
    - `uv run --group benchmark pytest -q` → 818 passed, 16 deselected. slow 16 passed(재기동 E2E 포함).
    - 웹 vitest 107, tsc·lint·build 통과. `~/.teamweaver`는 생성되지 않았다.
    - 결함 주입 23종이 모두 검출됐다(일부는 테스트를 보강한 뒤 검출).
  - 리뷰: Codex 한도 소진(09:03 회복)으로 Claude Opus 폴백 2라운드를 받았다.
    - 1라운드 MUST 1(PUT 순서), SHOULD 5를 반영했다.
    - 2라운드 MUST 1(revision 시각 기준), SHOULD 1을 반영했다. **이 반영분은 3차 리뷰 전이다.**
- 교차 리뷰(claude-b → claude-a): `feat/claude-a-review-rounds` MUST 없음(노트 문구 SHOULD 1 → claude-a가 `3c24bc6`에서 반영). `feat/claude-a-k6-int8` MUST·SHOULD 없음.
- 근거: `.omc/reports/2026-10-05-k13-persistence.md`, `.omc/reports/2026-10-05-xreview-claude-a-review-rounds.md`

## 2026-10-05 · claude-b · 통합 브랜치 실브라우저 E2E
- 브랜치/커밋: `feat/claude-b-integration`의 테스트 커밋(아래 기록 직전). main 병합은 사용자 승인 후.
- 한 일: slow 테스트 `tests/api/test_ui_e2e.py`를 추가했다.
  - 빌드된 웹을 실제 Chromium으로 조작한다: 최소 투입률 25% 저장 → 가상 20명/4프로젝트 zip 업로드(새 meta 수신까지 대기) → 최적화 → 교체 검토·적용 → PDF 내려받기.
  - PDF 텍스트에서 다음을 단언한다: 적용 교체 1건과 그 행의 업로드 인력 이름, "서명 확인"(optimize의 plan_token이 PDF에서 검증됨), "최소 투입률 25%". 브라우저 콘솔 오류는 0건이어야 한다.
- 상대 영향: 없음. slow 기준선 15 passed(전체 795 passed, 15 deselected).
- 검증: E2E 3회 연속 통과(각 약 2.5초). 웹이 plan_token을 보내지 않게 하는 결함 주입에서 실패하는 것을 확인했다.
- 리뷰: Codex 한도 소진(09:03 회복 예정, Stop 훅 게이트 끔)이라 Claude Opus 폴백 2라운드를 받았다. MUST는 없었고 1라운드 SHOULD 3건을 반영했다.
  - 반영: 경고 없는 경로를 결정적으로 단언, 전환 후 meta 대기, 업로드 인력 이름 단언.

## 2026-10-05 · claude-b · K4를 K8→K9→K10 줄기에 통합(병합 후보 한 줄)
- 브랜치/커밋: `feat/claude-b-integration` `095e290`(K10 `2783206` + K4 `1217f38` 병합) + 이 기록. main 병합은 사용자 승인 후.
  - **main에 넣을 때는 이 브랜치 하나만 병합하면 된다**(K4·K8·K9·K10이 모두 들어 있다). 개별 브랜치는 기록용이다.
- 한 일:
  - K4(PDF Host 신뢰 제거)와 K9·K10이 함께 고친 `api/routes/report.py`의 충돌을 풀었다. 순서는 다음과 같다.
    - 값싼 검사(데이터셋 버전, 원 플랜 서명)를 먼저 한다.
    - 그다음 동시성 슬롯을 잡는다.
    - [교체 재계산 + 리포트 meta 생성(전용 스레드 풀) → 내부 origin 렌더]를 하나의 시간 상한 안에서 돌린다.
  - 시간 초과로 응답이 끝나도 준비 스레드가 끝날 때까지 슬롯을 유지한다(파이썬 스레드는 강제로 멈출 수 없다).
  - PDF meta는 리포트에 필요한 사람·프로젝트·협업선만 담는다. 최종 렌더 데이터는 4MiB까지다(413).
- 상대 영향: 없음(claude-b 영역). 테스트 기준선 **795 passed, 14 deselected**.
- 검증:
  - `uv run --group benchmark pytest -q` → 795 passed. `uv run pytest -m slow -q` → 14 passed.
  - 웹: vitest 97 passed, `tsc -b`·lint·build 통과.
  - 결함 주입 7종이 모두 테스트에 걸렸다.
- 리뷰: Codex 적대적 리뷰 2라운드. 1라운드 high 2(시간 초과 후 슬롯 조기 반납, meta가 크기·시간 상한 밖)를 반영했다. 2라운드는 approve.
- 근거: `.omc/reports/2026-10-05-k4-integration.md`

## 2026-10-05 · claude-b · K10 교체 "검토 → 적용" 흐름
- 브랜치/커밋: `feat/claude-b-swap-apply` `dc0ed9e`(코드) + 이 기록. **K9 브랜치 위**(K8 → K9 → K10 순서로 병합). main 병합은 사용자 승인 후.
- 한 일:
  - `POST /api/plans/apply-swap`: 교체 후 명단 전체를 `core.evaluate.plan_eval`로 다시 평가하고, 충족률·최적화율·미충원을 다시 계산한다(stateless).
    - 교체 규칙은 `/api/whatif`와 같은 함수를 쓴다.
    - 위반이 있는 명단은 최적화율을 `None`(산정 불가)으로 둔다.
  - 웹:
    - 검토 결과 아래에 "이 교체 적용"을 둔다. 새 위반·미충원이 있으면 페이지 안에서 "위반을 알고 적용"을 한 번 더 누르게 한다.
    - 플랜별 적용 스택을 둔다(마지막 적용 취소, 원래 플랜으로). 적용 후 다음 검토는 적용된 명단을 기준으로 한다.
    - 교체 선택을 바꾸면 이전 검토를 버린다. 적용 요청은 별도 세대로 관리한다(늦은 응답·409 무시).
  - PDF:
    - 원 플랜 명단과 교체 순서(id)만 받는다. 서버가 다시 적용해 명단·지표·교체별 Δ·경고·최종 위반을 계산한다.
    - 교체는 최대 50건, 명단은 최대 5,000건이다. LP 상한은 요청당 1회이고, 재계산은 워커 스레드에서 돈다.
  - 원 플랜 서명: `/api/optimize`의 plan 이벤트에 `plan_token`(HMAC; 데이터셋·라벨·명단·가중치·파라미터)을 싣는다.
    - PDF는 서명을 검증한다. 일치하면 "서버 계산 확인", 없으면 "미검증"으로 표시하고, 틀리면 422다.
    - 비밀키는 `TEAMWEAVER_PLAN_SECRET`이고, 없으면 프로세스마다 무작위로 만든다.
- 상대 영향:
  - **claude-a**: `core.evaluate.plan_eval.evaluate_plan`을 import만 했다(수정 없음).
    - 평가 결과의 위반·미충원 문장이 화면·PDF 경고로 나간다. 문장이 바뀌면 `api/routes/plans.swap_warnings`·`web/src/api/whatifWarnings.ts`를 확인해야 한다.
  - **API 계약**:
    - `ReportRequest`: `applied_swaps`(id 목록), `base_entries`, `weights`, `plan_token`이 추가됐다. `optimization_ratio`는 null을 허용한다.
    - plan 이벤트에 `plan_token`이 추가됐다.
  - **테스트 기준선**: 743 passed, 11 deselected(slow PDF 테스트 1개 추가).
- 검증:
  - `uv run --group benchmark pytest -q` → 743 passed. `uv run pytest -m slow -q` → 11 passed.
  - 웹: vitest 97 passed, `tsc -b`·lint·build 통과.
  - 결함 주입 21종이 모두 테스트에 걸렸다. 처음 공허했던 테스트 3개(되돌리기 스택, 설정 변경, 교체 상한)는 보강했다.
- 리뷰: Codex 적대적 리뷰 2라운드.
  - 1라운드 high 2·medium 2, 2라운드 high 2를 모두 반영했다.
  - 2라운드 반영분은 3라운드 리뷰를 받지 않았고 결함 주입으로 확인했다.
- 근거: `.omc/plan/2026-10-05-k10-swap-apply.md`, `.omc/reports/2026-10-05-k10-swap-apply.md`

## 2026-10-05 · claude-b · K9 CSV 묶음 업로드 → 활성 데이터셋 전환
- 브랜치/커밋: `feat/claude-b-dataset-upload` `ebcb140`(코드) + 이 기록. **K8 브랜치(`feat/claude-b-milp-settings` `d936415`) 위에서 갈라 만들었다**. K8을 먼저 병합해야 한다. main 병합은 사용자 승인 후.
- 한 일:
  - `POST /api/datasets`: zip 원본 본문을 받아 `core.ingest.load_bundle`·`to_dataset`으로 검증한다.
    - 오류가 있으면 422와 리포트(오류·경고·노트·행 수)를 돌려주고 전환하지 않는다. 통과하면 활성 데이터셋을 통째로 교체한다.
    - 관련 엔드포인트: `GET /api/datasets/active`, `POST /api/datasets/reset`, `GET /api/admin`.
  - zip 안전 검사: 경로 탈출, 링크, 이상 파일, 중앙 디렉터리 레코드 수, 해제 크기. 업로드는 20MiB까지(413)이고 처리 중이면 409다.
  - 실데이터를 디스크에 남기지 않는다.
    - 해제 폴더는 요청이 끝나면 삭제한다.
    - SQLite는 메모리 DB로 복사하고 임시 파일은 바로 지운다.
    - 물러난 데이터셋은 마지막 요청이 끝날 때 닫는다.
  - 캐시 키에 데이터셋 내용 해시를 넣었다.
  - `meta`·plan 이벤트에 `dataset_version`을 싣는다. optimize·whatif·report는 버전이 다르면 409를 낸다. PDF 데이터에는 요청이 잡은 데이터셋의 meta를 넣는다.
  - `TEAMWEAVER_ADMIN_TOKEN`이 있으면 업로드·되돌리기·설정 저장에 `X-Admin-Token`을 요구한다.
  - 웹 "데이터" 탭을 추가했다. 전환하면 플랜과 가중치를 초기화하고, 409를 받으면 화면을 다시 불러온다.
- 상대 영향:
  - **claude-a**: `core.ingest`를 수정하지 않고 그대로 import했다. 고칠 점은 발견하지 못했다(요청 없음).
    - `api/deps.get_graph`·`get_sqlite_conn`은 이제 `get_dataset`(async, 요청당 1회)에서 나온다. `api/rag/**`는 같은 의존성을 쓰면 된다.
    - `/api/meta.review_items`는 여전히 fixture 목록이다.
  - **API 계약**:
    - `ResultCache.key`에 `dataset_version` 인자가 추가됐다.
    - `MetaResponse`에 `dataset_version`이 추가됐다.
    - optimize·whatif·report 요청에 선택 필드 `dataset_version`이 생겼다. 보내지 않으면 검사하지 않는다.
  - **테스트 기준선**: 727 passed, 10 deselected.
- 검증:
  - `uv run --group benchmark pytest -q` → 727 passed. `uv run pytest -m slow -q` → 10 passed.
  - 웹: vitest 83 passed, `tsc -b`·lint·build 통과.
  - 실제 CBC 확인: 가상 50명/10프로젝트와 40명/8프로젝트를 업로드해 Plan A·B가 나왔다.
  - 결함 주입 25종 가운데 24종이 테스트에 걸렸다. 나머지 1종은 이중 방어라 단독으로 제거하면 검출되지 않는다.
- 리뷰: Codex 적대적 리뷰 2라운드(04:03 쿼터 회복 후). 1라운드 high 3·medium 1, 2라운드 high 1·medium 2를 모두 반영했다.
  - 시스템 전체 인증은 사용자 결정 사항으로 남겼다.
  - 2라운드 반영분은 3라운드 리뷰를 받지 않았고 결함 주입으로 확인했다.
- 근거: `.omc/plan/2026-10-05-k9-dataset-upload.md`, `.omc/reports/2026-10-05-k9-dataset-upload.md`

## 2026-10-05 · claude-b · K8 관리자 배치 설정 화면
- 브랜치/커밋: `feat/claude-b-milp-settings`(main `f10e710`=K2 병합 후에서 갈라 만듦). main 병합은 사용자 승인 후.
  - K4(`feat/claude-b-pdf-origin`, main `80db4da` 기준)와 `api/main.py`·`api/schemas.py`를 함께 고친다. 병합할 때 충돌을 확인할 것.
- 한 일:
  - `GET/PUT /api/settings`를 추가했다. 서버 JSON 파일(`TEAMWEAVER_SETTINGS_PATH`, 기본 `~/.teamweaver/settings.json`)에 관리자 배치 설정을 저장한다.
    - 항목: 최소 투입률 **기본 30%**, 반복 협업 기준, 협업 가중, 반복 협업 감점, 시간 한도, gap.
    - PUT에는 `based_on`(읽은 시점)을 함께 보낸다. 그사이 다른 사람이 저장했으면 409로 거부한다.
  - 웹 "배치 설정" 탭을 추가했다. 실행할 때마다 설정을 다시 읽어 `/api/optimize`의 `milp_params`로 보낸다.
    - 그때의 설정과 가중치를 스냅숏으로 남긴다. `/api/whatif`와 PDF는 그 스냅숏을 쓰므로, 플랜과 교체 점수가 같은 기준이 된다.
    - 설정을 바꾼 뒤에는 "이전 설정으로 계산됨(바뀐 항목)" 안내를 띄운다. PDF에는 계산 기준을 한 줄로 적는다.
  - `milp_params` 요청 계약을 `MilpParamsIn`으로 정했다(범위 검사, `extra=forbid`). 오타 키와 범위 밖 값은 422다.
  - 캐시 키는 적용된 파라미터 전체로 만든다. 부팅 사전계산은 저장된 설정으로 하되, `time_limit ≤ 120`이고 `gap ≥ 5%`일 때만 한다.
- 상대 영향:
  - **Codex(C6)**: `MilpParams` 기본값(0.2)은 그대로 뒀다. 30%는 설정 계층의 기본값이다.
    - `MilpParams`에 필드를 추가하면 `api/schemas.py::MilpParamsIn`도 같이 늘려야 한다. 테스트 `test_milp_params_in_mirrors_every_model_field`가 이를 알려 준다.
    - 관리자 화면에 노출할지는 `api/settings.py::PlacementSettings`에서 정한다.
  - **API 계약**: `/api/optimize`·`/api/whatif`에 알 수 없는 `milp_params` 키를 보내면 이제 422다(예전에는 조용히 무시).
  - **테스트 기준선**: 691 passed, 10 deselected(648 + 43). `tests/api/conftest.py`가 설정 파일 경로를 테스트마다 임시 폴더로 돌린다.
- 검증:
  - `uv run --group benchmark pytest -q` → 691 passed, 10 deselected. `uv run pytest -m slow -q` → 10 passed.
  - 웹: `npx vitest run` 71 passed, `npx tsc -b`·`npm run lint`·`npm run build` 통과.
  - 결함 주입 13종 중 12종이 테스트에 걸렸다. 나머지 1종은 결과가 원래 코드와 같은 변형이다.
- 리뷰: Codex는 사용 한도 소진(04:02까지)이라 Claude Opus 적대적 리뷰를 2라운드 받았다. MUST는 없었다. SHOULD 8건과 NIT 다수를 반영했다.
- 근거: `.omc/plan/2026-10-05-k8-milp-settings.md`, `.omc/reports/2026-10-05-k8-milp-settings.md`(루트, gitignore)

## 2026-10-05 · claude-a · K2 CSV 입력 계약 v0
- 브랜치/커밋: `feat/claude-a-csv-ingest` (`5bce379` 읽기·검증, `0cad783` 변환, 생성기·진입점 커밋) — main 병합은 사용자 승인 후.
- 한 일: 실제 인사 자료 모양의 CSV 묶음(사람·단가표·기술 경력 개월·업무이력·월별 가용 M/M·프로젝트·등급/기술 요구·리뷰·리뷰 항목 + manifest, 선택 mapping.json)을
  읽어 검증 리포트를 만들고 현행 `Dataset`으로 변환한다. 같은 모양의 가상 묶음 생성기와 `python -m core.ingest generate|check`를 추가했다.
- 상대 영향:
  - **Codex**: 경력 개월 기반 점수와 리뷰 회차 확장을 work-split "요청"에 올렸다(공유 계약). 그 전까지 변환은 개월→1~5 대리 레벨, 최신 리뷰 회차만 쓴다.
    가상 묶음(`--people 40 --projects 8 --seed 7`)에서 CBC 원시 해의 예산 ~1.5e-6 초과가 재현된다(C1 참고).
  - **claude-b**: K8(설정 화면)이나 업로드 화면이 필요하면 `core.ingest.load_bundle`/`to_dataset`을 import해 쓴다(파일 수정 불필요).
  - 테스트 기준선이 늘었다(아래).
- 검증: tests/ingest 102개, 전체 648 passed·10 deselected. 결함 주입으로 검출력 확인(빠진 달 채우기, 중복 키 누락, 협업 이중 계산, 오래된 회차 선택).
  가상 50/100/300명 생성→변환 0.2초 이내, 오류 0.
- 근거: `.omc/plan/2026-10-05-k2-csv-ingest.md`, `.omc/reports/2026-10-05-k2-csv-ingest.md`

## 2026-10-05 · claude-b · K4 PDF 생성의 Host 신뢰 제거 (감사 [A-P1])
- 브랜치/커밋: `feat/claude-b-pdf-origin` `bd70251`(코드) + 이 기록. main 병합은 사용자 승인 후.
- 한 일:
  - `/api/report`의 PDF 브라우저 주소를 요청 Host 헤더가 아니라 `TEAMWEAVER_PDF_ORIGIN` 또는 실제로 연결을 받은 소켓 주소(`scope["server"]`)에서만 만든다.
  - 브라우저 요청을 그 origin으로만 허용한다. 웹소켓·service worker는 막는다. 데이터 주입 스크립트는 그 origin 문서에서만 동작한다.
    - 한계: 내부 origin이 302로 외부를 가리키면 그 한 번은 나간다(route가 리다이렉트 다음 단계를 못 본다). 탐지해서 렌더를 실패시키고, 데이터는 주입되지 않는다. 앱에는 열린 리다이렉트가 없다.
  - 자원 상한(환경변수로 조정): 본문 2 MiB(413, JSON 파싱 전), 동시 생성 2건(429, 대기열 없음), 전체 60초(504).
    - 기준: 300명/60프로젝트 합성 데이터 실측. 최악치 1,500건이 본문 143KB, 렌더 0.74초였다.
  - 500 응답에 예외 원문(내부 URL 포함)을 싣지 않는다. 원문은 로그에만 남긴다.
- 상대 영향: 없음(claude-b 영역만 수정).
  - 운영 참고: TLS를 uvicorn이 직접 받거나 유닉스 소켓으로 띄울 때는 `TEAMWEAVER_PDF_ORIGIN`이 필요하다.
  - 동시성 상한은 프로세스별로 센다(`--workers N`이면 N배).
- 검증: `uv run --group benchmark pytest -q` → **593 passed, 13 deselected**(기준선 546 + 새 테스트 47). `uv run pytest -m slow -q` → 13 passed.
  - 결함 주입 15종(Host 신뢰 복귀, route·ws 가드 제거, root_path 미처리 등)이 모두 테스트에 걸린다.
- 리뷰: Codex는 2회 모두 쿼터 소진이었다. 그래서 Claude Opus 적대적 리뷰를 2라운드 받았다. 1라운드 지적 6건(root_path 우회 등)과 2라운드 NIT 4건을 반영했다. 남은 MUST는 없다.
- 근거: `.omc/reports/2026-10-05-k4-pdf-origin.md`(루트, gitignore)

## 2026-10-05 · claude(채팅 세션) · 3인 체제 문서 초안: claude-a·claude-b·codex 영역 분리
- 브랜치/커밋: 커밋하지 않았다. 루트 작업 트리의 미커밋 변경이며, 사용자가 검토하고 승인한 뒤 main에 커밋한다.
- 한 일: `docs/work-split.md`에 "에이전트와 작업 위치" 절을 추가하고 Claude 영역을 claude-a(입력·평가·근거)와 claude-b(API·웹·스크립트)로 나눴다. `CLAUDE.md`·`AGENTS.md`의 "두 에이전트" 문구를 세 에이전트로 고쳤다.
- 상대 영향: **Codex**: Claude 영역이 둘로 갈라졌다. `api/rag/**`는 claude-a, 나머지 `api/**`는 claude-b다. 코드 변경은 없다.
- 검증: 문서만 바뀌었다. 영역 경로를 실제 디렉터리와 대조했다(`core/ingest/`는 K2에서 만들 예정이라 아직 없다).
- 미결: 영역 배분은 초안이다. 사용자가 확인하면 확정한다.

## 2026-10-05 · claude-a · 3인 체제 확정 + 스키마 양식 v2 main 병합
- 브랜치/커밋: `feat/claude-intake-screens`(`fae6230` 양식 v2, `d9f7c87` 3인 체제 확정, 리뷰 반영 커밋)를 사용자 승인으로 **main에 fast-forward 병합**.
- 한 일:
  - 다른 Claude 세션이 남긴 3인 체제 분담 초안(`docs/work-split.md`·`AGENTS.md`·`CLAUDE.md`)을 사용자 확정으로 커밋했다. claude-b 작업 K8(관리자 배치 설정 화면)을 추가했다.
  - Codex 쿼터 소진으로 양식 v2를 Claude Opus 적대적 리뷰 2라운드로 검토했다. 지적 12건을 반영했다.
    - 1라운드 8건: 저장소 읽기 실패 중 불러오기·내보내기 차단, 손상 초안 보호, 여러 탭 덮어쓰기, 비우기 경합, 용량 상한 일치, 글자 저장 실패 안내, 추출 입력 보호
    - 2라운드 4건: Web Lock 탭 잠금, file:// 안전성, 복원 오류 안내, 중지 상태 상시 표시·닫기 경고
- 상대 영향: `scripts/**`는 이제 claude-b 영역이다(`scripts/extract_intake_images.py` 포함). 테스트 기준선 **546 passed, 10 deselected**.
- 검증: pytest 546, 추출 테스트 25. Playwright 10묶음을 Chromium의 file://로 돌렸고 모두 통과했다. Safari는 미검증이다.

## 2026-10-05 · Claude(claude-a) · 실데이터 스키마 답변 수령 + 숙련도 결정
- 브랜치/커밋: `feat/claude-intake-screens` (main 병합은 사용자 승인 후)
- 한 일: 사용자 답변을 `private/schema-intake.json`(글자 답만, 개인 식별 정보 없음)에 정리했다. 기술 이력 시스템 구성은 `parts.skill.answers.fields`에 있다.
- 결정(사용자): **숙련도 레벨은 없는 것으로 하고 기술별 경력 연수(누적 수행기간)로만 계산한다.**
- 상대 영향: 현재 S(보유 레벨/요구 레벨)와 가상 데이터의 1~5 레벨 가정이 실제와 다르다. 점수(`core/scoring`)와 datagen은 공유 계약이므로,
  바꾸기 전에 `docs/work-split.md` "요청"에 설계를 올린다. 배치 규칙 답(`parts.rules`)은 C6의 입력이다.
  - 최소 투입률 30%(관리자 설정 가능), 동시 프로젝트 최대 3개, 월별 투입률 변경 허용.
  - 필요 기술은 선호(점수 반영), 요청에 없는 등급도 허용 → 감사 [A-P1] 미기재 등급 항목은 현재 동작이 의도에 맞는다.
- 근거: `private/schema-intake.json`(gitignore), `docs/data-schema/README.md`

## 2026-10-04 · Claude · 스키마 양식 v2: 이미지 첨부 지원
- 브랜치/커밋: `feat/claude-intake-screens` (main 병합은 사용자 승인 후)
- 한 일:
  - 기술 이력 영역의 "입력 항목 전체 목록" 표를 이미지 첨부 칸(`skill.screens`: 파일 선택·끌어놓기·붙여넣기, 이미지별 설명)과 `skill.screen_notes`로 바꿨다.
  - JSON 양식 버전을 2로 올렸다. 버전 1 초안·파일도 불러오며, 없어진 질문의 답은 보존한다.
  - 자동 저장을 IndexedDB(이미지 포함)와 localStorage(글자 즉시 저장)로 나눴다. 이미지가 localStorage 한도(~5MB)를 넘고, 탭을 닫는 순간의 비동기 저장은 끝나지 않기 때문이다.
  - `scripts/extract_intake_images.py`를 추가했다. JSON에 첨부된 이미지를 파일로 꺼낸다.
- 상대 영향: JSON의 `parts.skill.answers.items`는 버전 1 초안에만 있을 수 있다.
- 검증: 추출 스크립트 pytest 24개. Playwright로 다음을 확인했다.
  - 버전 1 초안 이전, 이미지 추가·붙여넣기·삭제·설명, 큰 이미지 자동저장, 떠나기 직전 입력 보존
  - 내보내기→새 세션 불러오기, 잘못된 이미지 거부, 추출 왕복, 모바일 넘침 0, 페이지 오류 0
- Codex 적대적 리뷰 2라운드를 반영했다. 2라운드 반영분은 3라운드 리뷰를 받지 않았고(규칙상 최대 2라운드), 재현 시험과 결함 주입으로만 확인했다.
  - 이미지 ID 기반 병합(다른 초안 혼합·삭제 부활 방지), 복원 중 입력 잠금, 형식·시그니처·디코딩 검사
  - IndexedDB 읽기 실패 시 덮어쓰기 차단과 안내, 읽는 중 불러오기 혼합 차단, 중복 ID 재발급
  - 추출 폴더 원자적 교체·롤백·소유 표시
- 근거: `docs/data-schema/README.md`

## 2026-10-04 · Claude · K7 main 병합
- 브랜치/커밋: `feat/claude-schema-intake`(`30c08ae` `2dacab6` `edf5bbf` + 이 기록)를 사용자 승인으로 **main에 fast-forward 병합**.
- 한 일: 스키마 입력 양식의 불러오기를 엄격하게 바꿨다(Stop 훅 지적 2건 반영). 답 하나라도 형식이 틀리면 파일 전체를 거부하고 초안과 자동저장을 보존한다.
- 상대 영향: 없음. 양식과 문서만 바뀌었다. 답변 `private/schema-intake.json`은 사용자가 작성 중이다.

## 2026-10-04 · Claude · K7 실데이터 스키마 입력 양식
- 브랜치/커밋: `feat/claude-schema-intake` (아래 병합 항목 참고)
- 한 일: 사용자가 기술 이력 시스템·피어 리뷰의 실제 항목·형식·의미·선택지·대략적 분포와 배치 규칙을 채우는
  로컬 HTML 양식(`docs/data-schema/schema-intake.html`, 6개 영역 45문항)을 만들었다. 공용 안내(`docs/data-schema/README.md`)도 작성했다.
  답변은 `private/schema-intake.json`(gitignore)에 둔다.
- 상대 영향: **Codex도 이 JSON을 읽는다.** `parts.rules.answers.*.answer`는 C6(필수 기술·미기재 등급·최소 투입률)의 입력이고,
  `parts.outside.answers.scale.answer`는 Phase 1 규모 가정의 입력이다. `.gitignore`에 `private/`를 추가했다. 이 폴더는 커밋·push 금지다.
- 검증: Playwright(Chromium)로 입력 → JSON 내보내기 → 새로고침 복원 → 다운로드 → 새 세션 불러오기 →
  모바일 390px 가로 넘침 0, 페이지 오류 0을 확인했다.
- 근거: `docs/data-schema/README.md`, `.omc/reports/2026-10-04-schema-intake.md`

## 2026-10-04 · Codex · main 반영
- 브랜치/커밋: `feat/phase1-solver-benchmark` `35e7e5c`(main `d416304`를 merge)
- 한 일: 분담·공유 문서와 K1을 Codex 브랜치에 반영했다(Claude가 git 이력으로 확인해 대신 기록함).

## 2026-10-04 · Claude · 공유 기록 도입 + K1 main 병합 + Codex 중간 감사 담당 배정
- 브랜치/커밋: `feat/claude-whatif-contract` → **main에 fast-forward 병합**(이 항목이 들어간 커밋까지).
- 한 일: 이 문서를 만들었다. `AGENTS.md`·`CLAUDE.md`에 "작업 전 이 문서 읽기, 작업 후 항목 추가" 규칙을 추가했다.
  Codex 중간 품질 감사(`bf50a7e`)의 발견 사항에 `docs/work-split.md` 기준으로 담당을 배정했다(같은 문서의 작업 목록 참고).
- 상대 영향: **Codex의 `feat/phase1-solver-benchmark`(`bf50a7e`)는 분담 이전 지점 `f2c6252`에서 갈라져 있다.**
  다음 작업 전에 main을 merge 또는 rebase해야 분담 문서와 K1이 보인다. 겹치는 파일은 없다(`bf50a7e`는 HTML 1개 추가).
- 근거: `docs/work-split.md`

## 2026-10-04 · Claude · K1 What-if 계약 정정
- 브랜치/커밋: `feat/claude-whatif-contract` `a034250` `1d4bb7f` `763d543` `c7e252b` (main 병합됨, 위 항목 참고)
- 한 일:
  - 신규 `core/evaluate/plan_eval.py`: 임의의 표시용 배치를 MILP 전체 목적 4항으로 재계산하고 위반(가용률·예산·등급 초과·투입률 범위)과 등급 미충원을 보고한다.
  - `/api/whatif`가 교체 전후를 이 평가기로 재평가한다. 웹과 PDF는 "현행 점수 기준 변화(참고·재최적화 아님)"와 경고를 표시한다.
  - Codex 감사의 **P2 "what-if 점수가 전체 목적과 다름"이 이 작업으로 해소**됐다.
- 상대 영향:
  - `core/evaluate/plan_eval.py`가 Codex 소유인 `core/optimize/milp.py`의 `pruned_pairs`, `_overfamiliar_pairs`를 **import한다.**
    두 함수의 시그니처나 의미를 바꾸거나 목적식에 항을 추가하면 "요청"에 적어 Claude에 알려야 한다. 그렇지 않으면 What-if 점수가 MILP와 어긋난다.
  - API 계약 변경:
    - `WhatifResponse`에 `before`·`after`·`new_violations`·`new_shortfalls`·`feasible` 필드가 추가됐다. `objective_delta`의 의미는 전체 목적 차이로 바뀌었다.
    - `ReportRequest.swap_violations`가 추가됐다.
    - in 사람이 이미 같은 프로젝트에 있거나 entries에 알 수 없는 ID가 있으면 422를 낸다.
  - 테스트 기준선 변경: Python **521 passed, 10 deselected**(기존 497). slow 10 passed. 웹 49 passed.
- 검증: 위 테스트, `tsc -b`, 빌드, oxlint, 결함 주입 3종, 실서버 스모크. Codex 리뷰 4회(SHOULD 2건 반영).
- 근거: `.omc/plan/2026-10-04-k1-whatif-contract.md`, `.omc/reports/2026-10-04-k1-whatif-contract.md`

## 2026-10-04 · Codex · 중간 품질 감사 (읽기 전용)
- 브랜치/커밋: `feat/phase1-solver-benchmark` `bf50a7e` (**main 미병합**, 기준 소스 `f2c6252`)
- 한 일: 모델·API·실험·보고서를 읽기 전용으로 검수했다. P0 2건, P1 4건, P2 6건과 양호한 기반 3건을 정리했다. 코드는 변경하지 않았다.
- 상대 영향: 발견 사항 중 Claude 영역(PDF Host 신뢰, 리뷰 근거 숫자)이 있다. 담당 배정은 `docs/work-split.md` 작업 목록에 있다.
- 근거: `outputs/eli5-mid-project-quality-audit.html` (phase1 브랜치)

## 2026-10-04 · Claude · 파일 영역 분담 도입
- 브랜치/커밋: main `3df8399`
- 한 일: `docs/work-split.md`(소유 영역·작업 목록·요청 절)와 `AGENTS.md`(Codex 진입점)를 추가했다.
- 상대 영향: Codex 소유는 `core/optimize/**`, `experiments/**`, `outputs/phase*`, `docs/superpowers/**`다. 그 밖의 파일은 Claude 소유이거나 공유 계약이다.
- 근거: `docs/work-split.md`, `.omc/plan/2026-10-03-claude-codex-work-split.md`

## 2026-10-03 · Claude · Phase 0/1 main 병합 + 세션 지도
- 브랜치/커밋: `feat/phase1-solver-benchmark`(`0bc4c7c`까지) + `f2c6252`를 **main에 fast-forward 병합**(`9cc3021`→`f2c6252`). 병합 후 497 passed.
- 한 일: `CLAUDE.md`(세션 지도·함정)와 `docs/project-context.md`(전체 해설 HTML의 압축본)를 추가했다.
- 상대 영향: 없음. 문서만 추가했다.
- 근거: `docs/project-context.md`, `.omc/reports/2026-10-03-project-context-onboarding.md`

## 2026-09-18 ~ 10-03 · Codex · Phase 0 모델 검증 + Phase 1 솔버 비교
- 브랜치/커밋: `feat/phase0-model-validation`(`c22ca5a`) → `feat/phase1-solver-benchmark`(`0bc4c7c`). 10-03에 main에 병합됐다.
- 한 일:
  - 원시 해 계약, 독립 검증기(`core/optimize/validation.py`), 소형 정답기를 만들었다. Phase 0 결과는 11/11 PASS다.
  - CBC/HiGHS/SCIP 357케이스 동결 sweep을 완료했다. HiGHS가 잠정 기본 후보이고, 300/60 규모는 서비스 준비가 안 된 상태다.
  - 프로젝트 전체 해설 HTML을 작성했다.
- 상대 영향: `core/optimize/milp.py`에 `solve_milp_diagnostic`(원시 해 반환)이 추가됐다. `solve_milp`는 호환을 유지한다.
- 근거: `docs/phase1-checkpoint.md`, `docs/superpowers/specs/`, `outputs/phase1-solver-benchmark.html`, `outputs/eli5-project-history-roadmap.html`

## 2026-08-02 ~ 08-21 · Claude · Plan 1~5 (제품 시제품)
- 브랜치/커밋: main (`5d91490` ~ `c799f75`)
- 한 일: 코어(도메인·datagen·점수·Greedy/MILP·대안), 실험 1~5와 Neo4j 제거 결정, FastAPI(SSE·What-if·XAI·PDF), React 웹을 만들었다.
- 근거: 루트 `.omc/plan/`, `.omc/reports/` (로컬), 요약은 `docs/project-context.md` 4~5절
