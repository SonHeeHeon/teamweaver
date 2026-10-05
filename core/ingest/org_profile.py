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
from core.ingest.review_text import ORG_REVIEW_ITEMS, negative_text, positive_text
from core.ingest.synthetic import (BASE_RATE, CONSULTING_PREMIUM, GRADE_WEIGHTS, _month_add, _month_end, _months,
                                   _review_rounds, _write_csv)

GENERATOR = "core.ingest.org_profile v1"
SIZES = {100: {"DP": 100}, 200: {"DP": 100, "AI": 100}, 300: {"DP": 100, "AI": 100, "AU": 100}}
HISTORY_MONTHS = 120       # last 10 years of work history (user decision 2026-10-05)
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
            out.append({"person_id": f"{g}{i + 1:04d}", "display_name": f"가상{g}{i + 1:04d}",
                        "career_grade": rng.choices(GRADES, weights=GRADE_WEIGHTS)[0],
                        "role_type": "컨설팅" if fam[2] else "개발", "job_family": f"{g}·{fam[0]}",
                        "_group": g, "_pool": fam[3]})
    return out


def _work_history(rng: random.Random, people: list[dict], last: dt.date):
    """Past project codes per group; AI codes and LLM-era skills live only in the last LLM_ERA_MONTHS."""
    origin = _month_add(last, -HISTORY_MONTHS)
    llm_origin = _month_add(last, -(LLM_ERA_MONTHS - 1))      # inclusive window: exactly LLM_ERA_MONTHS months
    codes = defaultdict(list)
    by_group = defaultdict(int)
    for p in people:
        by_group[p["_group"]] += 1
    k = 0
    for g, n in by_group.items():
        span_origin, span = (llm_origin, LLM_ERA_MONTHS) if g == "AI" else (origin, HISTORY_MONTHS)
        for _ in range(max(8, n)):
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
    # career length by grade (months before the horizon); juniors joined recently
    tenure = {"초급": (12, 40), "중급": (36, 84), "고급": (72, 120), "특급": (96, 120)}
    for p in people:
        lo_t, hi_t = tenure[p["career_grade"]]
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
                   "_start": s, "_end": e, "_code": code, "_used": list(used), "_ws": []}
            works.append(row)
            for skill in dict.fromkeys(used + extra + [code["domain"]]):
                ss = max(s, llm_origin) if skill in LLM_ERA else s
                if ss <= e:
                    row["_ws"].append(len(work_skills))
                    work_skills.append((p["person_id"], skill, ss, e))
            t = _month_add(e, 1 + rng.choice((0, 0, 0, 1, 2)))
    return works, work_skills, [c for cs in codes.values() for c in cs]


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


def _org_reviews(rng: random.Random, works: list[dict], people: list[dict], rounds):
    """Half-yearly peer reviews among people active on the same past project (~5 per person per round, i.e.
    ~10 a year as in the schema answer). Strength count leans on the hidden ability; prose from review_text."""
    by_id = {p["person_id"]: p for p in people}
    by_code = defaultdict(list)
    for w in works:
        by_code[w["project_code"]].append(w)
    reviews, review_items = [], []
    n = 0
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
                reviews.append({
                    "review_id": rid, "review_round": round_id, "project_code": w["project_code"],
                    "reviewer_id": reviewer, "reviewee_id": reviewee, "reviewed_at": reviewed_at.isoformat(),
                    "positive_text": positive_text(rng, pos, work_name=w["work_name"], domain=w["industry"],
                                                   skill=skill, area=w["_code"]["area"], grade=p["career_grade"]),
                    "negative_text": negative_text(rng, neg, skill=skill)})
                review_items += [{"review_id": rid, "polarity": "positive", "item": x} for x in pos]
                review_items += [{"review_id": rid, "polarity": "negative", "item": x} for x in neg]
    return reviews, review_items


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
        if code["end"] >= last or len({w["person_id"] for w in team}) < 2:
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
            span = _months(row["_start"], row["_end"])
            if span < 2:
                continue                                      # a one-month stint cannot be cut short
            left = _month_add(row["_start"], rng.randint(0, span - 2))      # last month before the swap
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


def _projects(rng: random.Random, groups: dict[str, int], horizon: list[dt.date], supply: list[float]):
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
            label = f"[{'제안' if phase == '제안' else '실행'}] " + name.format(c=client)
            pid = f"J{j:03d}"
            projects.append({"project_id": pid, "project_name": label, "sector": sector, "phase": phase,
                             "start_month": horizon[start].strftime("%Y-%m"), "end_month": horizon[end].strftime("%Y-%m"),
                             "_start": start, "_end": end, "_hc": hc, "_flagship": False})
            for skill in rng.sample(skills, rng.randint(max(2, len(skills) - 2), len(skills))):
                cap = 24 if skill in LLM_ERA else 60
                skill_reqs.append({"project_id": pid, "skill_name": skill,
                                   "min_experience_months": str(rng.choice([m for m in (12, 24, 36, 60) if m <= cap])),
                                   "headcount": str(rng.randint(1, 2))})

    def peak_ratio():
        return max((sum(sum(p["_hc"].values()) for p in projects if p["_start"] <= m <= p["_end"]) / supply[m]
                    for m in range(HORIZON_MONTHS) if supply[m] > 0), default=0.0)
    def peak_month():
        return max((m for m in range(HORIZON_MONTHS) if supply[m] > 0),
                   key=lambda m: sum(sum(p["_hc"].values()) for p in projects if p["_start"] <= m <= p["_end"]) / supply[m])
    while peak_ratio() > 0.85:
        # shrink only projects running in the peak month (others do not lower the peak), never the flagship
        m = peak_month()
        p = max((p for p in projects if not p["_flagship"] and p["_start"] <= m <= p["_end"]
                 and sum(p["_hc"].values()) > 1), key=lambda p: sum(p["_hc"].values()), default=None)
        if p is None:
            break
        g = max(p["_hc"], key=p["_hc"].get)
        p["_hc"][g] -= 1
    peak = peak_ratio()
    for p in projects:
        budget = sum(BASE_RATE[g] * k for g, k in p["_hc"].items())
        p["monthly_budget"] = str(max(1, int(budget * rng.uniform(0.9, 1.15))))
        grade_reqs += [{"project_id": p["project_id"], "career_grade": g, "headcount": str(k)}
                       for g, k in p["_hc"].items() if k > 0]
    return projects, grade_reqs, skill_reqs, peak


