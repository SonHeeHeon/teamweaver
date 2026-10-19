"""Organisation-shaped synthetic bundles for the scale rehearsal (100 / 200 / 300 people).

The plain generator (core/ingest/synthetic.py) draws generic SI staff and projects. This one follows the
user's description of the real organisation (2026-10-05) so the rehearsal looks like the real workload:

- 100 people = data platform delivery group (DP) only; 200 = DP 100 + AI delivery group 100;
  300 = DP 100 + AI 100 + business automation platform delivery group (AU) 100 (user decision 2026-10-05).
- DP work: unstructured-to-metadata conversion and unstructured data marts for financial clients, plus
  internal (group affiliate) data platform work. AI work: AI agent development for financial clients.
- The largest engagement is a joint AI agent programme for a large commercial bank: 8 internal people from
  DP and 20 from AI (outsourced staff are many more but are outside the model -- only internal seats count).

Every value is invented and client names are anonymised ("대형 시중은행 A", "계열사 B"): real company or
client names are never written into the repository. Work history comes first and skills, coworking and
reviews are derived from it, exactly as in synthetic.py whose helpers are reused.
"""
import datetime as dt
import math
import json
import random
from collections import defaultdict
from pathlib import Path

from core.ingest.contract import GRADES, HORIZON_MONTHS, SCHEMA_VERSION
from core.ingest.convert import LOOKBACK_MONTHS
from core.ingest.leave import available_mm, leave_seed, leave_windows, manifest_section
from core.ingest.names import virtual_name
from core.ingest.review_text import ORG_REVIEW_ITEMS, org_review_prose, positive_text
from core.ingest.synthetic import (BASE_RATE, CONSULTING_PREMIUM, GRADE_WEIGHTS, _month_add, _month_end, _months,
                                   _review_rounds, _write_csv)

# v2(2026-10-11): 가상 이름, 아쉬운 점 항목마다 문장·직설형 평가자·어긋난 글.
# v3(2026-10-19, 전일 투입 재설계 -- .omc/plan/2026-10-19-concurrency.md 4절): 가용률 = 휴직·휴가 가정(core/ingest/leave, 모든 묶음이 같은 창),
# 사업 정원 = 등급·달별 (휴직 아닌 그 등급 인원)의 85% 이하(한 사람 한 사업으로 채울 수 있게), 예산 = 기준 단가 × 정원 × 1.0~1.25(사용자 결정),
# 연초 현재 배치 = 전일(1.0)·겹침 없음, 운영 중 = 휴직 인력은 진행 사업·대기 인력에서 뺀다. 사람·이력·평가 글 난수열은 그대로.
GENERATOR = "core.ingest.org_profile v5"
# v5(2026-10-19, 설계 .omc/plan/2026-10-19-lifecycle-v5.md): 사용자 "기존 사업 20개는 여전히 전부 하반기 시작 … 잘못된거같은데" -- 연초 계획의
# 실행 사업 대부분(RUNNING_SHARE)이 계획 전에 시작한 진행 사업(운영 중 묶음과 같은 수명 주기 비율, 운영 = 내부 2~3명). 사용자 "진행해야겠어"
# (선택 "갈아 끼우고 평가도 새로") -- 진행 사업 현재 팀원의 업무 이력·기술 경력·동료 평가를 그 사업에 잇는다(`_link_ongoing`).
# 사람(people.csv)·과거 성과·교체 기록과, 끊긴 이력과 맞는 원래 평가 글은 그대로다(새 평가 글만 서비스 AI로 판정해 동봉).
# v4(2026-10-19, 설계 .omc/plan/2026-10-19-lifecycle-mix.md): 사업 수명 주기 섞기 -- 사용자 "24년에 시작해서 아직 진행중인 플젝(… 25~26년엔 운영),
# 26년엔 운영에 클라우드 마이그레이션 … 추가 과제가 있어 6달만 회사 인력은 2명만 추가(외주는 여러명)", "26년 상반기에 시작된것도 있을거고 25년에 했는데
# 추가로 고도화 과제가 떠서 하고있는것도". 별도 난수열이라 사람·이력·평가 글·과거 성과는 v3와 바이트까지 같다(동봉 판정 그대로).
RUNNING_SHARE = 0.75                # v5 연초 계획: 실행 사업 중 계획 전에 시작해 진행 중인 몫(나머지는 계획 안에서 새로 시작) -- 사용자 "전부 하반기 시작은 잘못"
ADDONS = {100: 1, 200: 2, 300: 2}   # 연초 계획: 운영 사업에 붙는 추가 과제 수(운영 중 묶음은 신규 제안 중 1개) -- 사용자 "한번에 1-2개"
JOIN_MONTHS = (3, 30)               # v5: 진행 사업 팀원의 합류 = 계획 시작 3~30개월 전(사업 시작 뒤) -- 그때부터 업무 이력이 그 사업이다
STAGE_AREA = {"운영": "운영·유지보수", "고도화": "고도화 개발", "개발": "개발"}
SECTOR_DOMAIN = {"대외금융": "금융 업무", "대내": "그룹사 업무", "대외공공": "공공 업무"}
ADDON_MONTHS = 6                    # 추가 과제 기간(사용자 "6달만") -- 계획 끝을 넘으면 로더가 자른다
ADDON_SKILLS = ("Private Cloud", "Kubernetes")       # 클라우드 마이그레이션(+수정 개발) -- 상위 사업 기술 1~2개를 더한다
EXTERNAL_RANGE = (3, 6)             # 추가 과제 외주 인원(참고값, 계산에 안 씀 -- 사용자 "외주는 여러명")
OPERATING_LIFE = (("운영", 0.3), ("고도화", 0.2), ("개발2025", 0.2), ("개발2026", 0.3))   # 운영 중 묶음 진행 사업의 수명 주기 비율


def lifecycle_seed(seed: int) -> int:
    """사업 수명 주기 난수열(v4) -- 사람·이력·평가 글을 만드는 난수열과 따로라 v3 묶음과 그 표가 그대로다."""
    return seed * 6700417 + 17


def _life_start(lrng: random.Random, kind: str, first: dt.date) -> str:
    """수명 주기 종류 → 실제 시작 달(YYYY-MM), **계획 시작 달 기준**: 운영 22~33개월 전, 고도화·개발2025 10~21개월 전, 개발2026 4~9개월 전.
    2026-10 시작이면 각각 2024년 · 2025년 · 2026년 1~6월이다(같은 난수 하나 -- 예전 연도 고정식과 같은 결과). 계획 시작을 바꿔도 늘 그 전이다
    (Codex 리뷰: 연도를 박아 두면 2026-01 시작 묶음에서 시작 2026-05 · 끝 2026-03 사업이 생겼다)."""
    hi, back = {"운영": (12, 34), "고도화": (12, 22), "개발2025": (12, 22), "개발2026": (6, 10)}[kind]
    return _month_add(first, lrng.randint(1, hi) - back).strftime("%Y-%m")


def _life_record(kind: str, started: str) -> dict:
    return {"stage": {"운영": "운영", "고도화": "고도화"}.get(kind, "개발"), "started": started}


# 이름 머리말은 사업 **종류**만(운영·고도화 -- 추가 과제는 아래에서 따로). 단계 말("[실행]"·"[제안]")은 붙이지 않는다: 단계는 화면에서
# 바뀌어(제안 → 발주 예정 → 수주) 이름의 단계 말이 낡는다 -- 사용자 2026-10-20 "[제안]은 빼자 … 추가 과제나 이런건 앞에 붙이면 좋은데
# 아닌 것들은 안붙이는게 좋은거같아"(claude-b, 사용자 지시로 claude-a 영역 수정). 개발 단계는 수명 주기 칩이 보인다. 난수 소비는 그대로.
LIFE_LABEL = {"운영": "[운영]", "고도화": "[고도화]", "개발2025": "", "개발2026": ""}


def _labeled(kind: str, name: str) -> str:
    return f"{LIFE_LABEL[kind]} {name}" if LIFE_LABEL[kind] else name
PEAK_CAP = 0.85                     # 등급·달별 정원 합 / 그 달 그 등급 가용 인원 상한(예전 전체 85% 규칙을 등급별로)
BUDGET_RANGE = (1.0, 1.25)          # 예산 = 기준 단가 × 정원 × uniform(BUDGET_RANGE) -- 사용자 결정 2026-10-19(예전 0.9~1.15)
BLUNT_SHARE, MISMATCH_SHARE = 0.08, 0.05        # 직설형 평가자 비율(평가자 단위) · 항목과 글이 어긋나는 리뷰 비율(claude-b 요청 5~10%)
SIZES = {100: {"DP": 100}, 200: {"DP": 100, "AI": 100}, 300: {"DP": 100, "AI": 100, "AU": 100}}
# the export keeps only the last LOOKBACK_MONTHS (10 years, same constant as the loader) -- careers can be longer
# (20+ years), but old work and skills are not remembered well (user decision 2026-10-05)
CAREER_MONTHS = 300        # careers are simulated up to 25 years back, then cut to the lookback window
TENURE = {"초급": (12, 72), "중급": (60, 120), "고급": (108, 192), "특급": (156, 300)}   # months of service by grade
LLM_ERA_MONTHS = 36           # LLM-era skills only exist in the last three years of history

