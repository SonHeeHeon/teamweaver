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

## 2026-10-09 · claude-a · 인사팀 소명 글 GraphRAG 서비스 모듈 + 확대 검증 E7b
- 브랜치/커밋: `feat/claude-a-graphrag-justify` `be6c902`(모듈·시험·측정 스크립트, 측정 전 커밋) + 결과·문서 커밋(skill-dictionary 위, main 병합은 사용자 승인 대기)
- 한 일: `core/kg/justify.py`(사실 F1…·템플릿·검사·채우기) + `api/rag/justification.py`(AI 호출·대체). 측정 전 설계 변경 2회(폴백 리뷰 5회): 자유 문장 대조 → 자리표시+금지 목록 →
  **자리표시 + 연결 말 허용 목록 문법**(AI는 사실을 고르고·순서·묶기만, 숫자·충족/미달·사람-사실 짝은 서버가 데이터 그대로 채움). E7b(618회, $0.50): G1 통과(주입 오류 9,309/9,309, 무결성 실패 0),
  **G2 조건부 94.7%**(탈락 대부분 주제 라벨-사실 종류 불일치·일본어 글자 섞임 → 지시문 개선 후보), 불리한 사실 인용률 92~98%, 글 길이는 템플릿과 비슷(2.3~3.0천 자 vs 2.9~4.2천 자).
- 상대 영향: **claude-b 계약** `docs/requests/2026-10-09-hr-justification.md`(함수 호출·출력 키·`POST /api/justification` 제안·화면·PDF, `llm_text`는 화면에 보이지 말 것, `facts[].adverse` 표시).
  `core/kg/views.project_evidence`의 요구 행에 `implied_from`(부분 인정 출처) 추가. 시험 기준선 1510 → **1583**(이 브랜치).
- 검증: `uv run pytest -q` → 1583 passed, 1 skipped, 19 deselected · `uv run python -m rehearsal.justify_scale` → G1 pass, G2 0.9466 조건부
- 근거: `rehearsal/results/justify-scale.html`(원자료 `.json.gz`), 사전 등록 `rehearsal/justify_scale.py` docstring

## 2026-10-09 · claude-a · 사용자 결정: 인사팀 소명은 GraphRAG
- 브랜치/커밋: `feat/claude-a-skill-dictionary`(문서만)
- 한 일: 사용자 결정 기록 — 인사팀 소명 글은 GraphRAG(그래프 사실만 주고 AI가 근거 번호를 달아 쓰기), 지식 그래프 템플릿 글은 실패 시 대체로만. 근거는 사용성(E7: 근거 오류 0으로 같고, GraphRAG 약 890자 vs 템플릿 약 5,200자).
- 상대 영향: claude-b — 지식 그래프 화면·소명 PDF의 계약은 claude-a가 생성·검증 모듈을 만들 때 적는다(work-split 요청 갱신).
- 검증: 문서만(코드 변경 없음)
- 근거: `rehearsal/results/justification-compare.html`

## 2026-10-09 · claude-a · IT 기술 이름 사전 + 하위 기술 부분 인정 + E8b(그래프 DB·RDF 장점 실측)
- 브랜치/커밋: `feat/claude-a-skill-dictionary` `2442c4f..` (지식 그래프 브랜치 위, main 병합은 사용자 승인 대기)
- 한 일: 사용자 결정(사전 범위 = SI 핵심 큐레이션, 상하위 관계 = 점수에 부분 인정, KG 구현 = core/kg 유지). `core/ingest/skills.py`·`skill_dictionary.json`(228개, 한·영 별칭, 상위·관련 관계,
  버전 표기·괄호 병기 규칙, SKOS Turtle 내보내기) → `to_dataset` 기본 적용(대표 이름, 가장 긴 경력, 하위 → 상위 `0.5^깊이` 인정, 모르는 이름은 경고만) → `build_kg`도 같은 함수(인정 간선에 `implied_from`).
  측정: 별칭·실데이터형 변형 사본에서 S 원본과 같음(100·200·300), 사전 없이는 100명 쌍의 86% S 변화·배치 점수 49.1 vs 57.8. E8b: 3,000명에서 core/kg 조직 기술 지도 671 → 33.6 ms(색인·집계 개선, Neo4j 27.9),
  RDF 표준 추론은 전이 147쌍 같음·가중 인정은 표현 불가. 보고서용 요약 `docs/kg-technology-decision.md`에 "두 기술의 장점은 얼마나 큰가" 절.
- 상대 영향: **(1) 입력 단계 기본 동작 변경** -- 시연 org-n200·n300의 S가 오른다(100은 그대로). main 병합 뒤 미리 계산 4개(n200·n300 × 계획·운영 중)를 다시 만들어야 한다(그 전엔 재채점에서 걸러져 실시간 계산).
  (2) `build_kg`의 `skill_alias` 인자 삭제(호출자 없음) → `skill_dictionary`·`partial_credit`. (3) claude-b 요청 4건(work-split): 계수 설정 노출, 데이터셋 버전에 사전 버전, 모르는 이름 경고 표시, 인정분 출처(공유 계약 제안).
  (4) 시험 기준선 1413 → **1510**(이 브랜치). Phase 0 PASS(입력 단계 변경 후 재확인, 결과 파일은 원래대로 둠).
- 검증: `uv run --group benchmark pytest -q` → 1510 passed, 1 skipped, 19 deselected · Phase 0 PASS · Opus 폴백 리뷰 2회(MUST 4 → 0, 재확인 MUST 2 → 0)
- 근거: `rehearsal/results/skill-dictionary.html`, `rehearsal/results/kg-advantages.html`, `docs/kg-technology-decision.md`

## 2026-10-09 · claude-a · E8 지식 그래프 구현 기술 비교(RDF vs 그래프 DB vs networkx) — 증적
- 브랜치/커밋: `feat/claude-a-kg-backends`(`feat/claude-a-knowledge-graph` 위) `6af8da0..` (main 병합은 지식 그래프 브랜치와 함께, 사용자 승인 대기)
- 한 일: 사용자 요청("3가지 버전에서 지금 시스템에 어떤게 베스트인지 증적, 보고서에 쓸 근거"). 판정 규칙을 측정 전에 커밋(`rehearsal/kg_backend_decision.py`)한 뒤
  같은 사실·같은 질문 4개로 networkx·Oxigraph(SPARQL)·Neo4j(Docker, Cypher)·rdflib(참고)를 쟀다(`rehearsal/kg_backends.py`). 판정 **메모리 그래프(networkx)** —
  정확성 세 후보 모두 4/4, Neo4j는 별도 서버라 후순위, networkx 11/12칸. 규칙 3을 빼도 networkx 8·Neo4j 4·RDF 0. 현재 core/kg는 networkx가 아닌 자체 메모리 그래프(10/12칸 더 빠름) —
  유지·이전은 사용자 결정. Opus 폴백 리뷰 MUST 2(Q4 일부 속성만 비교, 메모리 과소) 고치고 처음부터 재측정, 첫 결과 `kg-backends.superseded-1.json` 보존. 보고서용 요약 `docs/kg-technology-decision.md`.
- 상대 영향: 없음(영역 안 rehearsal·tests·docs만, 비교용 라이브러리는 `uv run --with`로만 — `pyproject.toml`·`uv.lock` 무변경). 테스트 기준선 1413 → **1423**(이 브랜치, 판정 규칙 시험 10개 추가).
- 검증: `uv run --group benchmark pytest -q` → 1423 passed, 19 deselected · `git diff 6af8da0 -- rehearsal/kg_backend_decision.py` 비어 있음 · 최종 측정 env commit `c03eaa5` dirty=false
- 근거: `rehearsal/results/kg-backends.html`, `docs/kg-technology-decision.md`, 다음 계획(기술 이름 사전) `.omc/plan/2026-10-09-skill-dictionary.md`(루트 로컬)

## 2026-10-08 · claude-a · 지식 그래프(그래프 하나 + 보기 둘) + 인사팀 소명 방식 실험 E7
- 브랜치/커밋: `feat/claude-a-knowledge-graph` (push, **main 병합은 사용자 승인 대기**)
- 한 일: 사용자 요청("지식그래프도 일단 시작하자, 용도별로 다 각각 만들어야 하나?" → 그래프 하나). `core/kg/`(새 디렉터리, claude-a 소유) — 사람·기술·현재/과거 사업·고객사·산업·평가·협업을
  메모리 그래프로(그래프 DB 없음), 보기 `skill_map`(조직 기술 지도·제안 부족)·`project_evidence`(요구 기술 충족·산업/고객사 경험·팀 협업·평가·"왜 다른 사람이 아니었나").
  실데이터는 이름·평가 원문·업무 요약 제외. 시안 `rehearsal/results/kg-preview.html`. E7(`rehearsal/results/justification-compare.html`): KG 템플릿·GraphRAG 둘 다 근거 오류 0·필수 요소 63/63,
  GraphRAG 약 890자 vs 템플릿 5,200자, 일반 RAG는 비교·규칙 사실이 없어 핵심을 못 다룸 — 소명 방식은 사용자 결정 대기.
- 상대 영향(claude-b): 화면·PDF 계약은 사용자 결정 뒤에 적는다(work-split 예고). 테스트 기준선 **1413 passed**(이 브랜치).
- 검증: 전체 1413 passed · Phase 0 PASS · Opus 대체 리뷰 MUST 1(월별 투입) 수정 후 0.
- 근거: `.omc/reports/2026-10-07-knowledge-graph.md`(claude-a 로컬)

## 2026-10-08 · claude-a · claude-b 요청 처리: 미리 계산 재생성·GLM 단가·MILP 동기화 함정
- 브랜치/커밋: `feat/claude-a-b-requests` → main `6cfcf4a`
- 한 일: 리뷰 판정 추론 강도 low 반영으로 무효가 된 미리 계산 6개를 main `a3efcc7` 기준으로 재생성(임시 데이터 폴더 서버에서 6개 모두 수용 확인). `fixtures/pricing.json`에 `glm-5.3`(추론 low).
  CLAUDE.md 함정: MILP 정식을 다시 쓰는 곳 다섯(서비스·벤치·C1 보정 LP·검증기·평가기). 시연 리뷰 글 생성기 개선은 사용자 결정으로 최종 정리 때.
- 상대 영향: 미리 계산 파일이 바뀌었다(화면 "미리 계산" 표시 대상). Z.ai 잔액 소진(E6) — GLM 비교 전 충전 필요.
- 검증: 전체 1403 passed(병합 전) · 서버 스모크 6/6 수용.
- 근거: `.omc/reports/2026-10-07-claude-b-requests.md`(claude-a 로컬)

## 2026-10-07 · claude-a · E6: LLM 직접 배치 vs 모델+솔버(사용자 요청 "증명해 보이자")
- 브랜치/커밋: `feat/claude-a-llm-vs-solver` (main 병합, 사용자 승인)
- 한 일: gpt-6-luna(추론 high)·GLM 5.3에게 같은 문제(20/50/100명)를 통째로 맡겨 서비스 평가기로 채점. luna는 20명에선 솔버의 89~94%, 100명에선 원자료 0/2(위반)·점수 제공 2/3(55%), 한 번에 4~11분.
  GLM은 쓸 수 있는 배치 0(출력 한도를 추론에 소진·위반 1), 실험 중 **Z.ai 잔액 소진**으로 50·100명은 미실행. 결과 `rehearsal/results/llm-vs-solver.html`, 해설 `docs/demo-notes.md` 8절, 대본 장면 3-1.
- 상대 영향(claude-b): **Z.ai(GLM) 잔액이 바닥났다** — GLM 비교(E4·E5 후속 등)를 돌리기 전에 충전 필요(사용자). 코드 영향 없음.
- 검증: 기록에서 다시 그리기(`--render-only`) · Opus 대체 리뷰 MUST 1(요약표 분모·시범 강도) 수정 후 0. 비용 ≥ $1.44(성공 호출 기준 하한).
- 근거: `.omc/plan/2026-10-07-llm-vs-solver.md`, `.omc/reports/2026-10-07-llm-vs-solver.md`(claude-a 로컬)