def _current_roster(rng: random.Random, people: list[dict], projects: list[dict], avail: dict) -> list[dict]:
    """Who is already on a running execution project at the start of the plan (continuity input).
    70% of the execution projects running in month 0 (the flagship always) get 50-100% of their seats filled
    with people of the right grade who still have capacity in every month of the project; 10% are locked."""
    remaining = {p["person_id"]: list(avail[p["person_id"]]) for p in people}
    by_grade = defaultdict(list)
    for p in people:
        by_grade[p["career_grade"]].append(p["person_id"])
    rows = []
    running = [j for j in projects if j["phase"] == "실행" and j["_start"] == 0]
    for j in running:
        if not j["_flagship"] and rng.random() > 0.7:
            continue
        months = range(j["_start"], j["_end"] + 1)
        for grade, seats in j["_hc"].items():
            want = round(seats * rng.uniform(0.5, 1.0))
            cands = [pid for pid in by_grade[grade] if min(remaining[pid][m] for m in months) >= 0.3]
            for pid in rng.sample(cands, min(want, len(cands))):
                cap = min(remaining[pid][m] for m in months)
                alloc = round(min(cap, rng.choice((0.3, 0.5, 0.5, 0.7, 1.0, 1.0))), 1)
                if alloc < 0.3:
                    continue
                for m in months:
                    remaining[pid][m] -= alloc
                rows.append({"person_id": pid, "project_id": j["project_id"], "alloc": f"{alloc:.1f}",
                             "locked": "Y" if rng.random() < 0.1 else "N"})
    return rows


def generate_org_bundle(out_dir: Path, size: int, seed: int, horizon_start: str = "2026-10") -> Path:
    if size not in SIZES:
        raise ValueError(f"size must be one of {sorted(SIZES)}")
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
    _describe_works(works, last)
    availability, supply = [], [0.0] * HORIZON_MONTHS
    avail = defaultdict(list)
    for p in people:
        for k, m in enumerate(horizon):
            mm = rng.choices((1.0, 0.7, 0.5, 0.3, 0.0), weights=(0.6, 0.1, 0.15, 0.05, 0.1))[0]
            avail[p["person_id"]].append(mm)
            supply[k] += mm
            availability.append({"person_id": p["person_id"], "month": m.strftime("%Y-%m"), "available_mm": f"{mm:.1f}"})
    projects, grade_reqs, skill_reqs, peak = _projects(rng, groups, horizon, supply)
    reviews, review_items = _org_reviews(random.Random(seed * 1299709 + 5), works, people, _review_rounds(first))
    # separate RNG stream so adding the roster does not change any other table of an existing seed
    roster = _current_roster(random.Random(seed * 7919 + 1), people, projects, avail)
    rate_card = [{"career_grade": g, "role_type": r,
                  "monthly_rate": str(round(BASE_RATE[g] * (CONSULTING_PREMIUM if r == "컨설팅" else 1)))}
                 for g in GRADES for r in ("개발", "컨설팅")]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {"people.csv": people, "rate_card.csv": rate_card, "person_skills.csv": _person_skills(work_skills),
              "work_history.csv": works, "availability.csv": availability, "projects.csv": projects,
              "project_grade_requirements.csv": grade_reqs, "project_skill_requirements.csv": skill_reqs,
              "reviews.csv": reviews, "review_items.csv": review_items,
              "current_assignments.csv": roster,
              "project_outcomes.csv": outcomes, "replacements.csv": replacements}
    hashes = {name: _write_csv(out_dir / name, name, rows) for name, rows in tables.items()}
    manifest = {"dataset_id": f"org-n{size}-s{seed}", "schema_version": SCHEMA_VERSION,
                "horizon_start": horizon_start, "horizon_months": HORIZON_MONTHS, "cost_unit": "가상비용점(월)",
                "synthetic": True, "seed": seed, "generator": GENERATOR, "groups": groups,
                "flagship_project": FLAGSHIP["project_id"],
                "note": "조직 구성은 사용자 설명(2026-10-05)을 따른 가상 데이터. 최대 사업은 내부 정원만 표현(외주 제외). 고객명 익명.",
                "peak_demand_ratio": round(peak, 4), "files": hashes}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out_dir