CATALOG = {
    "Programming Language": ["Python", "Java", "SQL", "Scala"],
    "Data": ["Spark", "Kafka", "Airflow", "ETL", "Hadoop", "Data Lake & Data Catalog", "메타데이터 관리",
             "데이터 모델링", "비정형 문서 처리(OCR)", "검색엔진", "BI Reporting"],
    "AI": ["LLM Application", "RAG·벡터DB", "AI 에이전트 프레임워크", "프롬프트 엔지니어링", "LLM 파인튜닝",
           "ML Ops", "Text Analysis"],
    "Automation": ["RPA", "워크플로우·BPM", "로우코드 플랫폼", "지능형 문서 처리(IDP)", "프로세스 마이닝"],
    "Framework": ["Spring", "FastAPI", "React"],
    "Cloud": ["AWS", "Private Cloud", "Kubernetes", "Docker"],
    "Domain": ["금융 업무", "그룹사 업무", "공공 업무"],
}
SKILL_CATEGORY = {s: c for c, skills in CATALOG.items() for s in skills}
LLM_ERA = {"LLM Application", "RAG·벡터DB", "AI 에이전트 프레임워크", "프롬프트 엔지니어링", "LLM 파인튜닝"}

# (family, weight, consulting?, skill pool)
FAMILIES = {
    "DP": [("데이터 엔지니어", 0.30, False, ["Python", "SQL", "Spark", "Kafka", "Airflow", "ETL", "Hadoop", "Scala", "AWS"]),
           ("비정형·문서AI", 0.25, False, ["Python", "비정형 문서 처리(OCR)", "Text Analysis", "검색엔진", "메타데이터 관리", "ETL"]),
           ("데이터 모델러·메타데이터", 0.20, False, ["SQL", "데이터 모델링", "메타데이터 관리", "Data Lake & Data Catalog", "BI Reporting"]),
           ("데이터 플랫폼", 0.15, False, ["AWS", "Private Cloud", "Kubernetes", "Docker", "Hadoop", "Kafka", "Data Lake & Data Catalog"]),
           ("금융 데이터 컨설턴트", 0.10, True, ["SQL", "데이터 모델링", "BI Reporting", "메타데이터 관리"])],
    "AI": [("LLM·에이전트 개발", 0.35, False, ["Python", "LLM Application", "AI 에이전트 프레임워크", "프롬프트 엔지니어링", "FastAPI", "RAG·벡터DB"]),
           ("RAG·검색", 0.20, False, ["Python", "RAG·벡터DB", "검색엔진", "Text Analysis", "LLM Application"]),
           ("MLOps", 0.15, False, ["ML Ops", "Kubernetes", "Docker", "AWS", "Python", "LLM 파인튜닝"]),
           ("AI 서비스 백엔드", 0.20, False, ["Java", "Spring", "FastAPI", "Python", "Docker", "React"]),
           ("AI 컨설턴트", 0.10, True, ["LLM Application", "프롬프트 엔지니어링", "Text Analysis"])],
    "AU": [("RPA 개발", 0.35, False, ["RPA", "Python", "SQL", "지능형 문서 처리(IDP)", "Java"]),
           ("워크플로우·BPM", 0.20, False, ["워크플로우·BPM", "Java", "Spring", "SQL", "React"]),
           ("로우코드 플랫폼", 0.20, False, ["로우코드 플랫폼", "React", "워크플로우·BPM", "SQL", "Docker"]),
           ("문서 자동화", 0.15, False, ["지능형 문서 처리(IDP)", "비정형 문서 처리(OCR)", "Python", "RPA", "LLM Application"]),
           ("업무자동화 컨설턴트", 0.10, True, ["프로세스 마이닝", "RPA", "워크플로우·BPM"])],
}
# past-work domain mix per group
DOMAINS = {"DP": (("금융 업무", 0.5), ("그룹사 업무", 0.4), ("공공 업무", 0.1)),
           "AI": (("금융 업무", 0.6), ("그룹사 업무", 0.4)),
           "AU": (("그룹사 업무", 0.6), ("금융 업무", 0.4))}