## 2026-10-07 · claude-b · 시연 정직성: 미리 계산 표시 + 시연 고르기 다듬기(claude-a 리허설 요청)
- 브랜치/커밋: `feat/claude-b-precompute-badge` (main 병합)
- 한 일:
  - 결과 카드: 미리 계산 결과면 "미리 계산 · 시각" 배지(화면에서 교체·조정하면 "미리 계산 원안 + 변경 n건"), 일반 캐시는 "저장된 결과". 카드 위 안내줄 + "다시 계산"(`fresh: true`).
  - 미리 계산 때 대안이 모자랐으면 "다시 실행"이 아니라 "다시 계산"을 안내한다(미리 계산 결과는 다시 실행해도 같다).
  - 운영 중 K 표: 미리 계산 행의 시간 칸에 "(미리 계산 때)", 표 위 안내줄 + "다시 계산"(`fresh`).
  - 시연 고르기: 목록에 `title`, 고른 묶음의 `description`, 처음 고른 항목은 지금 켜진 묶음.
  - 운영 중 묶음이면 요건 설정 "최적화 실행" 아래에 "전부 다시 짜기는 200·300명에서 빈자리가 남을 수 있다 → 운영 중 편성 탭" 안내. 이를 위해 데이터셋 정보(`/api/datasets/active` 등 `_info` 다섯 경로)에 `scenario`("operating"|"planning") 칸 추가.
- 상대 영향(claude-a): 서버 계약 변화는 `scenario` 칸 추가뿐. 미리 계산 파일에 `computed_at`이 비면 화면은 "시각 미상"으로 보인다. **미리 계산 6개는 main(추론 강도 low) 기준으로 다시 만들어야 화면에 "미리 계산"으로 나온다**(앞 항목 요청).
- 검증: 웹 vitest 173 passed · `npx tsc -b` · lint · build, 백엔드 `pytest -q` 1402 passed, 실서버 스모크(두 번째 요청 cached, `fresh`면 다시 계산, `scenario=planning`).

---

## 2026-10-07 · claude-b · 사내 LLM 추론 강도 low 확정 → 리뷰 판정 기본 reasoning_effort=low
- 브랜치/커밋: `feat/claude-b-effort-low` (main 병합)
- 한 일: 사용자 결정 "Low로 하자"(사내 LLM 추론 강도). `api/review_judge.py` 기본 추론 강도를 low로 바꿨다(대소문자·공백 정리). 칸을 받지 않는 모델이면 `TEAMWEAVER_REVIEW_REASONING_EFFORT=none`(또는 off)으로 끈다 -- 400 오류 메시지에도 이 안내를 붙였다. 요청 타임아웃 3배는 high·xhigh·max에만 둔다. 전체 한도의 건당 18초는 강한 추론이거나 OpenAI가 아닌 주소(사내)에 추론을 켤 때만이다(GLM low 300건 333초 실측, 요청 한도 대기 포함). OpenAI low는 예전처럼 2.25초. 비었거나 공백뿐인 설정은 low로 본다.
- 성능 근거(E4, 정답 있는 300건): 외부 gpt-6-luna는 low여도 부정 검출이 기본과 비슷하다(89% 대 92%). **사내 GLM 5.3 low는 69%로 max(84%)보다 확실히 덜 잡는다**(p≈0.019) -- 비용(1,000건 $0.78 대 $4.10)을 보고 사용자가 low를 골랐다.
- 상대 영향:
  - **미리 계산 결과 6개(`demo/precomputed/*.json`)가 모두 무효가 된다.** 판정 캐시 키에 "low"가 들어가 시연 묶음마다 처음 켤 때 다시 판정하고(100명 묶음 1,372건 약 3분·약 $0.12, 300명은 약 4,000건이라 약 9분·약 $0.33, 6개 묶음 각각), 판정값이 바뀌면 데이터셋 버전도 바뀌어 `load_precomputed`가 건너뛴다 -- 그러면 300명 안 A~D가 다시 실시간 계산(10분 이상)이다. **claude-a: main 병합 뒤 시연 기기에서 `python -m rehearsal.precompute_demo`로 다시 만들어 달라**(work-split 요청).
  - 키 없이 캐시만으로 돌던 오프라인 시연 PC는 캐시가 맞지 않아 판정이 실패하고 항목 점수로 돌아간다. 시연 PC에서 키를 넣고 한 번 판정하거나, 예전 동작이 필요하면 `TEAMWEAVER_REVIEW_REASONING_EFFORT=none`.
  - 시연 묶음의 저장된 교체 기록은 데이터 버전이 달라져 이어지지 않는다.
  - (claude-a) pricing에 glm-5.3 low 항목 요청(work-split).
- 검증: `uv run --group benchmark pytest -q` 전체(리포트·커밋 메시지 참고). E4 보고서는 라벨만 바꿔 기록으로 다시 만들었다("서비스 현재 설정" → "10-07 이전 서비스 설정").
- 미결: 미리 계산 재생성(claude-a), 사내 실제 엔드포인트가 생기면 low 재측정.

---

## 2026-10-07 · claude-b · 사내 LLM = Z.ai GLM 5.3으로 가정(사용자 결정)
- 브랜치/커밋: `feat/claude-b-inhouse-assume` (main 병합)
- 한 일: 사용자 결정 "사내 서버로 측정은 불가하니 z.ai로 연결한게 사내 llm이라 가정하고 해야해"에 맞춰 E4·E5 보고서·라벨·docstring의 "사내 LLM 대리/대리값·사내 엔드포인트에서 다시 잰다"를 "사내 LLM(GLM 5.3·Z.ai, 사내로 가정)"으로 바꿨다. 가정은 보고서 상단·방법·한계에 밝혔다. 수치·로직 변경 없음(기록만으로 재생성).
- 상대 영향: 앞으로 LLM 비교의 사내 열은 Z.ai GLM 5.3(low)이고, 사내 서버 재측정은 후속 과제가 아니다.
- 검증: `tests/test_e4_judges.py` 18 passed. Opus 폴백 리뷰 MUST 0·SHOULD 2(반영).

---

## 2026-10-07 · claude-a · 시연 리허설(헤드리스 브라우저로 대본 장면 1~8) + 미리 계산 신뢰도 배지 수정
- 브랜치/커밋: `feat/claude-a-demo-rehearsal` (push, main 병합은 사용자 승인 후)
- 한 일: 임시 데이터 폴더로 서버를 띄워 대본 장면을 실제로 따라 했다(전부 동작). 300명 안 B~D "증명 정보 없음" → 미리 계산 기록이 신뢰도 칸 없이
  만들어졌고 불러오는 코드도 칸을 손으로 나열해 버렸다 → 모든 칸을 되살리게 고치고 6개 묶음 다시 생성. 대본을 화면 이름·숫자에 맞춤.
- 상대 영향(claude-b): **미리 계산 표시가 가장 급하다** — 결과 카드는 "캐시"로만, 운영 중 K 표의 시간 칸은 방금 계산한 것처럼 보인다(work-split 요청 2026-10-07).
  나머지 화면 우선순위는 요청 문서 "리허설 결과" 절. 테스트 기준선 1385.
- 검증: 전체 1385 passed · 다시 띄운 서버에서 배지 확인(100명 A "최대 8.9%", 300명 B~D "허용 차이 5% 안에서 최선 증명") · Opus 대체 리뷰 MUST 0.
- 근거: `docs/demo-script.md`, `.omc/reports/2026-10-07-demo-rehearsal.md`(claude-a 로컬)

## 2026-10-07 · claude-a · 결정 기록 + main 병합(충돌 해결): 감점은 쌍 감점 유지, 시연 고르기는 claude-b 계약으로 통일
- 브랜치/커밋: `feat/claude-a-demo-presets` ← main 병합(충돌 해결) → main(사용자 승인 2026-10-07)
- 한 일:
  - 사용자 결정 — 실험 G 파트너 다양성 하한은 채택하지 않고 지금 쌍 감점(μ=0.2, 최근 3년 중 12개월) 유지. 하한 코드는 꺼진 실험 옵션.
  - **시연 데이터 고르기가 두 벌 구현돼 있었다**(claude-a `api/demo_presets.py` vs claude-b `api/demos.py`, 같은 경로·다른 계약). 사용자 결정 "claude-b 기준"으로
    claude-a 쪽 목록·전환을 지우고, claude-b 모듈 위에 claude-a 기능을 얹었다: zip 묶음(200·300명), `-broken` 제외, 목록 칸 `title·description·current·bench·proposals`(기존 칸 유지),
    공용 빌더 `api.demos.build_demo`, 미리 계산 연결(고르기·되돌리기·다시 판정·부팅), `/api/datasets/active`의 `demo_name`·`precomputed`.
    원인은 claude-a의 조율 누락(서버 쪽을 맡으며 "진행 중"에 적지 않음).
  - claude-b 요청 처리: `compare_move_budgets(on_row=)`(K마다 carry 반영 행).
- 상대 영향(claude-b): 웹은 그대로 동작한다(칸 추가만). `title·description`을 시연 데이터 카드에 쓸 수 있다. 미리 계산 배지(`precomputed_at`)·다시 계산(`fresh`)·운영 중 비교 미리 계산 연결은 요청 문서 끝 절.
- 검증: 병합 후 전체 **1384 passed**(claude-b 운영 중 화면·E4·E5 포함) · 웹 tsc·165 passed · Phase 0 PASS · Opus 대체 리뷰 MUST 1(운영 중 미리 계산이 가중치를 안 봄) 수정.
- 근거: `docs/demo-notes.md` 7절, `rehearsal/results/partner-compare.html`, `.omc/reports/2026-10-07-merge-demo-picker.md`(claude-a 로컬)

## 2026-10-07 · claude-a · 시연 데이터 두 장면 전환·미리 계산(E)·시연 대본·구조도(F)·감점 재설계 측정(G)
- 브랜치/커밋: `feat/claude-a-demo-presets` `479b3b3..` (push, main 병합은 사용자 승인 후)
- 한 일:
  - 시연 묶음 목록·전환 API(연초 계획/운영 중 × 100·200·300명). 화면은 claude-b(사용자 결정).
  - 미리 계산 결과 6개(`demo/precomputed/`). 서버가 버전·설정·재채점을 확인해 "미리 계산(시각)"으로 보여 준다. `fresh`로 다시 계산.
  - 시연 대본 `docs/demo-script.md`, 한 장 구조도 `docs/architecture.html`.
  - 실험 G: 파트너 다양성 하한(기본 꺼짐). 측정상 SI 팀 크기에서는 거의 걸리지 않고 200명 풀이를 망쳐 **채택 보류 권장**(사용자 결정 대기).
- 상대 영향:
  - **claude-b 영역 `api/`를 사용자 지시로 고쳤다**(`docs/work-split.md` 요청 2026-10-07). 화면 계약: `docs/requests/2026-10-06-operating-staffing-ui.md` 끝 절.
  - `MilpParams`에 `partner_floor`·`partner_floor_weight`(기본 0, HTTP 계약 밖). 결과 캐시 키(`params.model_dump`)에 들어가지만 기본값이라 같은 요청은 같은 키다.
  - 테스트 기준선 **1343 passed, 19 deselected**.
- 검증: `uv run --group benchmark pytest -q` → 1343 passed · Phase 0 PASS · 실제 서버로 6개 묶음 전환 시 미리 계산 모두 수용(skipped 없음) · Opus 대체 리뷰 2건 각 MUST 1 수정 후 재확인 MUST 0.
- 근거: `rehearsal/results/partner-compare.html`, `demo/precomputed/*.json`, `.omc/reports/2026-10-07-demo-both-and-remaining.md`(claude-a 로컬)

## 2026-10-07 · claude-b · 외부·사내 LLM 비교를 low·유의미한 표본으로: E4 갱신 + E5 교체 설명 비교(신규)
- 브랜치/커밋: `feat/claude-b-llm-low` (main 병합)
- 한 일(사용자 지시 "llm을 사용하는 모든 외부 사내 비교에서 비교가 유의미한 샘플 사이즈정도만, 전부 low"):
  - **E4 리뷰 판정**(`experiments/jev/e4_judges.py`)
    - 주 비교는 외부 gpt-6-luna low 대 사내 대리 GLM 5.3 low(Z.ai)다. 외부 기본 추론(서비스 현재 설정)·사내 max는 참고 열이다.
    - 시험은 충실한 글 300(기존 글, 지문 일치)과 현재 시연 원문 무작위 300이다.
    - 결과: 부정 검출 외부 low 89% · 사내 low 69%(McNemar p<0.001) · 사내 max 84% · Jev 17%. 1,000건당 $0.08 · $0.78 · $4.10.
  - **E5 교체 설명**(`experiments/jev/e5_briefing.py`, 신규)
    - claude-a `generate_briefing`을 그대로 부른다. 운영 중 시연 묶음에서 교체 100건(절반은 대기 인력 투입), 양쪽 모두 low.
    - 결과(2차): 성공 100% · 99%, 새 위반 교체의 '보류' 준수 100% · 100%, 자유 판단 결론 일치 93%(25/27), 인용 0.5 · 1.9개, 100건당 $0.06 · $0.96, 지연 3.6 · 7.6초.
    - 1차 측정(같은 100건)의 가드 기반 결과는 보고서 이력에 남겼다: 성공 98% · 98%, 외부 보류 위반 2, 사내 형식 실패 2. 1차 결론 분포는 파서 결함으로 폐기했다(`results/superseded/`).
  - 보고서: `outputs/review-judge-comparison.html`(E4), `outputs/briefing-llm-comparison.html`(E5).
