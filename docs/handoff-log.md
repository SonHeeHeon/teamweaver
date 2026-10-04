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