PAST_WORK = {   # (group, domain) -> (work name templates, work areas used in review prose)
    ("DP", "금융 업무"): (["{c} 비정형 문서 메타데이터 전환", "{c} 데이터 마트 구축", "{c} 차세대 데이터 플랫폼 구축"],
                       ["데이터 이관", "메타데이터 설계", "적재 파이프라인"]),
    ("DP", "그룹사 업무"): (["{c} 데이터 거버넌스 구축", "{c} 데이터 레이크 고도화"], ["데이터 표준화", "카탈로그 구축", "배치 운영"]),
    ("DP", "공공 업무"): (["{c} 공공데이터 개방 플랫폼 구축"], ["개방 API", "데이터 정제"]),
    ("AI", "금융 업무"): (["{c} AI 상담 에이전트 개발", "{c} 문서 요약 LLM 도입"], ["에이전트 설계", "RAG 검색", "프롬프트 튜닝"]),
    ("AI", "그룹사 업무"): (["{c} 사내 AI 어시스턴트 구축"], ["검색 품질 개선", "에이전트 설계", "서빙 운영"]),
    ("AU", "그룹사 업무"): (["{c} RPA 확산", "{c} 업무 워크플로우 플랫폼 구축"], ["봇 개발", "프로세스 분석", "결재 흐름 설계"]),
    ("AU", "금융 업무"): (["{c} 지능형 문서처리 자동화"], ["문서 인식", "예외 처리 설계"]),
}
INDUSTRY = {"금융 업무": "금융", "그룹사 업무": "그룹사(대내)", "공공 업무": "공공"}
FIN = [f"금융사 {c}{n}" for n in ("", "2") for c in "가나다라마바사아자차카타파하"]   # 300명 규모에서도 이름이 겹치지 않게
AFF = [f"계열사 {c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
PUB = [f"공공기관 {c}" for c in "갑을병정무기경신"]
# (name template, sector, client pool, skills, weight)
PROJECT_TYPES = {
    "DP": [("{c} 비정형 문서 메타데이터 전환", "대외금융", FIN, ["비정형 문서 처리(OCR)", "메타데이터 관리", "Text Analysis", "ETL", "금융 업무"], 0.30),
           ("{c} 비정형 데이터 마트 구축", "대외금융", FIN, ["Data Lake & Data Catalog", "ETL", "Spark", "데이터 모델링", "금융 업무"], 0.25),
           ("{c} 데이터 플랫폼 고도화", "대내", AFF, ["Spark", "Kafka", "Airflow", "Private Cloud", "Kubernetes"], 0.20),
           ("{c} 데이터 거버넌스·카탈로그 구축", "대내", AFF, ["메타데이터 관리", "Data Lake & Data Catalog", "데이터 모델링"], 0.15),
           ("{c} 데이터 개방 플랫폼", "대외공공", PUB, ["ETL", "SQL", "BI Reporting", "공공 업무"], 0.10)],
    "AI": [("{c} AI 에이전트 개발", "대외금융", FIN, ["LLM Application", "AI 에이전트 프레임워크", "RAG·벡터DB", "프롬프트 엔지니어링", "금융 업무"], 0.50),
           ("{c} 사내 AI 어시스턴트", "대내", AFF, ["LLM Application", "RAG·벡터DB", "검색엔진", "FastAPI"], 0.30),
           ("{c} 상담 문서 AI 분석", "대외금융", FIN, ["Text Analysis", "LLM Application", "ML Ops", "금융 업무"], 0.20)],
    "AU": [("{c} 업무자동화(RPA) 확산", "대내", AFF, ["RPA", "지능형 문서 처리(IDP)", "프로세스 마이닝", "그룹사 업무"], 0.40),
           ("{c} 지능형 문서처리 자동화", "대외금융", FIN, ["지능형 문서 처리(IDP)", "비정형 문서 처리(OCR)", "RPA", "금융 업무"], 0.25),
           ("{c} 업무 워크플로우 플랫폼 구축", "대내", AFF, ["워크플로우·BPM", "로우코드 플랫폼", "Java", "React"], 0.20),
           ("{c} AI 기반 업무자동화", "대외금융", FIN, ["RPA", "LLM Application", "AI 에이전트 프레임워크", "금융 업무"], 0.15)],
}
FLAGSHIP = {
    "project_id": "J001",
    "project_name": "대형 시중은행 A AI 에이전트 개발(데이터플랫폼·AI 공동)",
    # internal seats only -- outsourced staff are outside the model
    "seats": {"DP": {"특급": 1, "고급": 3, "중급": 3, "초급": 1},
              "AI": {"특급": 1, "고급": 5, "중급": 9, "초급": 5}},
    "skills": {"DP": [("메타데이터 관리", 3, 24), ("ETL", 2, 24), ("비정형 문서 처리(OCR)", 2, 24), ("금융 업무", 3, 36)],
               "AI": [("LLM Application", 6, 12), ("AI 에이전트 프레임워크", 5, 12), ("RAG·벡터DB", 4, 12),
                      ("프롬프트 엔지니어링", 3, 12), ("Kubernetes", 2, 24)]},
}


def _people(rng: random.Random, groups: dict[str, int]) -> list[dict]:
    out = []
    for g, n in groups.items():
        fams = FAMILIES[g]
        for i in range(n):
            fam = rng.choices(fams, weights=[f[1] for f in fams])[0]
            out.append({"person_id": f"{g}{i + 1:04d}", "display_name": virtual_name(f"{g}{i + 1:04d}"),   # 가상 이름(2026-10-11, 별도 난수열)
                        "career_grade": rng.choices(GRADES, weights=GRADE_WEIGHTS)[0],
                        "role_type": "컨설팅" if fam[2] else "개발", "job_family": f"{g}·{fam[0]}",
                        "_group": g, "_pool": fam[3]})
    return out


def _work_history(rng: random.Random, people: list[dict], last: dt.date):
    """Past project codes per group; AI codes and LLM-era skills live only in the last LLM_ERA_MONTHS."""
    origin = _month_add(last, -CAREER_MONTHS)
    llm_origin = _month_add(last, -(LLM_ERA_MONTHS - 1))      # inclusive window: exactly LLM_ERA_MONTHS months
    codes = defaultdict(list)
    by_group = defaultdict(int)
    for p in people:
        by_group[p["_group"]] += 1
    k = 0
    for g, n in by_group.items():
        span_origin, span = (llm_origin, LLM_ERA_MONTHS) if g == "AI" else (origin, CAREER_MONTHS)
        for _ in range(max(8, n) * span // 120):        # about the same number running at any time
            k += 1
            start = _month_add(span_origin, rng.randrange(0, span - 4))
            end = min(_month_add(start, rng.randint(4, 18) - 1), last)
            dom = rng.choices([d for d, _ in DOMAINS[g]], weights=[w for _, w in DOMAINS[g]])[0]
            names, areas = PAST_WORK[(g, dom)]
            client = rng.choice(FIN if dom == "금융 업무" else AFF if dom == "그룹사 업무" else PUB)
            codes[g].append({"code": f"X-{k:04d}", "start": start, "end": end, "domain": dom, "group": g,
                             "client": client, "work_name": rng.choice(names).format(c=client),
                             "area": rng.choice(areas)})
    works, work_skills = [], []
    n = 0
    tech = [s for s in SKILL_CATEGORY if SKILL_CATEGORY[s] != "Domain"]
    all_codes = [c for cs in codes.values() for c in cs]
    for p in people:
        lo_t, hi_t = TENURE[p["career_grade"]]
        t = _month_add(last, -rng.randint(lo_t, hi_t) + 1)
        while t <= last:
            # continuous career: the next assignment starts right after the previous one (0-2 month gap)
            own = [c for c in codes[p["_group"]] if c["start"] <= t <= c["end"]]
            other = [c for c in all_codes if c["start"] <= t <= c["end"]]
            pool = own if own and rng.random() > 0.1 else other
            if not pool:
                t = _month_add(t, 1)
                continue
            code = rng.choice(pool)
            s = t
            e = min(_month_add(s, rng.randint(3, 12) - 1), code["end"], last)
            n += 1
            status = "진행중" if e >= last else rng.choices(("확정완료", "미등록"), weights=(0.85, 0.15))[0]
            used = rng.sample(p["_pool"], min(len(p["_pool"]), rng.randint(3, 6)))
            extra = rng.sample([x for x in tech if x not in p["_pool"]], rng.randint(0, 2))
            row = {"person_id": p["person_id"], "work_id": f"W{n:06d}", "project_code": code["code"],
                   "start_date": s.isoformat(), "end_date": _month_end(e).isoformat(), "status": status,
                   "work_name": code["work_name"], "client": code["client"],
                   "industry": INDUSTRY[code["domain"]],
                   "_start": s, "_end": e, "_code": code, "_used": list(used), "_ws": [],
                   "_all": list(dict.fromkeys(used + extra + [code["domain"]]))}     # 업무별 사용 기술(work_skills.csv, 2026-10-19)
            works.append(row)
            for skill in dict.fromkeys(used + extra + [code["domain"]]):
                ss = max(s, llm_origin) if skill in LLM_ERA else s
                if ss <= e:
                    row["_ws"].append(len(work_skills))
                    work_skills.append((p["person_id"], skill, ss, e))
            t = _month_add(e, 1 + rng.choice((0, 0, 0, 1, 2)))
    return works, work_skills, [c for cs in codes.values() for c in cs]


def _apply_lookback(works: list[dict], work_skills: list, last: dt.date):
    """The export keeps the last LOOKBACK_MONTHS: older rows are dropped, rows that started earlier are cut to the
    window start (their skill months too). The hidden outcome rule was computed on the full career before this."""
    start = _month_add(last, -(LOOKBACK_MONTHS - 1))
    kept = []
    for w in works:
        if w["_end"] < start:
            continue
        if w["_start"] < start:
            w["_start"], w["start_date"] = start, start.isoformat()
        kept.append(w)
    skills = [(pid, sk, max(ss, start), e) for pid, sk, ss, e in work_skills if e >= start]
    return kept, skills


def _describe_works(works: list[dict], last: dt.date) -> None:
    """Work summaries and the skills reviews may mention, from each row's FINAL period (replacements cut some
    rows short) -- no "LLM Application" on work that ended before the LLM era."""
    llm_origin = _month_add(last, -(LLM_ERA_MONTHS - 1))
    for w in works:
        in_era = [x for x in w["_used"] if x not in LLM_ERA or w["_end"] >= llm_origin]
        w["summary"] = (f"{w['work_name']}에서 {w['_code']['area']} 담당"
                        + (f"({', '.join(in_era[:3])} 활용)" if in_era else ""))
        w["_skills"] = in_era or [w["_code"]["domain"]]


def _person_traits(rng: random.Random, people: list[dict]) -> None:
    """Each person gets a hidden true ability (used only by the outcome generator) and a stable strength /
    weakness profile, so that different colleagues' reviews of one person point the same way."""
    for p in people:
        p["_ability"] = rng.gauss(0.0, 1.0)
        # what colleagues can see is only partly the true ability (target correlation ~0.45), and reviewers
        # differ in how generous they are -- so reviews cannot simply read the hidden ability back
        p["_visible"] = 0.45 * p["_ability"] + 0.89 * rng.gauss(0.0, 1.0)
        p["_leniency"] = rng.gauss(0.0, 0.6)
        traits = rng.sample(ORG_REVIEW_ITEMS, 9)
        p["_strengths"], p["_weaknesses"] = traits[:5], traits[5:]


def _pick(rng: random.Random, preferred: list[str], k: int, exclude: set[str]) -> list[str]:
    """Mostly from the person's profile, sometimes anything else (reviewers do not agree perfectly)."""
    out = []
    pool_other = [x for x in ORG_REVIEW_ITEMS if x not in preferred]
    while len(out) < k:
        src = preferred if rng.random() < 0.75 else pool_other
        cand = [x for x in src if x not in out and x not in exclude]
        if not cand:
            cand = [x for x in ORG_REVIEW_ITEMS if x not in out and x not in exclude]
        out.append(rng.choice(cand))
    return out


def _org_reviews(rng: random.Random, works: list[dict], people: list[dict], rounds, *, seed: int = 0,
                 blunt_share: float = BLUNT_SHARE, mismatch_share: float = MISMATCH_SHARE, id_start: int = 0):
    """Half-yearly peer reviews among people active on the same past project (~5 per person per round, i.e.
    ~10 a year as in the schema answer). Strength count leans on the hidden ability; prose from review_text.
    2026-10-11: rng picks the items exactly as before (positive_text still draws from it in the same order); the
    weakness prose, blunt reviewers and tone mismatches draw only from per-reviewer / per-review streams, so the picked
    items -- and everything after them -- are unchanged. Returns (reviews, review_items, style) where style says who
    is blunt and which reviews disagree with their items (ground truth for judge experiments, not a contract column)."""
    blunt_set = set()                                    # 그룹마다 정확히 round(비율 × 인원)명 -- 같은 그룹이면 묶음 크기와 무관하게 같은 사람
    for g in sorted({q["_group"] for q in people}):
        members = sorted((q["person_id"] for q in people if q["_group"] == g),
                         key=lambda pid: (random.Random(f"{seed}:blunt:{pid}").random(), pid))
        blunt_set.update(members[:round(blunt_share * len(members))])
    style_of = {"blunt_reviewers": set(), "blunt": [], "harsh": [], "soft": []}
    by_id = {p["person_id"]: p for p in people}
    by_code = defaultdict(list)
    for w in works:
        by_code[w["project_code"]].append(w)
    reviews, review_items = [], []
    n = id_start                                       # v5: 진행 사업 평가는 원래 평가 번호 뒤에 붙인다
    for round_id, reviewed_at in rounds:
        lo = _month_add(dt.date(reviewed_at.year, reviewed_at.month, 1), -5)
        hi = dt.date(reviewed_at.year, reviewed_at.month, 1)
        reviewers_of = defaultdict(dict)                 # reviewee -> {reviewer: work row of the reviewee}
        for code, ws in by_code.items():
            active = [w for w in ws if w["_start"] <= hi and w["_end"] >= lo]
            for a in active:
                for b in active:
                    if a["person_id"] != b["person_id"] and a["_start"] <= b["_end"] and b["_start"] <= a["_end"]:
                        reviewers_of[b["person_id"]].setdefault(a["person_id"], b)
        for reviewee, cands in sorted(reviewers_of.items()):
            chosen = rng.sample(sorted(cands), min(len(cands), rng.randint(3, 6)))
            p = by_id[reviewee]
            for reviewer in chosen:
                w = cands[reviewer]
                lenient = by_id[reviewer]["_leniency"]
                k_pos = max(1, min(5, round(2.5 + 0.9 * p["_visible"] + lenient + rng.gauss(0, 0.8))))
                k_neg = max(1, min(5, round(2.0 - 0.6 * p["_visible"] - 0.5 * lenient + rng.gauss(0, 0.8))))
                pos = _pick(rng, p["_strengths"], k_pos, set())
                neg = _pick(rng, p["_weaknesses"], k_neg, set(pos))
                skill = rng.choice(w["_skills"])
                n += 1
                rid = f"R{n:06d}"
                praise = positive_text(rng, pos, work_name=w["work_name"], domain=w["industry"],
                                       skill=skill, area=w["_code"]["area"], grade=p["career_grade"])
                prng = random.Random(f"{seed}:text:{rid}")
                style = "blunt" if reviewer in blunt_set else "normal"
                if style == "blunt":
                    style_of["blunt_reviewers"].add(reviewer)
                if prng.random() < mismatch_share:              # 항목과 글의 온도가 어긋남: 항목이 좋은 쪽이면 글이 독하고, 나쁜 쪽이면 부드럽다(직설형보다 우선)
                    style = "harsh" if len(pos) >= len(neg) else "soft"
                if style != "normal":
                    style_of[style].append(rid)
                pos_text, neg_text = org_review_prose(prng, style, pos, neg, praise, skill=skill)
                reviews.append({
                    "review_id": rid, "review_round": round_id, "project_code": w["project_code"],
                    "reviewer_id": reviewer, "reviewee_id": reviewee, "reviewed_at": reviewed_at.isoformat(),
                    "positive_text": pos_text, "negative_text": neg_text})
                review_items += [{"review_id": rid, "polarity": "positive", "item": x} for x in pos]
                review_items += [{"review_id": rid, "polarity": "negative", "item": x} for x in neg]
    style = {"blunt_share": blunt_share, "mismatch_share": mismatch_share, "blunt_reviewers": sorted(style_of["blunt_reviewers"]),
             "blunt_reviews": style_of["blunt"], "harsh_reviews": style_of["harsh"], "soft_reviews": style_of["soft"],
             "note": "시연 글 성향(정답 보조, 계약 칸 아님): 리뷰마다 normal·blunt·harsh·soft 중 하나 -- 세 목록에 없는 리뷰는 normal. "
                     "blunt = 직설형 평가자(blunt_reviewers)의 글(짧은 칭찬, 직설적인 아쉬운 점), harsh = 항목보다 글이 독함, soft = 항목보다 글이 부드러움. "
                     "어긋남(harsh·soft)이 직설형보다 우선해, 직설형 평가자의 리뷰 일부는 harsh·soft에 있다. 고른 항목(review_items.csv)은 성향과 무관하다."}
    return reviews, review_items, style


def ongoing_seed(seed: int) -> int:
    """v5 진행 사업 이력 잇기 난수열(합류 달·쓴 기술) -- 다른 표의 난수열과 따로."""
    return seed * 2750161 + 19


def _link_ongoing(hrng: random.Random, people: list[dict], works: list[dict], work_skills: list, projects: list[dict],
                  roster: list[dict], last: dt.date, first: dt.date, reviews: list[dict], review_items: list[dict],
                  review_style: dict, *, seed: int, blunt_share: float, mismatch_share: float,
                  replacements: list[dict] | None = None):
    """v5(2026-10-19, 사용자 "진행해야겠어" · 선택 "갈아 끼우고 평가도 새로"): 계획 전에 시작한 진행 사업의 현재 팀원은 합류 달(계획 시작
    3~30개월 전, 사업 시작 뒤)부터 업무 이력이 그 사업이다 -- 지난 이력은 합류 직전 달에서 끊고(그 뒤에 시작한 줄은 지움) 진행 사업 줄
    (합류 ~ 계획 직전, "진행중")을 넣는다. 기술 경력은 바뀐 이력에서 다시 센다(person_skills는 쓸 때 work_skills로 만든다).
    평가는 **원래 이력으로 이미 만든 것**을 받아, 끊긴 이력과 맞지 않게 된 평가(평가 기간에 두 사람이 그 사업에 함께 있지 않음)만 지우고,
    진행 사업 팀원끼리의 평가를 같은 생성기로 새로 만들어 뒤에 붙인다 -- 남는 평가 글은 v4와 같아 판정 동봉이 그대로 쓰인다(새 글만 판정).
    반환: (이력, 기술 경력, 평가, 평가 항목, 글 성향, manifest 기록)."""
    by_id = {p["person_id"]: p for p in people}
    proj = {p["project_id"]: p for p in projects}
    llm_origin = _month_add(last, -(LLM_ERA_MONTHS - 1))
    # 교체 기록(replacements.csv)은 "그 사업 이력이 교체 날짜에 끝난다"가 맞아야 한다 -- 합류는 그 사람의 마지막 교체 다음 달부터
    first_work = {}                                     # 그 사람의 첫 이력 -- 합류가 입사(첫 이력) 앞으로 가지 않게(폴백 리뷰 P3)
    for w in works:
        first_work[w["person_id"]] = min(first_work.get(w["person_id"], w["_start"]), w["_start"])
    replaced = {}
    for x in replacements or []:
        d = dt.date.fromisoformat(x["replaced_at"])
        replaced[x["person_id"]] = max(replaced.get(x["person_id"], d), d)
    joins: dict[str, tuple[dt.date, dict]] = {}
    for r in sorted(roster, key=lambda r: (r["person_id"], r["project_id"])):
        j = proj.get(r["project_id"])
        life = (j or {}).get("_life")
        if not life or life["stage"] == "추가 과제" or r["person_id"] in joins:
            continue
        y, m = map(int, life["started"].split("-"))
        started = dt.date(y, m, 1)
        if started >= first:
            continue
        span = _months(started, last)
        tenure = hrng.randint(min(JOIN_MONTHS[0], span), min(JOIN_MONTHS[1], span))
        jn = _month_add(last, -(tenure - 1))
        if r["person_id"] in replaced:
            rd = replaced[r["person_id"]]
            jn = max(jn, _month_add(dt.date(rd.year, rd.month, 1), 1))
        if r["person_id"] in first_work:
            jn = max(jn, first_work[r["person_id"]])
        if jn <= last:
            joins[r["person_id"]] = (jn, j)
    kept = []
    for w in works:
        jn = joins.get(w["person_id"], (None,))[0]
        if jn is None or w["_end"] < jn:
            kept.append(w)
            continue
        if w["_start"] >= jn:
            continue                                    # 합류 뒤에 시작한 지난 이력은 없던 일
        w["_end"] = _month_add(jn, -1)
        w["end_date"] = _month_end(w["_end"]).isoformat()
        if w["status"] == "진행중":
            w["status"] = "확정완료"
        kept.append(w)
    skills = []
    for t in work_skills:                               # 한 사람은 한 번에 한 이력(연속 경력)이라 합류 뒤 기술 달은 모두 끊긴 줄의 것
        pid, sk, ss, e = t
        jn = joins.get(pid, (None,))[0]
        if jn is None or e < jn:
            skills.append(t)
        elif ss <= _month_add(jn, -1):
            skills.append((pid, sk, ss, _month_add(jn, -1)))
    n_w = max(int(w["work_id"][1:]) for w in works)
    j_works = []
    for pid, (jn, j) in sorted(joins.items()):
        p = by_id[pid]
        stage = j["_life"]["stage"]
        domain = SECTOR_DOMAIN.get(j["sector"], "금융 업무")
        fit = [x for x in j.get("_skills", []) if x in p["_pool"]]
        rest = [x for x in p["_pool"] if x not in fit]
        used = list(dict.fromkeys(fit + hrng.sample(rest, min(len(rest), hrng.randint(1, 2)))))
        n_w += 1
        area = STAGE_AREA[stage]
        j_works.append({"person_id": pid, "work_id": f"W{n_w:06d}", "project_code": j["project_id"],
                        "start_date": jn.isoformat(), "end_date": _month_end(last).isoformat(), "status": "진행중",
                        "work_name": j["_base"], "client": j["_client"], "industry": INDUSTRY[domain],
                        "summary": f"{j['_base']}에서 {area} 담당" + (f"({', '.join(used[:3])} 활용)" if used else ""),
                        "_start": jn, "_end": last, "_code": {"area": area, "domain": domain}, "_used": used,
                        "_skills": used or [domain], "_all": list(dict.fromkeys(used + [domain]))})
        for sk in dict.fromkeys(used + [domain]):
            ss = max(jn, llm_origin) if sk in LLM_ERA else jn
            if ss <= last:
                skills.append((pid, sk, ss, last))
    order = {p["person_id"]: k for k, p in enumerate(people)}
    all_works = sorted(kept + j_works, key=lambda w: (order[w["person_id"]], w["_start"], w["work_id"]))
    # 평가: 합류한 사람이 낀 평가 중, 평가 기간(직전 6개월)에 두 사람이 그 사업에 함께 있지 않게 된 것만 지운다
    rows_by = defaultdict(list)
    for w in kept:
        rows_by[(w["person_id"], w["project_code"])].append((w["_start"], w["_end"]))

    def still_true(r: dict) -> bool:
        if r["reviewer_id"] not in joins and r["reviewee_id"] not in joins:
            return True
        d = dt.date.fromisoformat(r["reviewed_at"])
        hi = dt.date(d.year, d.month, 1)
        lo = _month_add(hi, -5)
        a = [x for x in rows_by[(r["reviewer_id"], r["project_code"])] if x[0] <= hi and x[1] >= lo]
        b = [x for x in rows_by[(r["reviewee_id"], r["project_code"])] if x[0] <= hi and x[1] >= lo]
        return any(a1 <= b2 and a2 <= b1 for a1, b1 in a for a2, b2 in b)
    kept_reviews = [r for r in reviews if still_true(r)]
    kept_ids = {r["review_id"] for r in kept_reviews}
    new_reviews, new_items, new_style = _org_reviews(random.Random(seed * 1299721 + 23), j_works, people,
                                                     _review_rounds(first), seed=seed, blunt_share=blunt_share,
                                                     mismatch_share=mismatch_share, id_start=len(reviews))
    style = dict(review_style)
    still = {r["reviewer_id"] for r in kept_reviews} | {r["reviewer_id"] for r in new_reviews}   # 평가가 모두 지워진 평가자는 뺀다
    style["blunt_reviewers"] = sorted((set(review_style["blunt_reviewers"]) | set(new_style["blunt_reviewers"])) & still)
    for key in ("blunt_reviews", "harsh_reviews", "soft_reviews"):
        style[key] = [x for x in review_style[key] if x in kept_ids] + new_style[key]
    record = {"people": len(joins), "dropped_reviews": len(reviews) - len(kept_reviews), "new_reviews": len(new_reviews),
              "join_months": list(JOIN_MONTHS),
              "note": "생성기 v5: 계획 전에 시작한 진행 사업의 현재 팀원은 합류 달부터 업무 이력이 그 사업이다(지난 이력은 합류 직전에서 끊음). "
                      "끊긴 이력과 맞지 않게 된 평가는 지우고 진행 사업 팀원끼리의 평가를 새로 만들었다(번호는 원래 평가 뒤)."}
    return (all_works, skills, kept_reviews + new_reviews,
            [x for x in review_items if x["review_id"] in kept_ids] + new_items, style, record)


CUSTOMER_REASONS = ("고객 요청 - 역량 부족", "고객 요청 - 소통 문제", "고객 요청 - 일정 지연 책임")
INTERNAL_REASONS = ("타 사업 긴급 차출", "개인 사정(휴직·이직)", "역할 재조정")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _past_outcomes(rng: random.Random, past_codes: list[dict], works: list[dict], people: list[dict], last: dt.date,
                   work_skills: list):
    """Outcomes of finished past projects from a rule the scoring model does not use as such: true ability
    (reviews reflect only a visible part of it, correlation ~0.45, blurred by reviewer leniency), how many of
    the team had worked together before (overlaps the model's collaboration term), prior experience in the
    project's industry (overlaps domain skill months), churn (people staying < 4 months), a per-client affinity
    and luck. The overlap is deliberate realism -- outcomes do relate to visible signals -- and is reported as such.
    Customer score 1-5, schedule kept or late, follow-on Y/N; low scores lead to customer-requested
    replacements of the weakest members, and some neutral internal replacements happen anyway."""
    by_id = {p["person_id"]: p for p in people}
    by_code = defaultdict(list)
    for w in works:
        by_code[w["project_code"]].append(w)
    history = defaultdict(list)
    for w in works:
        history[w["person_id"]].append(w)
    client_eff = {}
    raw = []
    for code in past_codes:
        team = by_code.get(code["code"], [])
        if (code["end"] >= last or code["end"] < _month_add(last, -(LOOKBACK_MONTHS - 1))
                or len({w["person_id"] for w in team}) < 2):
            continue
        members = sorted({w["person_id"] for w in team})
        start = min(w["_start"] for w in team)
        ability = sum(by_id[m]["_ability"] for m in members) / len(members)
        before = {m: {x["project_code"] for x in history[m] if x["_end"] < start} for m in members}
        pairs = [(a, b) for i, a in enumerate(members) for b in members[i + 1:]]
        chemistry = sum(bool(before[a] & before[b]) for a, b in pairs) / len(pairs)
        industry_exp = sum(any(x["_code"]["domain"] == code["domain"] and x["_end"] < start for x in history[m])
                           for m in members) / len(members)
        churn = sum(_months(w["_start"], w["_end"]) < 4 for w in team) / len(team)
        aff = client_eff.setdefault(code["client"], rng.gauss(0.0, 0.3))
        q = 0.7 * ability + 0.6 * chemistry + 0.5 * industry_exp - 0.6 * churn + aff + rng.gauss(0.0, 0.35)
        raw.append((code, members, team, q))
    if not raw:
        return [], []
    qs = sorted(r[3] for r in raw)
    cut = [qs[int(len(qs) * f)] for f in (0.1, 0.3, 0.7, 0.9)]            # ~10/20/40/20/10 % of scores 1..5
    outcomes, replacements = [], []
    for code, members, team, q in raw:
        score = 1 + sum(q >= c for c in cut)
        outcomes.append({"project_code": code["code"], "client": code["client"], "industry": INDUSTRY[code["domain"]],
                         "closed_month": code["end"].strftime("%Y-%m"), "customer_score": str(score),
                         "schedule": "준수" if rng.random() < _sigmoid(1.2 * q + 0.8) else "지연",
                         "follow_on": "Y" if rng.random() < _sigmoid(1.5 * q - 0.3) else "N"})
        picked = []
        if score <= 2:
            weakest = sorted(members, key=lambda m: by_id[m]["_ability"])[:1 if score == 2 else 2]
            picked += [(m, "고객", rng.choice(CUSTOMER_REASONS)) for m in weakest if rng.random() < 0.7]
        picked += [(m, "내부", rng.choice(INTERNAL_REASONS)) for m in members
                   if rng.random() < 0.03 and m not in {x[0] for x in picked}]
        for m, who, reason in picked:
            row = max((x for x in team if x["person_id"] == m), key=lambda x: x["_start"])
            begin = max(row["_start"], _month_add(last, -(LOOKBACK_MONTHS - 1)))   # inside the exported window
            span = _months(begin, row["_end"])
            if span < 2:
                continue                                      # a one-month stint cannot be cut short
            left = _month_add(begin, rng.randint(0, span - 2))                # last month before the swap
            row["_end"], row["end_date"] = left, _month_end(left).isoformat()
            for k in row["_ws"]:                                # skill months end with the stint
                pid, skill, ss, e = work_skills[k]
                work_skills[k] = (pid, skill, ss, min(e, left)) if ss <= left else None
            replacements.append({"project_code": code["code"], "person_id": m, "requested_by": who,
                                 "reason": reason, "replaced_at": _month_end(left).isoformat()})
    return outcomes, replacements


def _person_skills(work_skills) -> list[dict]:
    acc = defaultdict(lambda: {"months": set(), "count": 0, "last": None})
    for pid, skill, s, e in work_skills:
        a = acc[(pid, skill)]
        k = 0
        while _month_add(s, k) <= e:
            a["months"].add(_month_add(s, k))
            k += 1
        a["count"] += 1
        a["last"] = e if a["last"] is None or e > a["last"] else a["last"]
    return [{"person_id": pid, "skill_name": skill, "skill_category": SKILL_CATEGORY[skill],
             "project_count": str(a["count"]), "experience_months": str(len(a["months"])),
             "last_used_month": a["last"].strftime("%Y-%m")}
            for (pid, skill), a in sorted(acc.items())]


def grade_supply_of(people: list[dict], avail: dict, n_months: int) -> dict[str, list[float]]:
    """등급·달별 가용 인원(가용률 1.0인 사람 수 -- 전일 투입 기준)."""
    out = {g: [0.0] * n_months for g in GRADES}
    for p in people:
        for m, mm in enumerate(avail.get(p["person_id"], [])[:n_months]):
            if mm >= 1.0:
                out[p["career_grade"]][m] += 1.0
    return out


def shrink_to_supply(projects: list[dict], grade_supply: dict[str, list[float]], n_months: int, frozen=None) -> float:
    """모든 달·등급에서 (진행 사업의 그 등급 정원 합) ≤ PEAK_CAP × 그 달 그 등급 가용 인원이 될 때까지, 가장 넘친 칸에서
    그 달 진행 중인 사업 중 그 등급 정원이 가장 큰 사업의 정원을 하나 줄인다(frozen 사업 제외, 사업마다 최소 1석).
    구간 그래프 성질상 같은 등급 사람이 그 기간 내내 있으면 "달마다 수요 ≤ 공급"이 곧 동시 수행 없이 다 채울 수 있다는 뜻이다 --
    휴직자 때문에 여유를 15% 둔다. 돌려주는 값 = 남은 최대 비율(manifest peak_demand_ratio)."""
    frozen = frozen or (lambda p: p["_flagship"] or p.get("_addon", False))     # 추가 과제는 내부 2명 그대로(v4)

    def ratio(m: int, g: str) -> float:
        demand = sum(p["_hc"].get(g, 0) for p in projects if p["_start"] <= m <= p["_end"])
        supply = grade_supply.get(g, [0.0] * n_months)[m]
        return demand / supply if supply > 0 else (math.inf if demand > 0 else 0.0)

    cells = [(m, g) for m in range(n_months) for g in GRADES]
    blocked: set[tuple[int, str]] = set()           # 줄일 사업이 없는 칸(대표 사업만 남음) -- 그 칸만 빼고 다른 칸은 계속(폴백 리뷰 P3)
    while True:
        open_cells = [c for c in cells if c not in blocked]
        if not open_cells:
            break
        m, g = max(open_cells, key=lambda c: ratio(*c))
        if ratio(m, g) <= PEAK_CAP + 1e-12:
            break
        cand = [p for p in projects if not frozen(p) and p["_start"] <= m <= p["_end"] and p["_hc"].get(g, 0) > 0
                and sum(p["_hc"].values()) > 1]
        if not cand:
            blocked.add((m, g))
            continue
        p = max(cand, key=lambda p: (p["_hc"][g], sum(p["_hc"].values()), p["project_id"]))
        p["_hc"][g] -= 1
    finite = [r for r in (ratio(*c) for c in cells) if math.isfinite(r)]
    return max(finite, default=0.0)


def budget_of(rng: random.Random, hc: dict[str, int]) -> str:
    return str(max(1, int(sum(BASE_RATE[g] * k for g, k in hc.items()) * rng.uniform(*BUDGET_RANGE))))


def _projects(rng: random.Random, groups: dict[str, int], horizon: list[dt.date], grade_supply: dict[str, list[float]],
              lrng: random.Random | None = None, size: int = 100):
    projects, grade_reqs, skill_reqs = [], [], []
    # flagship: always in execution for the whole horizon; DP seats always, AI seats only with the AI group
    seats = defaultdict(int)
    reqs = []
    for g in ("DP", "AI"):                      # the flagship is a DP + AI joint programme (AU not involved)
        if groups.get(g):
            for grade, k in FLAGSHIP["seats"][g].items():
                seats[grade] += k
            reqs += FLAGSHIP["skills"][g]
    projects.append({"project_id": FLAGSHIP["project_id"], "project_name": FLAGSHIP["project_name"],
                     "sector": "대외금융", "phase": "실행", "start_month": horizon[0].strftime("%Y-%m"),
                     "end_month": horizon[-1].strftime("%Y-%m"), "_start": 0, "_end": HORIZON_MONTHS - 1,
                     "_hc": dict(seats), "_flagship": True})
    for skill, hc, months in reqs:
        skill_reqs.append({"project_id": FLAGSHIP["project_id"], "skill_name": skill,
                           "min_experience_months": str(months), "headcount": str(hc)})
    j = 1
    used_clients = set()
    for g, n_people in groups.items():
        n_proj = n_people // 5 - (1 if g == "DP" else 0)      # 5 people per project; DP's share includes the flagship
        types = PROJECT_TYPES[g]
        n_prop = round(n_proj * 0.35)
        for k in range(n_proj):
            j += 1
            name, sector, clients, skills, _ = rng.choices(types, weights=[t[4] for t in types])[0]
            client = next((c for c in rng.sample(clients, len(clients)) if (c, name) not in used_clients), clients[0])
            used_clients.add((client, name))
            phase = "제안" if k < n_prop else "실행"
            if phase == "제안":
                start = rng.randint(0, HORIZON_MONTHS - 2)
                end = start + rng.randint(0, 1)
                hc = {"고급": 1, "중급": rng.randint(1, 2)}
            else:
                start = rng.randint(0, 2)
                end = min(HORIZON_MONTHS - 1, start + rng.randint(2, 5))
                hc = {"특급": rng.randint(0, 1), "고급": rng.randint(1, 2), "중급": rng.randint(1, 3), "초급": rng.randint(0, 2)}
            label = name.format(c=client)                    # 단계 말 없음(2026-10-20, LIFE_LABEL 주석)
            pid = f"J{j:03d}"
            texts = (horizon[start].strftime("%Y-%m"), horizon[end].strftime("%Y-%m"))
            life = None
            if phase == "실행" and lrng is not None and lrng.random() < RUNNING_SHARE:
                # v5: 계획 전에 시작해 진행 중인 사업(수명 주기는 운영 중 묶음과 같은 비율). 주 난수열은 위에서 그대로 뽑았다 --
                # 이름·기술 요구는 v4와 같고, 시작 달·(운영이면) 정원·끝 달만 바꾼다
                kind = lrng.choices([k for k, _ in OPERATING_LIFE], weights=[w for _, w in OPERATING_LIFE])[0]
                started = _life_start(lrng, kind, horizon[0])
                life = _life_record(kind, started)
                start = 0
                if kind == "운영":                       # 운영 단계 = 회사 인력 2~3명(사용자 "2-3명이 평균"), 계획 뒤까지
                    hc, end = {"고급": 1, "중급": lrng.randint(1, 2)}, HORIZON_MONTHS - 1
                    texts = (started, _month_add(horizon[0], lrng.randint(12, 24)).strftime("%Y-%m"))
                else:
                    texts = (started, horizon[end].strftime("%Y-%m"))
                label = _labeled(kind, name.format(c=client))
            projects.append({"project_id": pid, "project_name": label, "sector": sector, "phase": phase,
                             "start_month": texts[0], "end_month": texts[1],
                             "_start": start, "_end": end, "_hc": hc, "_flagship": False,
                             "_base": name.format(c=client), "_client": client, "_skills": []})
            if life:
                projects[-1]["_life"] = life
            for skill in rng.sample(skills, rng.randint(max(2, len(skills) - 2), len(skills))):
                cap = 24 if skill in LLM_ERA else 60
                skill_reqs.append({"project_id": pid, "skill_name": skill,
                                   "min_experience_months": str(rng.choice([m for m in (12, 24, 36, 60) if m <= cap])),
                                   "headcount": str(rng.randint(1, 2))})
                projects[-1]["_skills"].append(skill)

    if lrng is not None:                        # v5: 운영 사업에 추가 과제(100명 1 · 200·300명 2)
        parents = [(p, p["_skills"]) for p in projects if (p.get("_life") or {}).get("stage") == "운영"]
        j = _add_addons(lrng, horizon, projects, skill_reqs, parents[:ADDONS.get(size, 1)], j)
    peak = shrink_to_supply(projects, grade_supply, HORIZON_MONTHS)
    for p in projects:
        p["monthly_budget"] = budget_of(rng, p["_hc"])
        grade_reqs += [{"project_id": p["project_id"], "career_grade": g, "headcount": str(k)}
                       for g, k in p["_hc"].items() if k > 0]
    return projects, grade_reqs, skill_reqs, peak


def _skill_rows_for(lrng: random.Random, skills) -> list[dict]:
    out = []
    for skill in lrng.sample(skills, lrng.randint(max(2, len(skills) - 2), len(skills))):
        cap = 24 if skill in LLM_ERA else 60
        out.append({"skill_name": skill, "min_experience_months": str(lrng.choice([m for m in (12, 24, 36, 60) if m <= cap])),
                    "headcount": str(lrng.randint(1, 2))})
    return out


def _addon_skills(lrng: random.Random, parent_skills: list[str]) -> list[dict]:
    """추가 과제 기술: 클라우드 이전 기술 + 상위 사업 기술 1~2개(수정 개발)."""
    # 상위 기술 중 클라우드 이전 기술과 같은 것은 빼고 고른다 -- 겹치면 같은 기술 요구가 두 행이라 로더가 거부했다(Codex 리뷰, 시드 14·1에서 재현)
    rest = [sk for sk in parent_skills if sk not in ADDON_SKILLS]
    picks = list(ADDON_SKILLS) + lrng.sample(rest, min(len(rest), lrng.randint(1, 2)))
    return [{"skill_name": sk, "min_experience_months": str(lrng.choice((12, 24))), "headcount": "1"} for sk in picks]


def _addon_months(horizon: list[dt.date], start: int) -> tuple[str, str, int]:
    """추가 과제 실제 기간(ADDON_MONTHS개월) -- 계획 끝을 넘는 끝 달은 CSV에 그대로 쓰고 모델 기간만 자른다."""
    end_text = _month_add(horizon[0], start + ADDON_MONTHS - 1).strftime("%Y-%m")
    return horizon[start].strftime("%Y-%m"), end_text, min(len(horizon) - 1, start + ADDON_MONTHS - 1)


def _add_addons(lrng: random.Random, horizon: list[dt.date], projects: list[dict], skill_reqs: list[dict],
                parents: list[tuple[dict, list[str]]], j: int) -> int:
    """연초 계획: 운영 사업마다 추가 과제 하나(내부 2명 · 6개월 · 외주 3~6명 참고값) -- 사용자 "6달만 회사 인력은 2명만 추가(외주는 여러명)"."""
    for parent, pskills in parents:
        j += 1
        pid = f"J{j:03d}"
        start = lrng.randint(0, 2)
        s_text, e_text, end = _addon_months(horizon, start)
        base = parent["project_name"].removeprefix("[운영] ")
        projects.append({"project_id": pid, "project_name": f"[추가 과제] {base} 클라우드 마이그레이션·수정 개발",
                         "sector": parent["sector"], "phase": "실행", "start_month": s_text, "end_month": e_text,
                         "_start": start, "_end": end, "_hc": {"고급": 1, "중급": 1}, "_flagship": False, "_addon": True,
                         "_life": {"stage": "추가 과제", "started": s_text, "parent": parent["project_id"],
                                   "external_headcount": lrng.randint(*EXTERNAL_RANGE)}})
        skill_reqs.extend({"project_id": pid, **r} for r in _addon_skills(lrng, pskills))
    return j


BENCH_SHARE = 0.10                 # 운영 중 시나리오: 최근 사업이 끝나 남는 인력 비율(사용자 설명 "100명 중 10명 내외")
PROPOSALS = {100: 2, 200: 3, 300: 3}


def _rate(person: dict) -> float:
    return BASE_RATE[person["career_grade"]] * (CONSULTING_PREMIUM if person["role_type"] == "컨설팅" else 1)


def _operating_scenario(rng: random.Random, people: list[dict], groups: dict[str, int], horizon: list[dt.date],
                        size: int, windows: dict[str, dict] | None = None, lrng: random.Random | None = None):
    """운영 중 시점(2026-10-06 사용자: "이미 배정돼 있는 인력이 대부분 … 신규 제안 2~3개에 투입할 인력은 최근 끝난 10명
    내외"). 진행 중 사업은 계획 기간 내내 이어지고 정원 = 현재 명단(투입률 1.0), 남는 인력 ~10%, 신규 제안 2~3개
    (계획 둘째 달 시작, 필요 인원 ≈ 남는 인력). 현재 명단은 기술이 대체로 맞게 꾸리되 무작위성을 둬 개선 여지가 자연스럽게
    남는다. 신규 제안의 요구 기술은 카탈로그에서 그대로 뽑는다 -- 결과를 맞추려 남는 인력과 어긋나게 만들지 않는다."""
    # v3: 계획 기간에 육아휴직 달이 걸린 사람은 진행 사업·대기 인력에서 뺀다(전일 투입 -- 그 기간 내내 일할 수 없다)
    windows = windows or {}
    on_leave = {pid for pid, w in windows.items()
                if any(available_mm(w, m.strftime("%Y-%m")) < 1.0 for m in horizon)}
    by_group = defaultdict(list)
    for p in people:
        if p["person_id"] not in on_leave:
            by_group[p["_group"]].append(p)
    bench = []
    working = {}
    for g, ps in by_group.items():
        ps = ps[:]
        rng.shuffle(ps)
        k = max(1, round(BENCH_SHARE * len(ps)))
        bench += ps[:k]
        working[g] = ps[k:]
    projects, grade_reqs, skill_reqs, roster = [], [], [], []

    def overlap(person, skills):
        return len(set(person["_pool"]) & set(skills)) + rng.random() * 1.5     # 대체로 맞게, 완벽하지는 않게

    def add_project(pid, name, sector, phase, start, end, members, hc, skills_rows, flagship=False, priced=None,
                    texts=None, life=None):
        s_text, e_text = texts or (horizon[start].strftime("%Y-%m"), horizon[end].strftime("%Y-%m"))
        projects.append({"project_id": pid, "project_name": name, "sector": sector, "phase": phase,
                         "start_month": s_text, "end_month": e_text,
                         "_start": start, "_end": end, "_hc": hc, "_flagship": flagship})
        if life:
            projects[-1]["_life"] = life
        skill_reqs.extend({"project_id": pid, **r} for r in skills_rows)
        priced = members or priced                       # 신규 제안은 그 자리를 채울 수 있는 대기 인력의 실제 단가로(v3)
        cost = sum(_rate(m) for m in priced) if priced else sum(BASE_RATE[g] * k for g, k in hc.items())
        projects[-1]["monthly_budget"] = str(max(1, int(cost * rng.uniform(1.02, 1.12))))
        grade_reqs.extend({"project_id": pid, "career_grade": g, "headcount": str(k)} for g, k in hc.items() if k > 0)
        for m in members:
            roster.append({"person_id": m["person_id"], "project_id": pid, "alloc": "1.0",
                           "locked": "Y" if rng.random() < 0.10 else "N"})

    def skill_rows(skills):
        out = []
        for skill in rng.sample(skills, rng.randint(max(2, len(skills) - 2), len(skills))):
            cap = 24 if skill in LLM_ERA else 60
            out.append({"skill_name": skill, "min_experience_months": str(rng.choice([m for m in (12, 24, 36, 60) if m <= cap])),
                        "headcount": str(rng.randint(1, 2))})
        return out

    # 대표 사업(공동 수행): 그룹별 정해진 내부 정원을 기술이 맞는 사람부터 채운다
    members, rows = [], []
    for g in ("DP", "AI"):
        if not groups.get(g):
            continue
        fskills = [s for s, _, _ in FLAGSHIP["skills"][g]]
        pool = sorted(working[g], key=lambda p: -overlap(p, fskills))
        for grade, k in FLAGSHIP["seats"][g].items():
            take = [p for p in pool if p["career_grade"] == grade and p not in members][:k]
            members += take
        rows += [{"skill_name": s, "min_experience_months": str(m), "headcount": str(hc)} for s, hc, m in FLAGSHIP["skills"][g]]
    for g in working:
        working[g] = [p for p in working[g] if p not in members]
    hc = defaultdict(int)
    for m in members:
        hc[m["career_grade"]] += 1
    add_project(FLAGSHIP["project_id"], FLAGSHIP["project_name"], "대외금융", "실행", 0, HORIZON_MONTHS - 1,
                members, dict(hc), rows, flagship=True)
    j = 1
    used = set()

    picked_client = {}

    def pick_type(g):
        types = PROJECT_TYPES[g]
        name, sector, clients, skills, _ = rng.choices(types, weights=[t[4] for t in types])[0]
        client = next((c for c in rng.sample(clients, len(clients)) if (c, name) not in used), clients[0])
        used.add((client, name))
        picked_client[name.format(c=client)] = client          # v5: 진행 사업 업무 이력의 고객사
        return name.format(c=client), sector, skills

    # v4: 진행 사업마다 수명 주기(별도 난수열) -- 운영(2024 시작, 내부 2~3명, 계획 뒤까지) · 고도화(2025) · 개발(2025 · 2026 상반기).
    # 첫 진행 사업은 운영으로 둬 추가 과제의 상위 사업이 늘 있게 한다. lrng가 없으면 v3 그대로(모두 계획 첫 달 시작).
    life_kinds, life_weights = [k for k, _ in OPERATING_LIFE], [w for _, w in OPERATING_LIFE]
    operating_parents: list[tuple[dict, list[str]]] = []
    for g, remaining in working.items():                 # 진행 중 사업: 남은 인원을 4~9명 팀(운영은 2~3명)으로 모두 배치
        remaining = remaining[:]
        while remaining:
            j += 1
            name, sector, skills = pick_type(g)
            kind = None
            if lrng is not None:
                kind = "운영" if not operating_parents and not any("_life" in p for p in projects) else \
                    lrng.choices(life_kinds, weights=life_weights)[0]
            small = kind == "운영"
            k = rng.randint(4, 9) if not small else lrng.randint(2, 3)
            if len(remaining) - k < (2 if small else 4):
                k = len(remaining)
            team = sorted(remaining, key=lambda p: -overlap(p, skills))[:k]
            remaining = [p for p in remaining if p not in team]
            hc = defaultdict(int)
            for m in team:
                hc[m["career_grade"]] += 1
            rows = skill_rows(skills)
            if kind is None:
                add_project(f"J{j:03d}", name, sector, "실행", 0, HORIZON_MONTHS - 1, team, dict(hc), rows)
                continue
            started = _life_start(lrng, kind, horizon[0])
            end_text = (_month_add(horizon[0], lrng.randint(12, 24)).strftime("%Y-%m") if small
                        else horizon[HORIZON_MONTHS - 1].strftime("%Y-%m"))
            add_project(f"J{j:03d}", _labeled(kind, name), sector, "실행", 0, HORIZON_MONTHS - 1, team, dict(hc), rows,
                        texts=(started, end_text), life=_life_record(kind, started))
            projects[-1].update(_base=name, _client=picked_client[name], _skills=[r["skill_name"] for r in rows])
            if small:
                operating_parents.append((projects[-1], [r["skill_name"] for r in rows]))
    order = [g for g in ("DP", "AI", "AU") if groups.get(g)]
    n_prop = PROPOSALS.get(size, 2)
    # v3(2026-10-19, 전일 투입): 신규 제안 정원 = 대기 인력의 **등급 구성** 그대로(한 명 여유) -- 예전 {고급 1, 중급 n-2, 초급 1}은
    # 등급이 대기 인력과 맞지 않아 아무도 옮기지 않으면(K=0) 피할 수 없는 빈자리가 생겼다(폴백 리뷰 P2: 100명 1·200명 4·300명 12석).
    # 등급 순으로 늘어놓고 한 명씩 돌려 나눠 사업마다 등급이 섞이게 한다. 예산은 그 사람들의 실제 단가 기준(1.02~1.12배, 진행 사업과 같은 규칙).
    pool = sorted(bench, key=lambda p: (GRADES.index(p["career_grade"]), p["person_id"]))
    if len(pool) > n_prop:                                 # 한 명 여유: 가장 많은 등급에서 한 명을 뺀다
        most = max(GRADES, key=lambda g: (sum(1 for p in pool if p["career_grade"] == g), -GRADES.index(g)))
        drop = next(p for p in reversed(pool) if p["career_grade"] == most)
        pool = [p for p in pool if p is not drop]
    addon = []
    if lrng is not None and operating_parents:            # v4: 추가 과제 1개 -- 대기 인력 중 고급·중급 한 명씩(없으면 앞의 두 명)
        for grade in ("고급", "중급"):
            pick = next((p for p in pool if p["career_grade"] == grade and p not in addon), None)
            if pick is not None:
                addon.append(pick)
        addon += [p for p in pool if p not in addon][:max(0, 2 - len(addon))]
        pool = [p for p in pool if p not in addon]
    dealt = [pool[k::n_prop] for k in range(n_prop)]
    proposal_ids = []
    for k in range(n_prop):
        j += 1
        g = order[k % len(order)]
        name, sector, skills = pick_type(g)
        hc = defaultdict(int)
        for p in dealt[k]:
            hc[p["career_grade"]] += 1
        add_project(f"J{j:03d}", name, sector, "제안", 1, HORIZON_MONTHS - 1, [], dict(hc), skill_rows(skills),
                    priced=dealt[k])
        proposal_ids.append(f"J{j:03d}")
    addons = {}
    if addon:
        parent, pskills = lrng.choice(operating_parents)
        j += 1
        pid = f"J{j:03d}"
        s_text, e_text, end = _addon_months(horizon, 1)
        hc = defaultdict(int)
        for p in addon:
            hc[p["career_grade"]] += 1
        base = parent["project_name"].removeprefix("[운영] ")
        external = lrng.randint(*EXTERNAL_RANGE)
        add_project(pid, f"[추가 과제] {base} 클라우드 마이그레이션·수정 개발", parent["sector"], "제안", 1, end, [], dict(hc),
                    _addon_skills(lrng, pskills), priced=addon, texts=(s_text, e_text),
                    life={"stage": "추가 과제", "started": s_text, "parent": parent["project_id"], "external_headcount": external})
        proposal_ids.append(pid)
        # 작업 데이터 화면의 추가 과제 모양(claude-b c531b4b: pipeline[id].parent · external_headcount)
        addons[pid] = {"stage": "제안", "predecessor": None, "parent": parent["project_id"], "external_headcount": external,
                       "registered_at": f"{horizon[0]:%Y-%m}-01"}
    availability, avail = [], defaultdict(list)
    for p in people:
        for m in horizon:
            mm = available_mm(windows.get(p["person_id"]), m.strftime("%Y-%m"))
            avail[p["person_id"]].append(mm)
            availability.append({"person_id": p["person_id"], "month": m.strftime("%Y-%m"), "available_mm": f"{mm:.1f}"})
    demand = sum(sum(p["_hc"].values()) for p in projects) / max(1, len(people))
    return (projects, grade_reqs, skill_reqs, roster, availability, avail, [b["person_id"] for b in bench], proposal_ids, demand,
            addons)


def _current_roster(rng: random.Random, people: list[dict], projects: list[dict], avail: dict) -> list[dict]:
    """Who is already on a running execution project at the start of the plan (continuity input).
    70% of the execution projects running in month 0 (the flagship always) get 50-100% of their seats filled
    with people of the right grade; 10% are locked. v3(2026-10-19, 전일 투입): 투입률 1.0, 그 사업 기간 가용률 1.0인 사람만,
    기간이 겹치는 다른 현재 배치가 없는 사람만(동시 수행 없음), 사업 예산 안에서만(잠긴 배치가 예산을 넘기면 풀 수 없다)."""
    busy = {p["person_id"]: set() for p in people}
    rate = {p["person_id"]: _rate(p) for p in people}
    by_grade = defaultdict(list)
    for p in people:
        by_grade[p["career_grade"]].append(p["person_id"])
    rows = []
    running = [j for j in projects if j["phase"] == "실행" and j["_start"] == 0]
    for j in running:
        # v5(2026-10-19 사용자 "진행 사업은 모두 현재 팀 있음"): 계획 전에 시작한 진행 사업은 빠짐없이 자리의 80~100%가 지금 일하는 사람이다.
        # 계획 첫 달에 새로 시작하는 사업만 예전 규칙(70%의 사업, 자리의 50~100%)
        ongoing = (j.get("_life") or {}).get("stage") not in (None, "추가 과제")
        if not j["_flagship"] and not ongoing and rng.random() > 0.7:
            continue
        months = set(range(j["_start"], j["_end"] + 1))
        left = float(j["monthly_budget"])
        for grade, seats in j["_hc"].items():
            want = round(seats * rng.uniform(0.8 if ongoing else 0.5, 1.0))
            cands = [pid for pid in by_grade[grade]
                     if all(avail[pid][m] >= 1.0 for m in months) and not (busy[pid] & months)]
            for pid in rng.sample(cands, min(want, len(cands))):
                if rate[pid] > left:
                    continue
                busy[pid] |= months
                left -= rate[pid]
                rows.append({"person_id": pid, "project_id": j["project_id"], "alloc": "1.0",
                             "locked": "Y" if rng.random() < 0.1 else "N"})
    return rows


def generate_org_bundle(out_dir: Path, size: int, seed: int, horizon_start: str = "2026-10",
                        scenario: str = "planning", *, blunt_share: float = BLUNT_SHARE,
                        mismatch_share: float = MISMATCH_SHARE) -> Path:
    """scenario: "planning" = 연초 계획(사업을 백지에서 배치), "operating" = 운영 중(대부분이 진행 사업에 있고 신규 제안
    2~3개를 남는 인력으로 짠다, 2026-10-06). 같은 seed면 사람·과거 성과·교체 기록은 두 시나리오가 같다 -- 업무 이력·기술 경력·평가는
    v5부터 진행 사업 현재 팀원의 경력을 이어 시나리오마다 다르다(`_link_ongoing`)."""
    if size not in SIZES:
        raise ValueError(f"size must be one of {sorted(SIZES)}")
    if scenario not in ("planning", "operating"):
        raise ValueError("scenario must be 'planning' or 'operating'")
    from core.ingest.loader import _parse_month
    first = _parse_month(horizon_start)
    groups = {g: n for g, n in SIZES[size].items() if n}
    rng = random.Random(seed)
    last = _month_add(first, -1)
    horizon = [_month_add(first, k) for k in range(HORIZON_MONTHS)]
    people = _people(rng, groups)
    works, work_skills, past_codes = _work_history(rng, people, last)
    _person_traits(random.Random(seed * 104729 + 3), people)
    # outcomes first: replacements cut stints short, and skill months and reviews must see the cut rows
    outcomes, replacements = _past_outcomes(random.Random(seed * 15485863 + 7), past_codes, works, people, last,
                                            work_skills)
    work_skills = [x for x in work_skills if x is not None]
    for w in works:
        del w["_ws"]                              # indices into the pre-filter list; stale from here on
    works, work_skills = _apply_lookback(works, work_skills, last)
    _describe_works(works, last)
    # v3: 휴직·휴가 창(core/ingest/leave) -- 같은 사람·같은 시드면 연초·운영 중·연간 묶음이 같은 창을 쓴다
    windows = leave_windows([p["person_id"] for p in people], horizon_start, leave_seed(seed))
    availability = []
    avail = defaultdict(list)
    for p in people if scenario == "planning" else []:
        for k, m in enumerate(horizon):
            # v2의 무작위 가용률 난수는 그대로 뽑아 버린다 -- 뒤따르는 사업 이름·기간·기술 요구가 v2와 같게 남는다
            rng.choices((1.0, 0.7, 0.5, 0.3, 0.0), weights=(0.6, 0.1, 0.15, 0.05, 0.1))
            mm = available_mm(windows.get(p["person_id"]), m.strftime("%Y-%m"))
            avail[p["person_id"]].append(mm)
            availability.append({"person_id": p["person_id"], "month": m.strftime("%Y-%m"), "available_mm": f"{mm:.1f}"})
    extra = {}
    if scenario == "planning":
        projects, grade_reqs, skill_reqs, peak = _projects(rng, groups, horizon,
                                                           grade_supply_of(people, avail, HORIZON_MONTHS),
                                                           random.Random(lifecycle_seed(seed)), size)
    else:
        (projects, grade_reqs, skill_reqs, roster, availability, avail, bench, proposals,
         peak, addons) = _operating_scenario(random.Random(seed * 31337 + 11), people, groups, horizon, size, windows,
                                             random.Random(lifecycle_seed(seed)))
        extra = {"scenario": "operating", "bench": bench, "proposals": proposals, **({"pipeline": addons} if addons else {})}
    for name, share in (("blunt_share", blunt_share), ("mismatch_share", mismatch_share)):
        if not 0 <= share <= 1:
            raise ValueError(f"{name}는 0~1이어야 한다: {share}")
    reviews, review_items, review_style = _org_reviews(random.Random(seed * 1299709 + 5), works, people, _review_rounds(first),
                                                       seed=seed, blunt_share=blunt_share, mismatch_share=mismatch_share)
    # separate RNG stream so adding the roster does not change any other table of an existing seed
    if scenario == "planning":
        roster = _current_roster(random.Random(seed * 7919 + 1), people, projects, avail)
    # v5: 진행 사업 경력을 업무 이력·동료 평가에 잇는다(현재 배치가 정해진 뒤, 원래 평가를 만든 뒤)
    works, work_skills, reviews, review_items, review_style, ongoing = _link_ongoing(
        random.Random(ongoing_seed(seed)), people, works, work_skills, projects, roster, last, first, reviews, review_items,
        review_style, seed=seed, blunt_share=blunt_share, mismatch_share=mismatch_share, replacements=replacements)
    rate_card = [{"career_grade": g, "role_type": r,
                  "monthly_rate": str(round(BASE_RATE[g] * (CONSULTING_PREMIUM if r == "컨설팅" else 1)))}
                 for g in GRADES for r in ("개발", "컨설팅")]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    llm_origin = _month_add(last, -(LLM_ERA_MONTHS - 1))
    # 업무별 사용 기술(2026-10-19 -- 진행 중 업무의 기술을 이 서비스가 늘린다, core/ingest/aging). LLM 시대 전에 끝난 업무에는 LLM 기술을 적지 않는다
    work_skill_rows = [{"work_id": w["work_id"], "skill_name": sk} for w in works for sk in w.get("_all", [])
                       if sk not in LLM_ERA or w["_end"] >= llm_origin]
    tables = {"people.csv": people, "rate_card.csv": rate_card, "person_skills.csv": _person_skills(work_skills),
              "work_history.csv": works, "work_skills.csv": work_skill_rows, "availability.csv": availability,
              "projects.csv": projects,
              "project_grade_requirements.csv": grade_reqs, "project_skill_requirements.csv": skill_reqs,
              "reviews.csv": reviews, "review_items.csv": review_items,
              "current_assignments.csv": roster,
              "project_outcomes.csv": outcomes, "replacements.csv": replacements}
    hashes = {name: _write_csv(out_dir / name, name, rows) for name, rows in tables.items()}
    manifest = {"dataset_id": f"org-n{size}-s{seed}" + ("-operating" if scenario == "operating" else ""),
                "schema_version": SCHEMA_VERSION,
                "horizon_start": horizon_start, "horizon_months": HORIZON_MONTHS, "cost_unit": "가상비용점(월)",
                # 데이터 기준월 = 계획 직전 달(이력·기술 경력이 거기까지) -- 진행 중 사업 기술은 늘어날 달이 없다(시연 숫자 그대로)
                "as_of": last.strftime("%Y-%m"), "skill_months_owner": "service",
                "synthetic": True, "seed": seed, "generator": GENERATOR, "groups": groups,
                "flagship_project": FLAGSHIP["project_id"],
                "note": "조직 구성은 사용자 설명(2026-10-05)을 따른 가상 데이터. 최대 사업은 내부 정원만 표현(외주 제외). 고객명 익명.",
                "peak_demand_ratio": round(peak, 4), **extra, "leave": manifest_section(windows),
                "lifecycle": {p["project_id"]: p["_life"] for p in projects if "_life" in p},
                "ongoing_history": ongoing,
                "review_style": review_style, "files": hashes}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out_dir