- 상대 영향:
  - (claude-a) 교체 설명 프롬프트·가드는 그대로다. 결과상 사내 GLM low로도 교체 설명은 외부와 같은 수준이다.
  - 리뷰 판정은 사내 low가 부정을 덜 잡는다 → 사내 배포의 추론 강도 결정 근거로 쓴다.
  - 가상 데이터 생성(core/datagen)은 서비스 기능이 아니라 비교에서 뺐다.
- 검증: `uv run --group benchmark pytest -q` → 1361 passed, 19 deselected(오프라인 시험 18개 포함). 보고서는 기록만으로 다시 만들어진다. Opus 폴백 리뷰 2라운드(1차 MUST 2·SHOULD 5, 2차 MUST 1·SHOULD 2), 모두 반영.
- 근거: `.omc/reports/2026-10-07-llm-low-comparisons.md`

---

## 2026-10-07 · claude-b · 판정기 비교 E4에 사내 LLM 대리(GLM 5.3) 추가 + 서비스 추론 강도 설정
- 브랜치/커밋: `feat/claude-b-e4-glm` (main 병합)
- 한 일:
  - 사용자 지시("LLM 쓰는 모든 곳은 외부 AI와 사내 GLM 5.3을 항상 함께 비교"): E4를 3자 비교로 확장했다. 외부 gpt-6-luna / 사내 대리 GLM 5.3(Z.ai 공식 API, 추론 max, 가상 데이터만) / Jev.
  - 실험 장치
    - 한 건마다 중간 기록을 남기고, 기본은 부분 결과를 그대로 쓴다. 이어서 판정하려면 `E4_RESUME=1`.
    - 잔액 부족·키 거절은 구조로 판별해 바로 멈춘다.
    - 판정한 글의 지문(fingerprint)을 남겨, 데이터가 바뀌면 섞지 않고 멈춘다.
    - McNemar·Wilson 통계를 낸다.
  - 서비스 `TEAMWEAVER_REVIEW_REASONING_EFFORT`: 설정하면 reasoning_effort를 보낸다. 설정하지 않으면 캐시 키·데이터 버전은 예전 그대로다. 설정 시 한도·요청 시간을 늘린다.
  - 결과(충실한 글 300건)
    - 부정 검출: 외부 92% · 사내 GLM 84% · Jev 17%. 두 LLM의 차이는 확정되지 않는다(McNemar p≈0.07).
    - 1,000건당 비용: $0.08 · $4.10 · $0.02. 건당 지연: 2.2초 · 10.5초 · 0.2초.
    - 시연 원문은 GLM 480/1,372건에서 잔액이 소진돼 부분 결과다.
- 상대 영향:
  - **main에는 claude-a의 시연 데이터 문구 변경(9087ffa, `demo/org-n100/reviews.csv`)이 이미 들어와 있다.** 그래서 E4 보고서를 다시 만들면 지문 불일치로 멈춘다(의도한 동작이다). 지금 보고서와 기록은 9087ffa 이전 시연 데이터(이 브랜치 기준 b8e41e2) 기준이다. 새 데이터로 보려면 E4를 다시 재야 한다(GLM은 비용이 든다).
  - Z.ai 키는 `.env`의 `ZAI_API_KEY`다(외부, 가상 데이터만 보낸다).
- 검증: `uv run --group benchmark pytest -q` → 1305 passed, 19 deselected(병합 전). Opus 폴백 리뷰 2라운드(1차 MUST 1·SHOULD 8, 2차 MUST 0·SHOULD 2), 모두 반영.
- 근거: `outputs/review-judge-comparison.html`, `experiments/jev/results/e4_*.json`, `.omc/reports/2026-10-07-e4-glm.md`

---
## 2026-10-06 · claude-b · 운영 중 편성·사업 보강 API와 화면 + 단순 규칙 대비 카드 + 계산 신뢰도 배지 + 시연 데이터 고르기
- 브랜치/커밋: `feat/claude-b-operating-ui` (기준 claude-a `feat/claude-a-operating-staffing`. main 미병합 — claude-a 브랜치와 함께 사용자 승인 후)
- 한 일(claude-a 요청 `docs/requests/2026-10-06-operating-staffing-ui.md`):
  - API `api/routes/operating.py`
    - `GET /api/operating/state`, `POST /api/operating/compare`(SSE: start→progress→row→done).
    - `POST /api/staffing/{candidates,simulate,best}`, `POST /api/baseline`.
    - 비교는 서버 전체에서 한 번에 하나다(바쁘면 429). 슬롯은 계산 스레드가 끝날 때 풀린다.
    - 운영 경로 time_limit은 600초 이하, K는 0~3, 입력 길이에 상한을 둔다.
  - 데이터: `ActiveDataset.current·scenario`(manifest의 scenario·bench·proposals).
    - `GET /api/datasets/demos`, `POST /api/datasets/demo`(관리자, 허용 목록만, 먼저 빌드하고 실패하면 422로 아무것도 바꾸지 않음, 성공하면 선택을 기억하고 업로드 보관본을 지움).
    - 되돌리기는 선택을 잊는다.
  - 신뢰도: `PlanAssignment.termination·best_bound·gap_used`(Codex 영역 임시 위임 범위). 플랜 카드·K 행·최선 n명에 배지(독립 검증 통과, 허용 차이 안 최선 증명, 시간 한도 시 "최적값이 이 해보다 최대 X% 높을 수 있음").
  - 웹
    - "운영 중 편성" 탭: K별 비교(빈자리와 배치 품질 분리, 이동 그림, 사업별 변화), 보강(후보표, 넣어 보기/빼 보기, 재평가, 최선 n명, 등급 선택).
    - What-if 화면: "단순 규칙 대비" 카드.
    - 데이터 탭: 시연 데이터 고르기(2단계 확인).
- 상대 영향:
  - (claude-a) K별 실시간 송출을 위해 `compare_move_budgets(on_row=)`를 요청했다(work-split).
  - 단순 규칙 대비 카드는 운영 화면에 두지 않았다. 시연 데이터 실측에서 K=0 품질 69.2 대 백지 단순 규칙 76.3으로, 같은 조건 비교가 아니었다.
  - `core/optimize/types.py`·`alternatives._solve` 칸을 추가했다(기본값이 있어 호환된다).
- 실측(`demo/org-n100-operating`, 100명):
  - compare 35초. K=0..3이 0.5/1.1/9/25초이고, 빈자리 2→1→0→0, 품질 69.19→69.90→69.68→70.39, 모두 최적 증명.
  - candidates 0.5초(빼 오기 1.9초), best 0.3~0.7초.
- 검증: `uv run --group benchmark pytest -q` → 1341 passed, 19 deselected. `-m slow` → 19 passed. 웹 165 passed. tsc·oxlint(0)·build 통과. Opus 적대적 리뷰 2라운드(1차 MUST 3·SHOULD 8, 2차 MUST 1·SHOULD 2), 모두 반영.
- 미결: simulate의 AI 설명 재사용, 운영 결과를 기준 배치로 이어 쓰기, PDF에 운영 결과 넣기.
- 근거: `.omc/reports/2026-10-06-operating-staffing-ui.md`

---

## 2026-10-06 · claude-a · 운영 중 편성(신규 제안 + 변경 예산 K)·진행 사업 보강 시뮬레이터·단순 규칙 대비·시연 장면 D
- 브랜치/커밋: `feat/claude-a-operating-staffing` `9087ffa..5b5693c` (push, main 병합은 사용자 승인 후. 기준 = `feat/claude-a-familiarity-literature`)
- 한 일:
  - 사용자 요청("대부분 이미 배치된 상황에서 신규 제안 2~3개를 최근 끝난 10명 안팎으로 짜기, 1~2명 이동 시 개선량, 진행 사업 보강 시뮬레이션")의 계산 부분.
  - 운영 중 데이터 `demo/org-n100-operating`(+200/300 zip, 같은 사람), 변경 예산 엔진 `core/optimize/incremental.py`, K=0..3 비교 `core/evaluate/operating.py`, 보강 후보·넣기/빼기·최선 n명 `core/evaluate/staffing_sim.py`, 단순 규칙 대비 `core/evaluate/baseline.py`.
  - 시연 장면 D: 깨진 업로드 묶음 `demo/org-n100-broken.zip`(오류 6건을 파일·줄·칸까지), AI 인용 검증 장면 `rehearsal/results/guard-demo.html`.
  - 실측: 100명 K=0→3 빈자리 2→0, 0.5~28초(모두 최적 증명). 최선 2명 보강 +2.10(남는 인력) / +2.61(1명 빼 오기). 단순 규칙 대비 배치 품질 100명 31.6→58.2, 300명 113.5→164.0(단순 규칙은 빈자리 1~3·예산 위반 0~2).
- 상대 영향:
  - **claude-b 요청**: API·화면 계약 `docs/requests/2026-10-06-operating-staffing-ui.md`(`docs/work-split.md` 요청 항목).
  - `core/optimize/milp.py`의 `extra_constraints` 훅이 `alloc_vars` 인자를 선언하면 투입률 변수를 받는다(기존 2인자 훅은 그대로).
  - `core/optimize/greedy.solve_greedy`에 `min_alloc` 인자(기본 0.2 = 예전 그대로).
  - 리뷰 문장 틀 문구 변경으로 `demo/org-n100`·zip의 `reviews.csv`가 바뀌었다(값·라벨 동일, 문장 한 구절만).
  - 테스트 기준선 **1318 passed, 19 deselected**(`--group benchmark`).
- 검증: `uv run --group benchmark pytest -q` → 1318 passed · Phase 0 PASS · Opus 대체 리뷰 MUST 1(겸직자 빈틈) 수정 후 재검토 MUST 0.
- 근거: `rehearsal/results/{operating-check,baseline-check}.json`, `.omc/reports/2026-10-06-operating-staffing.md`(claude-a 로컬)

## 2026-10-06 · claude-a · 실데이터 가정 전체 검증: 기준 정식 반영·시드 운영화·실데이터 경로 리허설·과거 성과 검증·시연 기본값 전환
- 브랜치/커밋: `feat/claude-a-recent-familiarity` `e021de8..` (push, main 병합은 사용자 승인 후)
- 한 일:
  - **익숙한 쌍 = 최근 36개월 중 12개월**(사용자 결정)을 정식으로 반영했다. 데이터 계약 `CoworkRecord.months_ago`, 그래프 `cowork_within`, `MilpParams.clique_window_months`를 추가했고, 검증기·오라클·평가기·벤치·설정·웹 "반복 협업 조회 기간"·E2E를 함께 바꿨다.
  - **시드 포트폴리오 운영화**: 큰 모델을 시작 인자로 넘기면 자식이 죽을 때 부모가 영원히 멈추던 문제를 고쳤다. 모든 시드가 실패하면 한 번 단일 풀이로 계획을 낸다. 시드 수는 `TEAMWEAVER_SOLVER_SEEDS`로 정한다(시연 4). 1시드면 대안이 품질 하한에 걸려 안 A 하나만 나왔다.
  - **실데이터 경로 리허설**(`rehearsal.run --as-real`): 100/200/300명 모두 업로드 오류 0, 안 A~D, 빈자리 0, 교체 검토·적용·PDF 정상. 실데이터 모드 AI 설명의 근거가 늘 비던 문제도 고쳤다(본문이 가리킨 라벨을 근거로 싣는다).
  - **과거 성과 검증** `core/evaluate/outcome_check.py`: 협업 방향은 단순 비교에서 일관되나 다변량에서는 견고하지 않다. 익숙함 감점 μ는 검정력이 부족해 판단할 수 없다. μ는 사용자 결정으로 남긴다.
  - **시연 기본 데이터를 `demo/org-n100`으로 전환**했다(`run_poc.sh`, 시드 4, 시연 데이터일 때 부팅 사전계산 생략).
