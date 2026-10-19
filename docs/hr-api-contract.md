# 인사 시스템 API 연동 계약 (2026-10-19, claude-a)

TeamWeaver는 사람·인사 정보를 **인사 시스템 API에서 주기적으로 받는 것이 기본**이고, 그런 서비스가 없는 회사(또는 표)는 **CSV 묶음 업로드**로 받는다.
두 경로 모두 같은 입력 계약(`core/ingest/contract.py`)과 같은 검사(`core/ingest/loader.load_bundle`)를 거친다. 이 PoC에는 진짜 인사 시스템이 없어
가짜 서버(`scripts/mock_hr_api.py`)로만 시험했고, 시연은 CSV로 한다.

## 1. 켜는 법(서버 환경 변수)

| 변수 | 뜻 |
|---|---|
| `TEAMWEAVER_HR_API_URL` | 인사 시스템 API 기본 주소. 있으면 **API 모드**, 없으면 CSV 모드 |
| `TEAMWEAVER_HR_API_TOKEN` | `Authorization: Bearer <토큰>`으로 보낸다(선택) |
| `TEAMWEAVER_HR_API_CONFIG` | 표별 끝점·칸 이름 대응 JSON 파일(선택, 아래 3절) |

API 모드에서 받는 때:
- **밤 배치 맨 앞**(`api/nightly.Nightly._hr_sync`, 기본 00:00 시작)
- **관리자 "지금 받기"**(설정 화면 → 데이터 원천, `POST /api/hr-source/sync`)

## 2. 끝점 모양

```
GET {URL}/{endpoint}?limit=500[&cursor=<next>]
Authorization: Bearer <토큰>
→ 200 {"items": [ {...}, ... ], "next": "<다음 쪽 커서>" | null}
```

- 항목 칸 이름은 CSV 계약의 열 이름과 같다(다르면 3절 `fields`로 맞춘다). 값은 문자열·숫자·null 모두 된다. 참·거짓은 `Y`/`N`으로 바뀐다.
- 계약에 없는 칸은 버린다. 빠진 필수 칸은 검사가 오류로 알린다.
- `next`가 null이면 끝이다. 같은 커서가 되풀이되거나 응답 모양이 다르거나 401/403/5xx면 **그 받기 전체를 버린다.**
  일부 표만 바꿔 켜지 않는다.

| 표(계약 파일) | 기본 끝점 | 원천 |
|---|---|---|
| people.csv | `people` | 인사 |
| rate_card.csv | `rate-card` | 인사(단가) |
| person_skills.csv | `person-skills` | 기술 이력 서비스 |
| work_history.csv | `work-history` | 기술 이력 서비스(사업별 업무) |
| work_skills.csv | `work-skills` | 기술 이력 서비스(업무별 사용 기술, 선택 표) |
| availability.csv | `availability` | 인사(휴직·휴가) |
| reviews.csv · review_items.csv | `reviews` · `review-items` | 동료 평가 서비스 |
| project_outcomes.csv · replacements.csv | `project-outcomes` · `replacements` | 사업 관리(과거 성과, 선택 표) |
| projects.csv · project_grade_requirements.csv · project_skill_requirements.csv · current_assignments.csv | (기본 안 받음) | **이 서비스**(작업 데이터 판) |

**사업·정원·요구 기술·현재 배치는 이 서비스가 관리한다.** 화면에서 등록한 신규 사업과 확정한 배치가 받기 때문에 사라지지 않게,
받을 때마다 지금 판의 것을 그대로 이어 붙인다. manifest의 단계·동시 수행 허용·수명 주기·계획 기간·갱신 주체도 지금 판의 것을 쓴다.
데이터 기준월 `as_of`는 기술 경력 표를 받았을 때만 받은 달로 바꾼다(기술 경력을 CSV로 두면 그 CSV의 기준월 그대로).

## 3. 설정 JSON(`TEAMWEAVER_HR_API_CONFIG`)

```json
{
  "page_size": 500,
  "timeout_s": 30,
  "tables": {
    "reviews.csv": null,
    "review_items.csv": null,
    "people.csv": {"endpoint": "hr/v2/employees", "fields": {"person_id": "empNo", "display_name": "name"}},
    "projects.csv": {"endpoint": "pms/projects"}
  }
}
```

