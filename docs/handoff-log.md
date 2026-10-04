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

## 2026-10-04 · Codex · C1 G0 보고서 정정 체크포인트
- 브랜치/커밋: `feat/phase1-solver-benchmark` `8a3f7d7` (main 미병합 / push 없음).
- 한 일: payload.error·exact legacy token·structured 충돌을 분류하고 core/pilot/compatibility/oracle 분모를 분리했다. 원본을 수정하지 않고 새 정정 HTML·전후 지표·ELI5를 작성했다.
- 상대 영향: 제품 코드·공유 모델 계약 변경 없음. C0 6개 budget 회귀와 기본 warm-up 거절은 아직 미해결.
- 검증: 새 테스트11 RED→13 GREEN, 리뷰 회귀3 RED→관련16 GREEN. 최고 역량 독립gpt-6-astra G0 PASS. 357건 terminal 해시 일치, core108각각/DONE21·76·72, 검증 거절0→87. 거절87 raw 부재. 기준 전체539 passed/6 failed/10 deselected 재확인. 시각QA 미수행.
- 근거: `outputs/phase1-c1-g0-eli5.html`, `outputs/phase1-c1-evidence-correction.html`, `outputs/phase1-c1-evidence-metrics.json`, `docs/superpowers/reviews/2026-10-04-c1-g0-review.md`.

## 2026-10-04 · Codex · C1 설계·구현 계획 — 독립 리뷰 PASS, 구현 전
- 브랜치/커밋: `feat/phase1-solver-benchmark` `d814c7d` (**main 미병합**, push 미실행). main `f87309c`를 반영한 `703bf4a`에서 문서 작업했다.
- 한 일: 과거 실패 증거·핵심 분모 정정과 미래 수치 복구를 분리했다. 5-task TDD 계획: 보고서 정정 → 원인/기술 후보 비교 → 비활성 미세 LP 검증 → 3솔버 증거 연결 → 서비스 회귀·제한 재비교.
  최종 tol=1e-6 유지, native/정규화/최종 후보 분리, 고정팀/native a±1e-7 복구, callback 모델 변형 fail closed, 시간 초과 거절, 전체 정책 manifest 결속을 설계했다. G1 실패면 서비스 연결하지 않는다.
- 상대 영향: **제품 코드·공유 계약·목적식·쌍 함수 변경 없음.** C0의 6개 회귀/기본 warm-up 거절은 여전히 미해결이며 main 병합 보류 유지. 실제 API lifespan smoke는 구현 G3에서 Claude에 요청할 계획이다.
- 검증: 작성자·독립 reviewer 모두 v2 `_load_run`으로 **357건 terminal 해시 결속 통과**, core DONE **CBC21/HiGHS76/SCIP72(분모108)**, legacy 거절 **CBC budget85+binary1, HiGHS budget1**, 해당 **87건 raw 부재** 확인.
  최고 역량 독립 `gpt-6-astra` 설계 리뷰 REVISE→지적4건 수정→PASS, 최종 인계 재확인 PASS. HTML 정적 검사 **3026 visible units / budget3300 PASS**, `git diff --cached --check` 통과. 시각 렌더링 QA/솔버 재실험/전체 테스트는 이번 문서 작업에서 실행하지 않았다.
- 근거: `docs/superpowers/specs/2026-10-04-c1-numerical-evidence-design.md`, `docs/superpowers/plans/2026-10-04-c1-numerical-evidence.md`, `docs/superpowers/reviews/2026-10-04-c1-design-review.md`, `outputs/phase1-c1-design-eli5.html`.
- 다음: 사용자 계획 승인 후 Task1(G0)부터 구현. 원본 423MB 결과와 private 데이터는 stage하지 않았으며 원격 다운로드 가능 상태라고 주장하지 않는다.

## 2026-10-04 · Codex · C0 안전 관문 구현 체크포인트 — 통합은 C1까지 보류
- 브랜치/커밋: `feat/phase1-solver-benchmark` `2edd3f7` (**main 미병합**, push 미실행). 작업 중 main `f87309c`의 K7 문서도 merge(`9b7a8a0`)해 반영했다.
- 한 일: `solve_milp_diagnostic`의 반환 직전에 독립 검증을 강제했다. 서비스 wrapper와 모든 대안 solve가 이를 공유한다.
  검증기는 NaN/무한대 공개값, 중복 plan entry, 비유한 재계산·제약 기준을 거절한다. 실제 CBC 0초/1초와 실패 주입 회귀 테스트 24개를 추가했다.
- 상대 영향: **기본 fixture Plan A가 budget 잔차 2~2.5e-6(tol=1e-6)로 거절되므로 정상 API warm-up 부팅이 실패한다.**
  API 코드는 수정하지 않았다. C1 수치 정책 분석 및 Claude warm-up 정책 확인이 필요하며, 해결 전 main에 통합하면 안 된다.
  `pruned_pairs`/`_overfamiliar_pairs`의 시그니처·의미·목적식 4항은 유지돼 K1 평가기 계약 변화는 없다.
- 검증: 관련 4파일 **64 passed**. 전체 `uv run --offline --group benchmark pytest -q --tb=short --disable-warnings` → **539 passed, 6 failed, 10 deselected**(6건 모두 budget 거절; 기존 테스트 기대 유지).
  최고 역량 독립 `gpt-6-astra` 최종 리뷰: safety PASS / local checkpoint ACCEPTABLE / main merge NOT_READY. HTML 정적 검사 통과; 브라우저 파일 정책으로 렌더링 QA 미수행.
- 근거: `docs/superpowers/reviews/2026-10-04-c0-independent-review.md`, `outputs/phase0-c0-eli5.html`, `docs/work-split.md` 요청 절.

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