- 상대 영향:
  - (claude-b) `api/settings.py`·`api/schemas.py`·`web/` 설정 화면·`tests/api/test_ui_e2e.py`·`scripts/run_poc.sh`를 바꿨다. 상세는 `docs/work-split.md` 요청에 있다.
  - 서비스 기본 익숙한 쌍 기준이 바뀌어 fixture 결과도 달라진다(137→79쌍).
  - 화면 E2E는 예전 기준을 화면에서 골라 결정적 시나리오를 유지한다.
  - 기준선: 1288 passed, slow 19 passed, 웹 153.
- 검증: 위 기준선, Phase 0 PASS. Opus 폴백 리뷰는 단계마다 받았고, MUST(웹 저장 422, 과장된 μ 결론, churn 오염)는 모두 반영했다.
- 근거: `.omc/reports/2026-10-06-validate-as-real.md`, `rehearsal/results/{rule-compare,outcome-check}.html`, `rehearsal/results/n*/pipeline-real.json`, `docs/demo-notes.md` 4~6절

---

## 2026-10-06 · claude-b · 리뷰 글 판정을 LLM으로 통일 + 판정기 비교 실험 E4(정확도·비용·시간)
- 브랜치/커밋: `feat/claude-b-llm-judge` (main 병합)
- 한 일:
  - 사용자 결정("선택권 없이 llm으로 통일"): 직전 커밋의 규칙 기반/Jev 선택 설정과 화면을 걷어냈다. 이전 settings.json·이전 화면의 `review_judge`는 버린다.
  - CSV 묶음(업로드·시연 묶음)의 평가 사유를 `api/review_judge.py`가 OpenAI 호환 API로 판정해 `text_polarity`를 다시 매긴다.
    - 주소 `TEAMWEAVER_REVIEW_BASE_URL`(OPENAI_BASE_URL과 분리), 모델 `TEAMWEAVER_REVIEW_MODEL`, 사내 키 `TEAMWEAVER_REVIEW_API_KEY`.
    - 병렬 16, 429 대기·재시도, 리뷰 수에 비례한 한도, 캐시 0600(실데이터는 지금 데이터만, 가상 데이터는 따로 쌓음).
    - fixture는 생성 때 LLM 값을 그대로 쓴다. 실패하면 항목 점수(`items`) + `POST /api/datasets/rejudge`로 남은 건만 다시 판정한다.
    - **실데이터를 사내로 확인되지 않은 곳(OpenAI 포함)으로 보내려면 `TEAMWEAVER_REVIEW_ALLOW_EXTERNAL=1`이 필요하다.** 없으면 `blocked`로 항목 점수를 쓴다(리뷰 지적, 사용자 확인 대기).
  - 데이터 탭: 판정 정보, 보내는 곳(외부 OpenAI/사내/확인 안 됨), 동의 상태, 판정 다시 시도.
  - 판정·전환은 잠금 밖에서 만들고 바꿔 끼우기만 잠금 안에서 한다(적용 교체 저장이 수 분 멈추지 않게).
  - 실험 E4(`experiments/jev/e4_judges.py`, 결과 `experiments/jev/results/e4_*.json`, 보고서 `outputs/review-judge-comparison.html`).
    - 충실한 글 300건: 부정 검출 LLM 92% 대 Jev 17%, 부호 일치 84% 대 60%.
    - 시연 원문 1,372건: LLM 201초·$0.118 대 Jev 17초·$0.030.
- 상대 영향:
  - **K5 결정 변경**(work-split 요청 항목): 실데이터 평가 사유가 점수 판정용으로 LLM에 간다(동의 플래그 또는 사내 주소일 때만).
  - CSV 묶음의 `dataset_version`이 판정값을 담아 바뀐다(`content_version`이 원천 해시, 업로드 저장 파일 이름). 시연 묶음 첫 기동은 판정 때문에 약 3분 늦다(이후 캐시).
  - 시연 생성기 글 수정 요청(직설형 평가자 5~10% 등)을 work-split에 남겼다.
  - 테스트는 `tests/api/conftest.py` autouse가 판정을 가짜로 바꾸고 주소를 막는다.
- 검증: `uv run --group benchmark pytest -q` → 1283 passed, 19 deselected. `-m slow` → 19 passed. `npx vitest run` → 149 passed. tsc·oxlint·build 통과. 실제 OpenAI로 서비스 경로 스모크(병렬 16: 1,372건 196초, 캐시 0.1초; 병렬 32는 429). Opus 폴백 리뷰 2라운드(1차 MUST 1·SHOULD 9, 2차 MUST 0·SHOULD 3), 모두 반영.
- 근거: `.omc/reports/2026-10-06-llm-review-judge.md`

---

## 2026-10-06 · claude-a · '익숙한 쌍' 기준 비교(최종 보고서 근거) — 사용자 결정 대기
- 브랜치/커밋: `feat/claude-a-familiarity-rule` `0d155d6` (push, main 미병합)
- 한 일: `rehearsal/rule_compare.py`로 현행(10년·6개월), 최근 5년·12개월, 최근 3년·12개월을 비교했다. 100/200/300명 × 1·4시드, 안 A, 자동 시간 조건이다. 보고서는 `rehearsal/results/rule-compare.html`이고 해석은 `rule-compare-analysis.json`에 있다.
- 결과:
  - 현행: 200·300명이 붕괴한다(빈자리 79 / 91~95).
  - 5년: 300명 갭 3~7%로 좋지만 200명은 갭 37~40%다.
  - 3년: 모든 규모에서 갭 4~11%, 빈자리 0이고 협업 보상이 가장 크다. 대신 오래 함께 일한 쌍이 한 팀에 더 들어간다.
  - 권장은 3년 기본 + 관리자 설정이다.
- 상대 영향: 없음(측정 도구). 결정되면 감점용 조회 기간을 데이터 계약·convert·graph에 추가한다. 이때 공유 계약이 바뀌므로 "요청"에 먼저 적는다. milp·validation·plan_eval·bench도 함께 바꾼다.
- 검증: 1272 passed, 19 deselected. Opus 폴백 리뷰 MUST 1건(표시 문구)을 반영했다.
- 근거: `.omc/reports/2026-10-06-familiarity-rule-compare.md`

## 2026-10-06 · claude-a · 계산 안정화(1차): HiGHS 상한 기록·시드 포트폴리오·진단 — 규칙 정의는 사용자 결정 대기
- 브랜치/커밋: `feat/claude-a-solve-stability` `d6b7c4f..b56f290` (push, main 미병합)
- 한 일:
  - `SolverEvidence.best_bound`에 HiGHS 증명 상한(최대화 방향)을 기록한다. 예전에는 항상 None이었다.
  - `MilpParams.solver_seeds`(기본 1 = 동일) 시드 포트폴리오 `core/optimize/highs_portfolio.py`: 100명 60초 29.5 → 33.3.
  - 측정 도구 `rehearsal/solve_probe.py`.
- 상대 영향:
  - (claude-b) `tests/api/test_settings.py` 미러 시험의 제외 목록에 `solver_seeds`를 추가했다. 설정 연결과 상한 표시 요청은 `docs/work-split.md`에 있다.
  - (모두) **실제 같은 10년 이력에서 200·300명이 붕괴한다**(빈자리 79 / 91~95). 익숙한 쌍(10년 중 6개월 이상)이 9,794 / 11,733개이기 때문이다. 포트폴리오로도 안 되고, 같은 의미의 묶음 정식으로도 품질이 낮다.
  - "최근 3년 중 12개월 이상"으로 바꾸면 100/200/300명 모두 빈자리 0, 갭 11 / 7.9 / 5.6%가 된다. 규칙 정의는 사용자가 결정한다.
- 검증: `uv run --group benchmark pytest -q` → 1227 passed, 19 deselected. Phase 0 PASS. Opus 폴백 리뷰 3회, 남은 MUST 없음.
- 근거: `.omc/plan/2026-10-06-solve-stability.md`, `.omc/reports/2026-10-06-solve-stability.md`, `rehearsal/results/n*/solve-probe.json`

---

## 2026-10-06 · claude-b · 리뷰 글 판정 방식 선택(규칙 기반 기본 / Jev)
- 브랜치/커밋: `feat/claude-b-review-judge` (main 병합)
- 한 일:
  - 설정 `review_judge: "rule"|"jev"`(기본 rule)을 추가했다. jev면 데이터셋을 만들 때 리뷰 원문을 TypeSafe Jev API로 보내 `text_polarity`를 다시 판정한다. Score 5단계를 확률 기댓값으로 바꿔 [-1,1]에 놓고, 병렬 16·전체 한도 120초로 부른다. 판정 결과는 캐시(데이터 폴더 `jev_judgments.json`, 0600, 지금 데이터만)에 둔다.
  - 판정 방식을 바꾸면 PUT /api/settings가 활성 데이터셋을 같은 원천으로 다시 만든다(업로드는 저장된 zip에서). jev 버전은 sha256(원 버전·판정값)이고, `content_version`은 원천 해시라 업로드 저장·복원에 쓴다.
  - Jev가 실패하면 규칙 기반으로 만들고 `judge_error`를 남긴다. 화면에서 "판정 다시 시도"(`retry_judge`)를 누를 수 있다.
  - 화면: 설정 탭에 선택지·외부 전송 경고·실측 수준 차이 안내, 데이터 탭에 판정 방식·실패 이유·Jev 상태 업로드 경고.
- 상대 영향:
  - `DatasetInfo`에 `content_version`·`review_judge`·`judge_error`가 생겼다. `DatasetStore`는 `content_version`으로 저장한다(기존 포인터는 그대로 호환).
  - `MilpParamsIn`은 `review_judge`를 받기만 한다. `PlacementSettings.to_milp_params`는 `NON_SOLVER_FIELDS`를 뺀다.
  - 실측(시연 100명, 1,372건): 첫 판정 17초, 캐시 0.01초. 규칙 기반과 상관 0.88이지만 평균 0.56 대 0.16이고 음수가 없다(보정 안 함).
  - 테스트는 `tests/api/conftest.py` autouse로 Jev 주소를 막는다.
- 검증: `uv run --group benchmark pytest -q` → 1257 passed, 19 deselected. `-m slow` → 19 passed. `npx vitest run` → 150 passed. `npx tsc -b`·oxlint·build 통과. 실제 키 스모크 2회. Opus 폴백 적대적 리뷰 2라운드(1차 MUST 2·SHOULD 7, 2차 MUST 0·SHOULD 2), 모두 반영.
- 근거: `.omc/reports/2026-10-06-review-judge-setting.md`

## 2026-10-06 · claude-b · claude-a 요청 처리: 데이터 탭 표시·자동 계산 시간·시간 한도 배지
- 브랜치/커밋: `feat/claude-b-data-settings` (main 병합)
- 한 일:
  - 데이터 탭: `source="demo-bundle"`를 "시연 데이터(실제 형식)"로 표시. 서버 오류 문장을 그대로 보여 준다. 기본 데이터로 되돌릴 때 시연 묶음이 실패하면 이유(`restore_error`)를 돌려준다. 선택 파일 2개에는 "(선택 · 계산에 쓰지 않음)" 표시.
  - 설정: `time_limit_auto`(새 설치 기본 켬)는 인원 기준 `time_budget.recommend`로 계산 시간을 정한다. GET `/api/settings`가 `effective_time_limit`을 주고, 웹은 이 숫자를 `milp_params.time_limit`으로 보낸다. 부팅 사전계산도 같은 값을 써서 캐시가 맞는다. `time_limit` 상한은 600에서 900으로 올렸다. 실행 안내에 A~D 최악 대기 시간을 보인다.
  - 시간 한도에서 멈춘 해: `PlanAssignment.time_limited`(`alternatives._solve`가 `termination_reason == "time_limit_incumbent"`이면 True) → SSE `time_limited` → 플랜 카드 배지 "시간 한도 도달(최선 증명 전)".
- 상대 영향:
  - `core/optimize/types.py`·`alternatives.py`(Codex 영역)를 임시 위임 범위에서 고쳤다. 새 칸은 기본값 False라 기존 호출은 그대로 동작한다.
  - 기존 `settings.json`(칸 없음)은 수동으로 읽는다. 관리자가 정한 시간은 바뀌지 않는다.
  - 기본 설정의 계산 시간은 100명 기준 120초에서 30초가 된다(자동). 시간 한도 해는 캐시하지 않는다.
