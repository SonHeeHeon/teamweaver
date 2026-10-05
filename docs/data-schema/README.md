# 실데이터 스키마 입력 (Claude·Codex 공용)

사용자가 실제 기술 이력 시스템과 피어 리뷰가 **어떤 항목을 어떤 형태로** 갖고 있는지 알려 주는 경로다.
실제 직원 값이 아니라 **스키마(항목·형식·의미·선택지·대략적 분포)와 가린 예시**만 받는다.

## 흐름
1. 사용자가 `docs/data-schema/schema-intake.html`을 브라우저로 열어 채운다.
   - 작성 중인 값은 브라우저에 자동 저장된다(전체는 IndexedDB, 글자 답은 localStorage에도 즉시 저장).
   - 기술 이력 영역은 항목 표 대신 이미지 첨부(`parts.skill.answers.screens`)와 보충 설명을 받는다.
2. "JSON 저장"으로 받은 파일을 **`private/schema-intake.json`**(저장소 루트, gitignore)에 둔다.
   이어서 쓰려면 양식의 "JSON 불러오기"를 쓴다.
3. Claude와 Codex는 이 JSON을 읽는다. worktree에서는 저장소 루트의 절대경로로 읽는다
   (`/Users/honey/Dev/teamweaver/private/schema-intake.json`).
4. 첨부 이미지가 있으면 파일로 꺼내 본다:
   `uv run python scripts/extract_intake_images.py` → `private/schema-intake-images/`
   (`skill-screens-01.png`처럼 영역·질문·순번으로 이름이 붙고, 설명은 `captions.json`에 들어간다).
   - 매번 모든 이미지를 먼저 검증한 뒤 폴더를 통째로 교체하므로, 이전 실행 결과가 섞이지 않는다.
   - 이 스크립트가 만든 폴더(`captions.json` 있음)가 아니면 교체를 거부한다.

## 규칙
- `private/` 내용은 **커밋·push·외부 게시하지 않는다.** 답변을 요약해 Git 추적 문서에 옮길 때도 사내 고유 명칭·선택지 원문은 일반화한다
  (예: "기술 목록 약 120개, 6개 분류").
- JSON의 `parts.confirm.answers.checks`에 공유 전 확인 항목이 모두 체크돼 있지 않으면, 사용하기 전에 사용자에게 확인한다.
- 빈 문자열·빈 목록은 "모름/미입력"이다. 추측으로 채우지 말고, 정한 가정은 가정이라고 표시한다.
- 양식의 질문을 바꾸면 `version`을 올린다. 기존 답변이 새 질문 키를 갖지 않을 수 있다.
  - 현재 버전은 **2**다. 버전 1에서 바뀐 점: `skill.items` 표를 없애고 `skill.screens`(이미지 첨부)와 `skill.screen_notes`를 추가했다.
  - 없어진 질문의 답(예: 버전 1의 `skill.items`)은 버리지 않고 그대로 실어 나른다.

## JSON 구조
```
{ "schema": "teamweaver-schema-intake", "version": 1, "saved_at": "...",
  "table_columns": {"name": "항목명(화면 표기)", ...},
  "parts": {
    "skill"|"review"|"outside"|"rules"|"outcome"|"confirm": {
      "title": "...",
      "answers": { "<key>": { "question": "질문 문구", "answer": <값> } } } } }
```
답의 형태는 질문 유형에 따라 다르다.
- 텍스트: 문자열
- 단일 선택: `{choice, other}`
- 복수 선택: `{choices[], other}`
- 범위: `{min, typical, max}`
- 항목 표: `[{name, format, required, repeat, meaning, choices, example}]` (현재 `review.items`)
- 이미지: `[{id, name, caption, data_url}]`.
  - `data_url`은 PNG·JPEG·GIF·WEBP·BMP의 base64 data URL이다. 형식, 실제 파일 시그니처, 15MB 상한을 양식과 추출 스크립트가 같은 규칙으로 검사한다.
  - `name`은 사용자가 올린 원래 파일명이라 경로로 쓰지 않는다. `id`는 자동 저장 병합용 내부 식별자다.

값은 항상 `parts.<영역>.answers.<키>.answer` 경로에 있다. 예를 들어 배치 규칙의 필수 기술 답은
`parts.rules.answers.must_skill.answer.choice`, 검토 규모는 `parts.outside.answers.scale.answer`다.

## 누가 무엇에 쓰나 (`docs/work-split.md` 기준)
- **Claude**:
  - K2 CSV 입력 계약 v0 — 기술 이력 항목 설명(`parts.skill.answers`)과 `parts.review.answers.items.answer`의 항목을
    `core/ingest/`의 컬럼 매핑으로 옮긴다.
  - 리뷰 근거 표시 범위(K5) — `parts.review.answers.visibility.answer`
- **Codex**:
  - C6 배치 규칙(필수 기술·미기재 등급·최소 투입률) — `parts.rules.answers.*.answer`
  - Phase 1 시나리오·규모 가정 — `parts.outside.answers.scale.answer`
- **공유 계약**: 가상 데이터 생성기(`core/datagen/`)의 분포 가정. 바꾸기 전에 `docs/work-split.md` "요청"에 기록한다.