- `null`: 그 표는 받지 않고 지금 데이터(CSV로 올린 것)를 쓴다. 예를 들어 동료 평가 서비스가 없는 회사가 이렇게 쓴다.
- `endpoint`·`fields`(`{계약 열: 원천 칸}`): 끝점과 칸 이름을 맞춘다. 사업 표에 끝점을 주면 사업도 API에서 받는다(PMS 연동).
- 모르는 표·계약에 없는 열·잘못된 값이면 받지 않고 이유를 알린다. 조용히 기본값으로 받지 않는다.

## 4. 받은 뒤

1. 받은 표를 CSV로 바꿔 지금 판에 갈아 끼운다(`core.ingest.hr_api.merge`).
2. 원문이 지금 판과 같으면 새 판을 만들지 않는다("원천이 그대로").
3. 다르면 업로드와 같은 경로로 검사·켜기를 한다(`api.workspace.commit`, 이력 종류 `hr_sync`, 되돌리기 가능).
   검사 오류면 켜지 않고 지금 데이터를 그대로 둔다. 결과는 `<데이터 폴더>/hr_source/last.json`과 밤 배치 보고서에 남는다.
   켜기를 시작하면 바깥에서 취소해도 켜기·저장·이력을 끝까지 한다. 밤 배치는 시간 안에 안 끝나면 기다리기만 그만두고 지금 데이터로 진행한다.
4. 새 평가 글이 있으면 업로드 때처럼 AI가 글 판정을 한다. 실데이터는 외부 AI 차단 규칙(`api/review_judge`)을 그대로 따른다.

## 5. 진행 중 사업의 기술 경력(개월 수)

- 원천의 기술 경력은 받은 때의 숫자다. 기술 이력 서비스가 진행 중 사업의 경력을 등록 때 숫자로 두면, 달이 지나도 늘지 않는다.
- 기본값(`skill_months_owner = "service"`)에서는 **이 서비스가 늘린다**(`core/ingest/aging.py`).
  - 대상은 진행 중(`status 진행중`) 업무 이력에 등록된 기술(`work_skills.csv`)만이다.
  - 그 경력의 `last_used_month`(원천이 센 마지막 달) 다음 달부터 계획 직전 달까지, 그 업무 기간 안의 달만 더한다.
    같은 기술의 별칭 행(Spark·Apache Spark)은 행마다 따로 따라잡은 뒤 합칠 때 가장 긴 경력을 쓴다.
  - 어디까지 셌는지(`last_used_month`·manifest `as_of`) 모르는 행은 늘리지 않고 경고한다(이중 계산 방지).
  - 최근 10년 창 밖에서 마지막으로 쓴 경력에 최근 달이 붙으면 옛 경력은 되살리지 않고 최근 달만 센다.
  - 한계: 경력이 "합계 개월 하나"로만 들어와 10년 창을 달마다 자를 수 없다. 그래서 창 경계에서 마지막 사용 달이 한 달만 달라도 레벨이 크게 갈리고,
    갱신 주체가 원천이면(`source`) 같은 이력이 입력 단계의 120개월 상한으로 들어가 이 서비스가 늘린 경우와 다를 수 있다.
  - 원천이 이미 갱신했으면 `last_used_month`가 최근이라 두 번 세지 않는다. 경력 행이 없는 기술은 업무 시작 달부터 센다.
- 원천이 매달 갱신하면 설정 화면에서 관리자가 **"원천 시스템이 갱신한다"**(`"source"`)로 바꾼다. 그러면 받은 숫자를 그대로 쓴다.
  바꾸면 작업 데이터의 새 판이 되고(계산 버전이 바뀐다) 되돌릴 수 있다.
- 계획 기간 안에서 달마다 숙련도를 올리지는 않는다(계획 시작 때의 숙련도 하나로 계산한다).

## 6. 시험·점검

```
uv run python scripts/mock_hr_api.py --bundle demo/org-n100-yearend --port 8765 --token dev-token
TEAMWEAVER_HR_API_URL=http://127.0.0.1:8765 TEAMWEAVER_HR_API_TOKEN=dev-token ./run_poc.sh
```

시험:
- `tests/ingest/test_hr_api.py`: 설정·쪽 넘김·인증·응답 모양. 받아 합친 묶음이 CSV 묶음과 같은 데이터가 되는지.
- `tests/ingest/test_skill_aging.py`: 경력 늘리기.
- `tests/scenarios/test_hr_source_api.py`: 받기 → 새 판, 그대로면 새 판 없음, 검사·인증 실패 시 지금 데이터 유지, 갱신 주체, 밤 배치 첫 단계.