- 검증: `uv run --group benchmark pytest -q` → 1221 passed, 19 deselected. `-m slow` → 19 passed. `npx vitest run` → 142 passed. `npx tsc -b`·oxlint·build 통과. Opus 폴백 리뷰(Codex 쿼터 소진): MUST 1(소유 영역 절차, work-split에 기록), SHOULD 4 반영.
- 근거: `.omc/reports/2026-10-06-data-settings.md`

## 2026-10-06 · claude-a · 실제 같은 시연 데이터: 긴 동료 평가·과거 성과·10년 조회 창·시연 부팅
- 브랜치/커밋: `feat/claude-a-demo-data` `b3456a6..7cc9c6e` → main 병합(사용자 결정 2026-10-06: 병합하되 `run_poc.sh` 기본값은 예전 고정 데이터, 실제 형식 데이터는 `TEAMWEAVER_DEMO_BUNDLE`로 켬)
- 한 일:
  - 조직형 생성기(`core/ingest/org_profile.py`): 등급별 근속(최대 25년) → 최근 10년만 내보냄. 업무 이력 맥락 칸. LLM 없는 긴 동료 평가(`core/ingest/review_text.py`, 20개 항목에 기획력·친화력 포함, 1인 연 ~9건).
  - 과거 성과 `project_outcomes.csv`·교체 `replacements.csv`(선택 파일, 숨은 규칙. 평가↔실제 역량 상관 0.35~0.45).
  - 읽는 쪽 10년 창(`convert.LOOKBACK_MONTHS`): 협업 개월은 창 안만 센다. 창 이전에 마지막으로 쓴 기술은 제외하고, 120개월 초과는 120으로 본다.
  - `TEAMWEAVER_DEMO_BUNDLE` 부팅과 `demo/org-n100`, `org-n200.zip`, `org-n300.zip`.
- 상대 영향:
  - (claude-b) `api/main.py`·`scripts/run_poc.sh`를 사용자 요청으로 고쳤다. 화면 요청 3건은 `docs/work-split.md` 요청 섹션에 있다.
  - (모두) 선택 입력 파일 2개와 업무 이력 선택 칸이 생겼다. 모델·API 동작은 그대로다.
  - (모두) **실제 같은 10년 이력에서는 MILP가 느리다.** 100명 안 A가 HiGHS 120초 한도에 걸려 목적 29.5~31이 나온다. 같은 문제의 B~D 해는 ~41, LP 상한은 49.8이다. 예전 데이터는 9.6초에 최적해가 나왔다.
    - 원인은 데이터 쪽이다: 6개월 이상 함께 일한 쌍이 321에서 1,764로 늘었고, 협업 보상·감점 구조가 함께 무거워졌다.
    - 기준 24개월, μ=0, 보상 쌍 100개, 스레드 4, 300초 모두 효과가 없었다.
    - 이 데이터를 기본으로 부팅하면 사전계산에 약 8분이 걸린다. 다음 작업 = 계산 안정화(claude-a, 공유 정식 파일이라 착수 시 요청 기록).
- 검증: `uv run --group benchmark pytest -q` → 1203 passed, 19 deselected. Opus 폴백 리뷰 2회, 남은 MUST 없음.
- 근거: `docs/demo-notes.md` 3절, `rehearsal/results/data-overview.html`, `.omc/reports/2026-10-06-realistic-demo-data.md`

---

## 2026-10-06 · claude-b · 사람별 달별 투입률 조정(UX) + Jev 실험 실제 결과
- 브랜치/커밋: `feat/claude-b-alloc-edit`(`50f6c2c`) + `feat/claude-b-jev-experiment`(`959832d`) → main fast-forward·push.
- 한 일:
  - **사람별 달별 조정**(사용자 결정: 기본은 기간 내내 한 비율, 겸임 인력만 수정):
    - 배치 표 행마다 "달별 조정"을 두고, 위에 안내 배너를 단다. 편집기에서 달별 %를 넣거나 "모든 달 같게"로 맞춘다. 최소 투입률도 안내한다.
    - 적용하면 서버가 명단 전체를 다시 평가한다(`POST /api/plans/apply-alloc`). 조정은 교체와 같은 이력에 쌓이고, 취소·초기화·서버 저장·PDF 재생이 같은 흐름을 탄다.
    - 적용 단계는 교체|조정 판별 유니온이다. `kind`가 없는 예전 기록은 교체로 읽고, 교체는 여전히 `kind` 없이 직렬화한다.
    - 명단에 월별 항목이 있으면 최적화율 분모는 월별 LP 상한이다. 화면·PDF에 안내한다.
  - **Jev 실험 실제 실행**: 공식 api.typesafe.ai, jev-1.13.0, 391회 호출, 약 $0.011. 결과는 cassette로 기록해 키 없이 재생한다(`outputs/jev-experiment.html`).
    - E1 솔버 대신(100명): Jev 26.24, MILP 40.58, 기술 1등 규칙 30.11, 무작위 11.41.
    - E2 리뷰 극성: Jev r=0.854, gpt-6-luna 0.851. 차이 CI [-0.022, +0.025]로 구분되지 않는다. 0.20초 대 2.63초/건. 단, 평균 오차·긍부정 일치는 Jev가 낮다(0.213 대 0.162, 61% 대 74%).
    - E3 교체 고르기(37건): Jev 40.5% [26,57], 기술 1등 76% [60,87], 무작위 45%.
- 상대 영향:
  - (claude-a) `PlanEditIn.swaps`·`ReportRequest.applied_swaps`가 교체와 `{"kind":"alloc", person_id, project_id, monthly_alloc}`를 받는다. `api.routes.plans.apply_step`이 공통 함수다. plan_eval은 그대로 쓴다(월별 지원 덕분).
  - (claude-a) Jev E2 결과상 리뷰 글 판정(text_polarity)을 Jev로 바꾸면 같은 상관을 13배 빠르게 얻는다. 절대 수준은 덜 맞는다. 바꿀지는 claude-a와 사용자가 판단한다.
  - 테스트 기준선: **1191 passed, 19 deselected**, slow 19, vitest 136.
- 검증: 위 기준선, tsc·oxlint·build 통과. 리뷰는 Opus 폴백으로 했다. 달별 조정은 MUST 0이고 SHOULD 4를 반영했다. Jev 정리도 MUST 0이고 SHOULD 2를 반영했으며, 키 없는 재생이 수치를 재현함을 확인했다.
- 근거: `.omc/reports/2026-10-06-alloc-edit-and-jev.md`

## 2026-10-05 · claude-b · 월별 투입률(사용자 결정) 끝까지: 모델·검증·API·화면·PDF + 권장 시간
- 브랜치/커밋: `feat/claude-b-monthly-alloc` → main fast-forward·push.
- 한 일:
  - `MilpParams.allocation_mode`: fixed(기본, 이전과 같음) | monthly(진행 달마다 투입률). 다음을 함께 바꿨다.
    - 서비스 MILP, 독립 검증기, C1 보정 LP, Phase 0 오라클(월별 MILP = 오라클 테스트)
    - 최적화율 분모(월별 LP 완화 상한)
  - 계약: `AssignEntry`/`EntryIn.monthly_alloc`(없으면 직렬화에서 빠짐). 서명은 월별 값을 묶는다. 교체는 빠지는 사람의 월별 값을 이어받는다.
  - 화면·PDF: "1~3월 20%, 4~6월 100%" 구간 요약, 설정 화면에 투입률 방식(기본 fixed), 방식별 권장 계산 시간(claude-a 요청 처리).
  - claude-a가 plan_eval에 월별 지원(`020ee6d`)을 넣었으므로, 임시로 둔 API 재검사(`api/monthly_eval.py`)는 지웠다.
  - 측정(조직형 데이터, HiGHS, Plan A, `outputs/c6-monthly-scale*.json`):
    - 끝까지 풀면 월별은 100/200/300명에서 +20.5/+15.4/+15.0%이고, 16/77/309초 걸린다.
    - 고정 방식 권장 시간 안에서는 200명이 시간 한도에 걸리고(+9.7%), 300명은 −1.6%다.
    - 그래서 월별 권장 시간 `time_budget.MEASURED_MONTHLY`(×1.5 → 30/120/480초)를 추가했다.
- 상대 영향:
  - (모두) `MilpParams`·`PlacementSettings`에 `allocation_mode`가 생겼다. plan_token은 params 전체를 서명하므로 이전 토큰은 무효가 된다.
  - (모두) `core.optimize.time_budget.recommend(n, allocation_mode=)` 인자가 추가됐다(기본 fixed, 결과 동일).
  - (Codex) 벤치 정식(`experiments/phase1/solvers.py`)은 fixed만 지원하고, monthly면 ValueError를 낸다. 월별 정식은 서비스와 오라클에 있다.
  - 테스트 기준선: **1180 passed, 19 deselected**, slow 19, vitest 133, Phase 0 PASS 11.
- 검증: 위 기준선을 확인했다. 실제 lifespan(사전계산 포함)은 4플랜 17초. 리뷰 Opus 폴백 2라운드: 1차 MUST 2(최적화율 분모, 평가기 평균)를 반영했고 2차는 승인이었으며 SHOULD 3도 반영했다.
- 근거: `.omc/reports/2026-10-05-monthly-allocation.md`, `.omc/plan/2026-10-05-monthly-allocation.md`

## 2026-10-05 · claude-a · 데이터 개요·시연 노트 + plan_eval 달별 투입률 지원(claude-b 요청)
- 브랜치/커밋: `feat/claude-a-seat-fit` → main.
- 한 일:
  - `docs/demo-notes.md`(시연 설명 노트: "왜 협업 보상은 궁합 상위 200쌍만인가" 등), 비교 보고서에 같은 설명.
  - `rehearsal/data_overview.py` → `rehearsal/results/data-overview.html`: 두 가상 데이터의 생성 방식·개수·샘플(과거 프로젝트 성과 데이터는 없음을 명시).
  - `core/evaluate/plan_eval.py`: `monthly_alloc`(진행 달 → 투입률) 지원 — 달별 가용률·월 예산·투입률 범위, 기술항 = 진행 달 평균. 없거나 `{}`면 비트 동일. `SUPPORTS_MONTHLY_ALLOC = True`.
- 상대 영향: **claude-b** — 네 `api/monthly_eval.adjust_for_monthly`는 이제 평가기 결과를 그대로 쓰면 된다. 평균≠alloc(1e-5 초과)·진행 달 불일치는 평가기에서 ValueError. 시험의 `_MEntry`는 `AssignEntry.monthly_alloc`이 들어오면 교체 예정. 기준선 1151 passed.
- 검증: 전체 통과, 리뷰 Opus 폴백 1R(MUST 1: 지원 표시 상수, SHOULD 3 반영).

## 2026-10-05 · claude-a · 로드맵 3번 "자리당 적합도" 실험 — 기각, 꺼진 선택지로 보존
- 브랜치/커밋: `feat/claude-a-seat-fit` → main.
- 한 일: 목적식에 β·Σ S·z(자리당 적합도)를 6곳(MILP·검증기·보정·plan_eval·오라클·벤치)에 일관되게 넣었다(오라클 일치 시험). 100·200명 β 실험 결과
  "작은 사업 손해"는 체계적이지 않았고(200명에선 반대), β를 키우면 쪼개기 비효율이 나빠져 **기본 0 유지**. HTTP 요청으로는 켤 수 없다(측정용).
- 상대 영향:
  - **모두**: `MilpParams`에 `seat_fit_weight`(0)가 생겨 `model_dump()`가 바뀐다 → 배포 전 서명한 plan_token과 그 토큰으로 저장된 적용 교체(K13)는 검증에 실패한다(오늘 max_pairs 변경 때와 같은 성격). 캐시는 프로세스 메모리라 무관.
  - **Codex/벤치**: `experiments/phase1/solvers.py`가 바뀌어 Phase 1 매니페스트 `source_commit`이 달라진다(β 기본 0이라 해는 동일).
  - 테스트 기준선: 1141 passed. Phase 0 PASS 11.
- 근거: `docs/model-roadmap.md`, `rehearsal/results/factor_lab/seat_fit_n{100,200}.json`

## 2026-10-05 · claude-a · claude-b 요청 2건: 동시 프로젝트 위반(plan_eval) + 위반 교체는 '보류'
- 브랜치/커밋: `feat/claude-a-scale-rehearsal` → main.
- 한 일: `core/evaluate/plan_eval.py`가 `concurrent_projects` 위반을 낸다(MILP·검증기와 같은 규칙). 교체 설명은 이 교체로 새 위반이 생기면 결론을 '보류'로 하고 위반을 위험 1순위로 쓰며, 지키지 않은 LLM 답은 규칙 기반으로 전환(사유 `recommends_infeasible`). 교체 전부터 있던 위반만 있으면 보류를 강제하지 않는다.
- 상대 영향: **claude-b** — `api/routes/whatif.py::_with_concurrency_check`는 평가기가 같은 코드를 내므로 이제 실행되지 않는다(정리 가능). 테스트 기준선 1134 passed.
- 검증: 전체 1134 passed. luna 실호출 3/3 '보류'+위반 1순위. 리뷰 Opus 폴백 1R(SHOULD 1 반영: 판정을 new_violations 기준으로).

## 2026-10-05 · claude-a · 규모 리허설 결과: 보상 쌍 상한 200 + 인원별 권장 시간 + 모델 실험실
- 브랜치/커밋: `feat/claude-a-scale-rehearsal`(`a7bf67d` 쌍 상한, `55c59a4` 모델 실험실, 이 항목 커밋) → main 병합.
- 한 일:
  - **서비스 기본 협업 보상 쌍 상한 5000 → 200**(`MilpParams.max_pairs`). 200명부터 쌍 변수(쌍×사업) 때문에 HiGHS가 600초에도 미충원 57석, 300명은 해 없음 → 200이면 세 규모 모두 미충원 0.
  - **인원별 권장 시간**(`core/optimize/time_budget.py`): 100/200/300명 → 30/60/180초(측정 15/30/120초 × 1.5).
  - 전 과정 재측정(실제 서버): 100명 29초, 200명 189초, 300명 392초 — 모두 A~D 4안·미충원 0·교체 검토·PDF 정상. 전후 비교 보고서 `rehearsal/results/rehearsal-report.html`.
  - 모델 실험실(`core/evaluate/factor_lab/`) 100명 결과: 검증용 가정에서는 현재 모델(S만)이 최선, 제안·소형·단기 사업에 적합도 낮은 사람이 앉는 경향(0.53 vs 0.63~0.67).
- 상대 영향:
  - **모두**: 서비스 해가 달라진다(더 빨리·더 좋게). 화면의 "협업 시너지"는 상위 200쌍만 보상. 이전 플랜 토큰·캐시 무효.
  - **claude-b**: 설정 화면 연결 요청 1건(work-split "요청"). C6 병합 뒤 "자리당 적합도" 항 정식 변경을 claude-a가 이어서 한다(상태 파일).
  - **claude-b**: `tests/jev/test_jev_harness.py`의 "키 없음" 시험이 `.env`의 `TYPESAFE_API_KEY`(다른 시험이 load_env로 올림)에 따라 순서 의존 실패 → `monkeypatch.delenv`로 고립시켰다.
  - C6(동시 프로젝트 상한)와 합친 뒤 100·300명 전 과정을 다시 돌려 결과 동일 확인(28초/395초, 4안·미충원 0).
  - 테스트 기준선: **1126 passed, 19 deselected**, Phase 0 PASS 11.
- 근거: `docs/model-roadmap.md`, `.omc/reports/2026-10-05-scale-rehearsal.md`
## 2026-10-05 · claude-b · Jev(판단 전용 AI) 대체 가능성 실험 장치 + 기준선
- 브랜치/커밋: `feat/claude-b-jev-experiment` `ddd8d4f` → main fast-forward·push(사용자 요청: 실험 과정·결과를 시연용으로 남김).
- 한 일: Jev가 대체할 수 있는 영역을 세 자리로 나눠 시험하는 장치를 만들었다(`experiments/jev/`, 보고서 `outputs/jev-experiment.html`).
  - E1 솔버 대신: 자리마다 Jev가 고르기(Choice)로 사람을 뽑는다. 숫자 조건은 코드가 지킨다.
  - E2 모델 재료 대신: 리뷰 글 → 극성(Score). 기준선은 LLM 파서다.
  - E3 판단 보조: 교체 후보 중 최선을 고른다.
  - Jev 응답은 cassette로 기록해 키 없이 재생한다. 기준선은 측정을 마쳤고, Jev 행은 키를 받은 뒤 채운다.
- 기준선(합성 데이터, NOT_CALIBRATED):
  - E1(100명): 솔버 40.58 / 기술 1등 규칙 30.11 / 무작위 11.41
  - E2: 정답 상관 gpt-6-luna 0.85(2.6초/건), 기록된 gpt-5-nano 0.56
  - E3(37건, 후보 4명): 기술 1등 81% [66,91], 무작위 기댓값 43%(동점 17건)
- 상대 영향: 없음. 서비스 코드 변경 없음. 의존성 추가 없음(httpx로 HTTP 직접 호출). `experiments/jev/cassettes/e2_luna.json`은 OpenAI 판정 결과 기록이다(키 미포함).
- 검증: `pytest tests/jev` 8 passed, 전체 1108 passed. 리뷰 Opus 2라운드: 1차 MUST 3(E3 동점, E2 지시문 동등성, 척도 가정)을 반영했고 2차는 승인이었다.
- 근거: `.omc/reports/2026-10-05-jev-experiment.md`(Jev 결과 후 작성), `outputs/jev-experiment.json`

## 2026-10-05 · claude-b · C6 배치 규칙(동시 프로젝트 상한) + 월별 투입률 측정 + whatif 재료 연결
- 브랜치/커밋: `feat/claude-b-c6-rules` (main `0a15c4f`에서 시작, main `d5d23cc`(K3 HiGHS) 병합 `cd19450`, C6 `ce225d1`) → main fast-forward·push.
- 한 일:
  - **C6 동시 프로젝트 상한**: 한 사람이 같은 달에 맡는 프로젝트 수 ≤ K. 기본 3이고 관리자 설정에서 1~6으로 바꾼다(사용자 답변 "최대 3개, 보통 1개").
    - 반영한 곳: 서비스 MILP, 벤치 정식, Phase 0 오라클, 독립 검증기(`concurrent_projects`), greedy, 설정 화면, PDF 계산 기준 줄.
    - 행은 묶일 수 있는 달에만 넣는다. 조건은 (K+1)·min_alloc ≤ 가용률이다.
  - **평가기 공백 메움**: 평가기(`core/evaluate`)가 이 규칙을 모른다. What-if·교체 적용·PDF 재계산이 위반을 놓치지 않게 `api/routes/whatif.py::_with_concurrency_check`가 위반을 덧붙인다. claude-a에게 요청했다.
  - **claude-a 요청 처리**: whatif가 `swap_context(project_id=)`와 `generate_briefing(score_change=)`를 넘긴다. score_change에는 `feasible`과, 위반이 있으면 `new_violations`도 들어간다.
  - **월별 투입률은 측정만 했다**(`experiments/c6/monthly_alloc.py`, `outputs/c6-monthly-alloc.json`): 25~100명에서 목적값 +11~13%, 같은 솔버·1스레드로 풀이 시간 1.1~3.8배. 서비스 반영은 사용자 결정 대기다.
  - **상한 비용**(`experiments/c6/concurrency_cost.py`, `outputs/c6-concurrency-cost.json`, HiGHS 서비스 설정): K=3은 K=6과 시간·점수가 같다(약 43초, 4플랜). min_alloc 0.3에서는 상한 행이 묶이지 않기 때문이다. K=1은 −2%다.
  - C5 Gurobi는 사용자 결정으로 보류했다(상용 유료).
- 상대 영향:
  - (claude-a) "요청" 2건이 있다. 평가기에 `concurrent_projects` 위반을 넣는 것과, 브리핑 프롬프트가 `score_change.feasible`을 쓰게 하는 것이다.
  - (모두) `MilpParams`·`PlacementSettings`·`MilpParamsIn`에 `max_concurrent_projects`가 생겼다. PDF 요청의 `milp_params`는 이 칸까지 있어야 한다(전체 필드 요구). 그래서 새로고침 전에 열려 있던 탭은 PDF 요청에서 422를 받을 수 있다.
  - (모두) `plan_token`이 params 전체에 서명하므로, 이전 계산에 묶인 저장 교체는 새로 계산하면 연결이 끊긴다(K3도 같은 효과).
  - (모두) 예전 Phase 1 raw 결과를 새 검증기로 다시 검증하면 `concurrent_projects`가 새로 뜰 수 있다(벤치 min_alloc 0.2).
  - 테스트 기준선: **1100 passed, 19 deselected**, slow 19, vitest 129, Phase 0 PASS 11.
- 검증: 위 기준선을 확인했다. 실제 lifespan(사전계산 포함)으로 4플랜이 43초에 나왔다. 상한 테스트 6개와 평가기 공백 테스트는 수정 전 실패를 확인했다.
  리뷰: Codex 소진 → Claude Opus 폴백 2라운드. 1차 MUST 1(평가기 공백)을 API에서 반영했고 SHOULD 5도 반영했다. 2차는 승인이었고, SHOULD 1(측정 출처 스크립트)도 반영했다.
- 근거: `.omc/reports/2026-10-05-c6-rules.md`

## 2026-10-05 · claude-a · 서비스 솔버 HiGHS 전환(K3) + 규모 리허설 도구
- 브랜치/커밋: `feat/claude-a-highs-service`(← `feat/claude-a-scale-rehearsal`), main 병합 예정.
- 한 일:
  - **사용자 결정으로 서비스 MILP 솔버를 HiGHS로 고정**(`MilpParams.solver="highs"`, CBC는 비교용 내부값, HTTP·설정 화면에서 못 바꿈).
    근거: Phase 1(1스레드·240초) HiGHS 79/112 vs CBC 22/112, 조직형 100명 리허설에서 같은 30·60초에 HiGHS +35.2/+41.5 vs CBC −5376/−460.
  - **HiGHS 경계 잔차 처리**: HiGHS가 z=1.0000000000000007 같은 값을 돌려 엄격 검증기(C0)가 전부 거절하던 문제를 `_snap_bounds`(1e-9)로 해결.
    정리된 값으로 C1 보정 자격·LP를 판단하고, 정리 개수·최대 이동량은 evidence.options에 남긴다. 검증기 허용오차는 그대로.
  - HiGHS가 해를 못 찾으면(PuLP가 0을 채움) no-incumbent 예외로 처리(이전엔 "검증 거절"로 오분류).
  - Phase 0 오라클 사례·시험은 `gap=0.0`(정확 최적 비교). 기본 5%로는 HiGHS가 최적 직전에 멈춰 실패했고, 예전 CBC 통과는 우연.
  - 리허설: `core/ingest/org_profile.py`(100=DP, 200=DP+AI, 300=DP+AI+업무자동화, 익명 최대 사업 28석), `rehearsal/`(실제 서버 전 과정 + 시간별 품질 sweep + 비교 보고서), `docs/model-roadmap.md`(사용자 제안 4건 검토).
- 상대 영향:
  - **모두**: 서비스 해 값·시간이 CBC 때와 달라진다(같은 시간에 더 좋은 해). `outputs/phase1-*` 서비스 스모크의 `native_validation`은 이제 정리 후 값 기준. 게이트 시험은 HiGHS·CBC 둘 다 돈다.
  - **claude-b**: `tests/api/test_settings.py`의 미러 시험에서 `solver`를 의도적 예외로 뺐다. `tests/test_solver_validation_gate.py` 가짜 솔버가 `sol_status`를 설정한다.
  - 테스트 기준선: **1089 passed, 19 deselected**, slow 19, Phase 0 PASS 11.
- 검증: 위 명령 전부 통과. 리뷰: Codex 주간 한도 소진 → Claude Opus 폴백 2라운드(1차 SHOULD 2, 2차 SHOULD 1·nit, 모두 반영).
- 근거: `.omc/reports/2026-10-05-highs-switch.md`, `docs/model-roadmap.md`

## 2026-10-05 · claude-a · LLM 모델 2단계 분리·프롬프트 튜닝 + 해설 문서 갱신
- 브랜치/커밋: `feat/claude-a-llm-tiers` (main 미병합)
- 한 일:
  - 모델 2단계(사용자 지시): 교체 설명=`gpt-6-luna`(complex), 리뷰 생성·분석=`gpt-5.5`(simple). `fixtures/pricing.json`만 바꾸면 된다. 단가 확인 결과 luna($0.10/$0.50)가 5.5($5/$30)보다 싸다(복잡도 단계와 가격이 반대 — 사용자에게 보고).
  - 교체 설명 프롬프트: 결론 먼저, 명분 2~3문장·위험 ≤3·대안 ≤2, `context.project`·`score_change`를 받으면 그것으로 판단. 추론 깊이는 모델 항목의 `reasoning_effort`(luna=low).
    숨김 모드는 라벨을 옮겨 적은 인용·따옴표를 허용(라벨 공개는 사용자 결정), 그 밖의 인용은 계속 거부.
  - `swap_context(..., project_id=)` 선택 인자(기존 SQLite 표 읽기만), `usage_summary`는 단가 없는 모델을 0원 대신 None, 리뷰 생성 프롬프트는 항목 단어 활용형 허용.
  - `experiments/bench/exp2_pipeline.py`: 비용 추정 모델을 실측 모델 gpt-5-nano로 고정(parse_model 변경으로 깨지던 것).
  - 해설 문서: `outputs/eli5-teamweaver-status.html`(9-12판 → 10-05 현황), `outputs/eli5-mid-project-quality-audit.html`(10-05 처리 현황 표 추가). 처음 Git에 올린다.
- 상대 영향:
  - **claude-b**: whatif 라우트 연결 2곳을 "요청"에 올렸다. `exp2_pipeline.py`(네가 임시로 맡은 experiments 영역) 한 군데를 고쳤다 — 실측 모델 고정뿐.
  - 테스트 기준선: 922 passed, 18 deselected.
- 검증: 전체 922 passed. 실제 API(fixture): 튜닝 전 설명 14초·위험 4~5개·결론 회피 → 튜닝 후 16/16 채택, 4~6초(project·score_change 넣은 조건), 위험 ≤3·대안 ≤2. 리뷰 분석 gpt-5.5 인용 60/60 원문 일치. 생성 6건 어색한 표현 0.
  리뷰: Codex 주간 한도 소진 → Claude Opus 폴백 1라운드, MUST 0·SHOULD 2·nit 3 반영.
- 근거: `.omc/reports/2026-10-05-llm-tiers.md`
## 2026-10-05 · claude-b · Codex 영역 임시 인계: C0·C1 main 통합, C2, C3, C4
- 브랜치/커밋: `feat/claude-b-c1-integrate` = Codex `feat/phase1-solver-benchmark`(`60bfc47`) + G4 산출물 + main 병합(`6f1b1c1`) + 리뷰 반영·C3·C2·C4. main fast-forward와 origin push(사용자 지시).
- 배경: Codex 주간 쿼터가 10-10 13:16까지 소진됐다. 사용자 지시로 claude-b가 C*를 이어받았다. Codex worktree·브랜치는 수정하지 않았다.
- 한 일:
  - C1 마무리: Codex가 커밋하지 못한 G4 결과(`outputs/phase1-c1-g4-*`, completion ELI5)를 그대로 커밋했다(증거 tarball 해시 `57e6111…` 일치). 원시 캡처는 아카이브로 보냈다.
  - 최종 리뷰(Opus, Codex G4 리뷰 대신) REVISE → 반영:
    - (MUST) 대안 하나가 검증에 거절되면 A~C까지 묶음 전체와 부팅 사전계산이 실패하던 것을 고쳤다. 이제 앞선 유효 플랜을 유지하고, 사전계산이 실패해도 서버가 뜬다.
    - 보정 LP 상한을 원본 값으로 했다(투입률을 올리지 않는다).
    - 다양성 컷을 이미 유효한 후보에도 확인한다.
    - 가드 회귀 테스트를 추가했다.
  - main 병합: 코드 충돌은 없었다(문서 2개만 충돌). `service_smoke`의 캐시 키를 K8/K9 키로 고쳤다.
  - C3: `display_alloc`이 해 값보다 크게 만들지 않는다(최소 투입률 0.205일 때 0.206이 0.21로 나오던 문제). 검증기 사본, greedy(내림), 벤치 추출, 웹 % 표시(0.1% 내림)를 함께 맞췄다.
  - C2:
    - 빈 팀, 중복 구성, A의 95% 미만, A보다 미충원이 많은 대안을 제외한다. 필요할 때는 미충원 상한을 모델 제약으로도 넣는다.
    - 대안 solve 실패를 분류한다.
    - 시간 한도에 걸린 해(PuLP가 "Optimal"로 보고 → `sol_status`로 구분)와 실패로 끊긴 묶음은 캐시하지 않는다.
    - done SSE에 `requested_alternatives`·`stop_reason`을 넣고, 화면에 "조건을 만족하는 대안 없음"을 표시한다.
  - C4: `experiments/results/phase1/`(2,788파일, 577MB)를 `~/Dev/teamweaver-archive/`에 48MB 압축으로 보관했다. 파일별 해시를 확인했다.
- 상대 영향:
  - (claude-a) `core/evaluate/plan_eval.py`는 바뀌지 않았다. 반환 alloc이 6자리일 수 있다(최소 투입률이 2자리보다 정밀할 때만). `plan_eval`은 자릿수를 가정하지 않는다.
  - (모두) `generate_plans(_streaming)`에 `outcome` 인자가 생겼다. 이를 monkeypatch하는 테스트는 `outcome=None`을 받아야 한다.
  - (모두) `SolverEvidence.termination_reason`이 시간 한도 해일 때 `time_limit_incumbent`다(서비스 경로만).
  - (Codex) 위 "요청" 두 건.
  - 테스트 기준선: **1044 passed, 19 deselected**(`--group benchmark`), slow 19, vitest 129.
- 검증: 전체 1044 passed. slow 19 passed. 실제 lifespan(사전계산 포함) 4플랜 35.2초로 네 플랜 모두 보정 후 엄격 검증을 통과했다. Phase 0 재검증 PASS(11). vitest 129, tsc·oxlint·build 통과. C3·C2·리뷰 반영 테스트는 수정 전 실패를 확인했다.
- 근거: `.omc/reports/2026-10-05-codex-takeover-c1-c4.md`, `outputs/phase1-c1-integration-service-smoke.json`, `outputs/phase0-c1-integration-revalidation.json`, `~/Dev/teamweaver-archive/README.md`

## 2026-10-05 · claude-b · 정리: 실행 스크립트, 409 뒤 적용 경합 수정, 결정 기록
- 브랜치/커밋: `feat/claude-b-cleanup`(main `b48d5c8` 위). 이 기록을 포함한 브랜치를 main에 fast-forward하고 origin에 push한다(사용자 지시).
- 한 일:
  - `scripts/run_poc.sh` 추가. 배포는 소스 그대로(사용자 결정)이며, 의존성·웹 빌드·Chromium을 준비한 뒤 uvicorn 한 포트로 띄운다. `.env`를 읽고, 외부 주소로 열 때 관리자 비밀번호가 없으면 경고한다.
  - 웹: 409 뒤 서버 상태 복원이 끝나기 전에 한 적용이 옛 이력으로 서버의 최신 저장분을 덮던 경합을 고쳤다(K13 남은 SHOULD). 같은 실행 안에서 기다리는 사이 플랜 화면 상태가 바뀐 저장은 보내지 않는다. 남은 경계: 강제 복원 자체가 네트워크 오류로 실패하면 그 뒤 저장이 서버 최신분을 덮을 수 있다(예전 동작).
  - work-split: K4·K8~K14 상태를 "main 병합"으로 바꿨다. 결정 두 가지를 기록했다. 실데이터의 리뷰 항목 라벨은 외부로 나가도 된다. 패키징은 소스 그대로다.
  - 병합된 claude-b worktree·브랜치 8개를 정리했다.
- 상대 영향: 없음. 실행 방법은 `scripts/run_poc.sh` 머리 주석에 있다.
- 검증: vitest 124 passed(새 회귀 테스트는 수정 전 실패를 확인), `npx tsc -b`·oxlint·build 통과, slow UI E2E 3 passed. 실행 스크립트로 띄워 `/api/meta`·`/`·`/report` 200, PDF 200(148KB)을 확인했다.
- 근거: `.omc/reports/2026-10-05-claude-b-cleanup.md`

## 2026-10-05 · claude-b · main 병합: K5 연결 + claude-a K5·회차 확장·K6
- 브랜치/커밋: `feat/claude-b-k5-connect` → main fast-forward(`67242b9..e6072d1`, 사용자 승인) + 이 기록.
- 한 일: main에 claude-a `feat/claude-a-k5-evidence`(K5 근거 색인, `feat/claude-a-review-rounds`, `feat/claude-a-k6-int8` 포함)와 claude-b K5 연결이 들어갔다.
- 상대 영향: (claude-a) 위 세 브랜치는 이제 main에 있다. 새 작업은 main(`e6072d1` 이후)에서 시작한다. 테스트 기준선은 914 passed, 18 deselected(`--group benchmark`)다.
- 검증: 병합 전 같은 커밋에서 `uv run --group benchmark pytest -q`를 돌려 914 passed, slow 18 passed를 확인했다. 웹은 123 passed, tsc·lint·build 통과.
- 근거: `.omc/reports/2026-10-05-k5c-evidence-connect.md`

## 2026-10-05 · claude-b · K5 연결: 근거 색인 → What-if·브리핑 화면·PDF
- 브랜치/커밋: `feat/claude-b-k5-connect` = main(`67242b9`) + `feat/claude-a-k5-evidence` 병합(`3925a59`) + `45fa9a7` + 이 기록. **main 병합 대기**(이 브랜치에는 claude-a의 미병합 K5·review-rounds·k6-int8이 함께 들어 있다).
- 한 일: claude-a의 K5 연결 요청 (1)~(4)를 모두 처리했다.
  - `build_active`가 근거 색인을 만든다. 원문은 synthetic=true일 때만 색인에 넣는다.
  - whatif는 `get_evidence` 의존성으로 같은 색인을 `swap_context`와 `generate_briefing`에 넘긴다.
  - `EvidenceOut`, `BriefingOut.evidence`를 추가했다. 화면(`EvidenceList`)과 PDF에 직접 인용·요약·원문 비공개 배지를 표시한다.
  - 자체 리뷰(Opus 폴백, 2라운드)에서 나온 지적을 반영했다.
    - M1: 실데이터의 긴 리뷰 항목 때문에 What-if가 500으로 실패했다. 응답에 싣기 전에 스키마 상한(2000자·200자·50개)으로 자른다(`api/briefing_evidence.clamp_briefing`).
    - S1: PDF에 지어낸 "직접 인용"이 찍힐 수 있었다. `/api/report`가 근거를 서버 색인으로 다시 확인하고, 맞지 않으면 422를 낸다.
- 상대 영향:
  - (claude-a) 근거 생산 쪽(`api/rag/evidence.py`, fallback, LLM 인용)은 응답 상한을 모른다. 지금은 라우트가 잘라서 막는다. 색인 쪽에서 라벨 길이를 제한할지는 claude-a가 판단한다.
  - (claude-a) 실데이터에서도 리뷰 *항목 라벨*(review_items.item)은 LLM 프롬프트·응답·PDF로 나간다. 원문 문장은 나가지 않는다. 항목이 자유 문자열이라 문장형 내용이 들어올 수 있으므로, 이를 허용할지 사용자 확인이 필요하다(미결).
  - `tests/api/conftest.py`의 `small_graph_client`는 이제 `get_evidence`도 None으로 덮어쓴다.
  - 테스트 기준선: 914 passed, 18 deselected.
- 검증: `uv run --group benchmark pytest -q` → 914 passed, 18 deselected · `-m slow` → 18 passed · `npm test` → 123 · `npx tsc -b`·oxlint·build 통과 · fault injection 6종 중 5종 검출(1종은 동등 변이: 숨김 모드에서는 원문이 없어 verify_quote가 이미 False를 낸다).
- 근거: `.omc/reports/2026-10-05-k5c-evidence-connect.md`

## 2026-10-05 · claude-b · K13 Codex 3차 반영 + K14 관리자 로그인
- 브랜치/커밋: `feat/claude-b-admin-login` = `feat/claude-b-persistence`(`ddbaba7`·`6df92b5` 반영) + `058e76c`(K14) + 이 기록. **main 병합 대기**(사용자 확인).
- K13 Codex 3차 리뷰(needs-attention, high 5)를 반영했다.
  - reset과 겹친 저장 재기록: 저장 직전 버전 확인과 쓰기를 데이터셋 잠금 안에서 한다.
  - 서버 관리 revision CAS(`expected_revision`, 409 `edits_changed`): 클라이언트 시각을 믿지 않는다.
  - 플랜별 변경 세대: 늦은 복원을 버린다. 복원이 진행 중 검토·적용을 무효화하고, 적용 전에 검토 기준 명단을 확인한다.
  - 키 파일은 완성 후 `os.link`로 게시한다(하드 링크 미지원 FS는 O_EXCL로 대체).
  - 이어진 Opus 확인 리뷰의 MUST(revision이 라벨 기준이라 재실행 뒤 섞임)를 고쳤다. 이제 plan_token 기준이다.
  - 같은 리뷰의 SHOULD도 고쳤다: 충돌 뒤 줄 선 저장을 버리고, 업로드·되돌리기는 전환 표시만 본다.
- K14 관리자 로그인:
  - `POST /api/admin/login`·`logout`, `GET /api/admin`.
  - 비밀번호는 `TEAMWEAVER_ADMIN_PASSWORD_HASH`(scrypt, `scripts/hash_admin_password.py`) 또는 `TEAMWEAVER_ADMIN_PASSWORD`.
  - 세션: 8시간 HttpOnly·SameSite=Strict 쿠키, 서명에 비밀번호 지문·세대를 넣는다. 로그아웃은 서버에서 무효화한다.
  - 잠금: 주소별 동시 1건, 5회 실패 시 60초.
  - 웹: 설정·데이터 탭 로그인 화면, 머리글 로그인/로그아웃, 편집 중 만료돼도 입력을 보존한다.
- 상대 영향:
  - **claude-a**: 관리자 보호 대상은 `PUT /api/settings`, `POST /api/datasets`, `POST /api/datasets/reset`이다. `/api/admin` 응답 형식이 바뀌었다(`login_required`·`protected`·`logged_in`·`expires_at`·`token_required`).
  - CORS `allow_credentials=True`(dev origin 2개). 데이터 폴더에 `session_secret`·`session_epoch`가 생긴다.
- 검증:
  - `uv run --group benchmark pytest -q` → **844 passed, 17 deselected**. slow 17 passed(관리자 로그인 실브라우저 E2E 포함). 웹 vitest 117, tsc·lint·build 통과.
  - 결함 주입: K13 3차 반영 T1~T7·V1~V3(T7은 이중 방어), K14 L1~L7·M1~M5 모두 검출.
- 리뷰:
  - K13: Codex 3차(10:00) 반영 → Claude Opus 확인 2회(Codex 쿼터 10/10까지 소진). 마지막은 MUST 없음.
  - K14: Codex 2회 모두 쿼터 소진 → Claude Opus 2라운드, MUST 없음.
- 남은 것: 409 직후 강제 복원 전에 새로 적용하는 좁은 경합(기존부터 있던 SHOULD), 직원별 계정·SSO는 범위 밖.
- 근거: `.omc/reports/2026-10-05-k14-admin-login.md`, `.omc/reports/2026-10-05-k13-persistence.md`

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

## 2026-10-05 · claude-a · K5 설명 근거의 출처·직접 인용 (claude-a 쪽)
- 브랜치/커밋: `feat/claude-a-k5-evidence` (main + `feat/claude-a-review-rounds` + `feat/claude-a-k6-int8` 병합 위, main 미병합)
- 한 일: 브리핑 문맥의 리뷰 근거를 극성 숫자에서 출처 ID가 붙은 근거로 바꿀 수 있게 했다(`api/rag/evidence.py`).
  LLM 브리핑은 인용을 원문과 글자 그대로 대조하고, 하나라도 어긋나면(없는 출처·바꿔 쓴 인용·본문 속 따옴표·인용 없는 출처 표시) 버리고 규칙 기반으로 전환한다(사용자 결정).
  실데이터(synthetic이 true가 아님)는 원문을 화면·LLM 어디에도 보내지 않고 항목 라벨만 쓴다(사용자 결정). 버린 사유는 `code=`로 로그에 남는다.
- 상대 영향:
  - **claude-b**: 연결 4건을 work-split "요청"에 올렸다. 연결 전에는 API 동작이 그대로다(색인을 안 넘기면 예전과 같음).
    `generate_briefing`·`rule_based_briefing` 결과에 `evidence` 키가 새로 있지만 지금 `BriefingOut`이 버린다.
  - **Codex**: 공유 계약 변경 없음. 참고 정보 1건(요청란).
  - 테스트 기준선: 이 브랜치 850 passed, 15 deselected.
- 검증: `uv run --group benchmark pytest -q` → 850 passed. 변이 2종(느슨한 인용 비교, 본문 따옴표 검사 제거) → 시험 실패 확인.
  리뷰: Codex 시도 1회 → 주간 한도 소진(재시도 10-10 13:16) → Claude Opus 폴백 적대적 2라운드(1차 MUST 1·SHOULD 3, 2차 MUST 1·SHOULD 2 모두 반영). 2차 수정분은 시험으로만 확인.
- 자체 리뷰(사용자 결정: 교차 리뷰 중단, 각자 자체 리뷰): main 대비 전체 diff 재검토 + 연결 흉내 스모크(가상 198조합 통과, 숨김 모드 100조합 원문 유출 없음), MUST 0.
- 근거: `.omc/reports/2026-10-05-k5-evidence.md`

## 2026-10-05 · claude-a · K6 협업 탐색 int8 넘침 수정
- 브랜치/커밋: `feat/claude-a-k6-int8` (main 미병합, 사용자 승인 대기)
- 한 일: `MemoryGraph.synergy_context_memory`가 한 홉 확장에서 frontier 이웃 수를 int8로 세어, 128개 이상(정확히 256개면 0)일 때 그 노드를 도달 집합에서 빠뜨렸다. int32로 바꾸고 127/128/150/256 병렬 경로 시험을 추가했다(고치기 전 128·150·256 실패 확인).
- 상대 영향:
  - **Codex (공유 `core/graph/`)**: 결과는 SQL·Cypher 질의와 같아지는 방향으로만 바뀐다. 실험 1의 예전 결과는 "K6 이전 측정"으로 볼 것(시간 차이 미미).
  - 테스트 기준선: main 648 → 652 passed(이 브랜치 단독).
- 검증: `uv run --group benchmark pytest -q` → 652 passed, 10 deselected. 리뷰: Codex 한도 소진(09:03 회복)으로 Claude Opus 폴백 1라운드, MUST·SHOULD 0, nit 2(주석 표현 반영, 실험 메모 위 기록).
- 근거: `.omc/reports/2026-10-05-k6-int8.md`
## 2026-10-05 · claude-a · 리뷰 회차 확장(예전 회차도 사용) + K2 main 병합
- 브랜치/커밋: K2는 main에 fast-forward 병합(`f10e710`, 사용자 승인). 회차 확장은 `feat/claude-a-review-rounds`(main 미병합, 사용자 승인 대기).
- 한 일: 사용자 요청("최신 회차만이 아니라 예전 회차도")으로 같은 평가자→피평가자의 모든 리뷰 회차를 협업 점수에 쓴다.
  방향 안의 회차를 먼저 평균하고 두 방향을 평균한다. `core.ingest` 변환은 모든 회차를 넘기고, 같은 날 두 회차 오류는 없앴다.
  K2 항목의 "최신 리뷰 회차만 쓴다"는 이 항목으로 정정한다. 점수 구간 12/36/60/96개월은 사용자 확정.
- 상대 영향:
  - **Codex·claude-b (공유 `core/graph/memory_graph.py`)**: 방향당 리뷰 1건인 데이터(fixture·datagen·Phase 1 시나리오·bench)는 쌍 점수가 비트 단위로 같다(테스트 고정).
    리뷰 목록과 parsed의 순서·길이가 다르면 경고 로그 후 기존 방식(방향당 마지막 1건).
  - **Codex (`core/graph/rehydrate.py`, `core/datagen/llm_checkpoint.py`)**: SQLite review 표에 회차 칸이 없어, 같은 방향 리뷰가 여러 건이면 `from_sqlite`와 LLM checkpoint가 `ValueError`로 거부한다.
    중복 검사는 기존 review 스캔 안에서 하므로 exp5 `rehydrate_ms` 측정 경로에 질의가 늘지 않는다. 회차 칸이 필요하면 "요청"에서 합의.
  - **claude-b**: K9 업로드 데이터에 여러 회차가 있으면 이제 모두 점수에 반영된다. `build_sqlite`·RAG 근거는 회차 수만큼 리뷰 행이 늘 뿐 오류는 없다.
  - 테스트 기준선: 648 → **656 passed, 10 deselected**(`--group benchmark`), slow 10 passed.
- 검증: `uv run --group benchmark pytest -q` → 656 passed · `pytest -m slow` → 10 passed · 방향 균형 제거 변이 → 시험 실패 확인. 리뷰: Codex 한도 소진(09:03 회복)으로 Claude Opus 폴백 2라운드, MUST 0.
- 교차 리뷰 요청: claude-b에게 `feat/claude-a-review-rounds` 병합 전 리뷰를 부탁한다(`.omc/agents/claude-a-status.md`).
- 근거: `.omc/reports/2026-10-05-review-rounds.md`
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

## 2026-10-05 · Codex · C1 G3 서비스 연결 / C0 회귀 해결
- 브랜치: `feat/phase1-solver-benchmark`, main 미병합/push 없음. G3 최고역량 독립gpt-6-astra PASS.
- 한 일: service native assessment→제한 고정팀 LP→strict finalgate 연결. callback 전후 모델 계약·추가 선형 조건 투영, unsupported변형은 복구 거절. 기존6실패 테스트 수정/제외 없음.
- 상대 영향: C0의 기본warm-up budget회귀가 해결됐다. API·목적식4항·쌍 함수 시그니처/의미는 그대로. 최종main통합은 G4/최종리뷰 및 사용자승인 전 보류.
- 검증: 전체616passed/0failed/10deselected74.24s; 독립41passed; projection누락 결함주입2FAIL. Phase0새경로11PASS1.063s. 실제APIlifespan skip없이4plans26.331s, A/C nativebudgetFAIL→maxdelta3.333e-9/strictfinalPASS.
- 근거: `outputs/phase1-c1-g3-eli5.html`, `outputs/phase0-c1-revalidation.json`, `outputs/phase1-c1-service-smoke.json`, `docs/superpowers/reviews/2026-10-05-c1-g3-review.md`. NOT_CALIBRATED. HTML화면QA미수행/정적검사PASS.

## 2026-10-04 · Codex · C1 G1 진단·비활성 복구 체크포인트
- 브랜치/커밋: `feat/phase1-solver-benchmark` `98ba47f` (main 미병합 / push 없음).
- 한 일: 기존 CBC 출력·원시값을 보존해 A(문턱 완화)/B(outputFormat6)/C(고정팀 미세LP)를 비교. 제품 service는 연결하지 않은 상태에서 복구 정책·callback 변형 거절·최종 재검증을 구현했다.
- 상대 영향: 목적식/쌍 함수/공유 도메인/API 변경 없음. B는 옵션 적용을 실제 로그로 확인했지만 예산 잔차가 그대로였다. C는 두 재현 입력 strictPASS, tol1e-6 유지.
- 검증: probe5/refinement25 총30 passed. 독립gpt-6-astra G1 REVISE→시간초과 후success2회귀 RED/GREEN 및B증거보완→PASS. C delta≤1e-7, 추가시간약0.00413/0.05233초. 서비스미변경 상태 전체581 pass/6 기존회귀 fail/10 deselected(마지막2회귀 추가 전).
- 근거: `outputs/phase1-c1-g1-eli5.html`, `outputs/phase1-c1-numerical-probe.json`, `docs/superpowers/reviews/2026-10-04-c1-g1-review.md`. G1 PASS는 main통합/사업효과 승인이 아님.

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
# 2026-10-04 Codex · C1 G2 benchmark evidence checkpoint

- G2 highest independent gpt-6-astra review PASS after3Important+1Minor RED→GREEN. Phase1 222passed; related61passed. Service remains unchanged; 6C0 regressions are next Task5.
- Native/validation/final files persisted and hash/path-bound; policy full fields frozen; rejected timings preserved; final worker validator authoritative. Invalid bound retained as BOUND_INVALID, never quality PASS. Payload2/checkpoint1 compatibility preserved.
- ELI5 `outputs/phase1-c1-g2-eli5.html`; review `docs/superpowers/reviews/2026-10-04-c1-g2-review.md`. No API/objective/pair-function change. No main merge/push. NOT_CALIBRATED.
