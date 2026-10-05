"""Organisation-shaped synthetic bundles for the scale rehearsal (100 / 200 / 300 people).

The plain generator (core/ingest/synthetic.py) draws generic SI staff and projects. This one follows the
user's description of the real organisation (2026-10-05) so the rehearsal looks like the real workload:

- 100 people = data platform delivery group (DP) only; 200 / 300 = DP 100 + AI delivery group 100 / 200
  (the AI head count is claude-a's assumption -- the user gave only the totals).
- DP work: unstructured-to-metadata conversion and unstructured data marts for financial clients, plus
  internal (group affiliate) data platform work. AI work: AI agent development for financial clients.
- The largest engagement is a joint AI agent programme for a large commercial bank: 8 internal people from
  DP and 20 from AI (outsourced staff are many more but are outside the model -- only internal seats count).

Every value is invented and client names are anonymised ("대형 시중은행 A", "계열사 B"): real company or
client names are never written into the repository. Work history comes first and skills, coworking and
reviews are derived from it, exactly as in synthetic.py whose helpers are reused.
"""
import datetime as dt
import json
import random
from collections import defaultdict
from pathlib import Path

from core.config import load_review_items
from core.ingest.contract import GRADES, HORIZON_MONTHS, SCHEMA_VERSION
from core.ingest.synthetic import (BASE_RATE, CONSULTING_PREMIUM, GRADE_WEIGHTS, _month_add, _month_end, _months,
                                   _review_rounds, _reviews, _write_csv)

GENERATOR = "core.ingest.org_profile v1"
SIZES = {100: {"DP": 100, "AI": 0}, 200: {"DP": 100, "AI": 100}, 300: {"DP": 100, "AI": 200}}
HISTORY_MONTHS = 100
LLM_ERA_MONTHS = 36           # LLM-era skills only exist in the last three years of history

CATALOG = {
    "Programming Language": ["Python", "Java", "SQL", "Scala"],
    "Data": ["Spark", "Kafka", "Airflow", "ETL", "Hadoop", "Data Lake & Data Catalog", "메타데이터 관리",
             "데이터 모델링", "비정형 문서 처리(OCR)", "검색엔진", "BI Reporting"],
    "AI": ["LLM Application", "RAG·벡터DB", "AI 에이전트 프레임워크", "프롬프트 엔지니어링", "LLM 파인튜닝",
           "ML Ops", "Text Analysis"],
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
}
# past-work domain mix per group
DOMAINS = {"DP": (("금융 업무", 0.5), ("그룹사 업무", 0.4), ("공공 업무", 0.1)),
           "AI": (("금융 업무", 0.6), ("그룹사 업무", 0.4))}

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
    llm_origin = _month_add(last, -LLM_ERA_MONTHS)
    codes = defaultdict(list)
    by_group = defaultdict(int)
    for p in people:
        by_group[p["_group"]] += 1
    k = 0
    for g, n in by_group.items():
        span_origin, span = (llm_origin, LLM_ERA_MONTHS) if g == "AI" else (origin, HISTORY_MONTHS)
        for _ in range(max(4, n // 2)):
            k += 1
            start = _month_add(span_origin, rng.randrange(0, span - 4))
            end = min(_month_add(start, rng.randint(4, 18) - 1), last)
            dom = rng.choices([d for d, _ in DOMAINS[g]], weights=[w for _, w in DOMAINS[g]])[0]
            codes[g].append({"code": f"X-{k:04d}", "start": start, "end": end, "domain": dom})
    works, work_skills = [], []
    n = 0
    tech = [s for s in SKILL_CATEGORY if SKILL_CATEGORY[s] != "Domain"]
    for p in people:
        own = codes[p["_group"]]
        other = codes["AI" if p["_group"] == "DP" else "DP"]
        picks = rng.sample(own, min(len(own), rng.randint(3, 9)))
        if other and rng.random() < 0.15:          # a few people crossed over to the other group's work
            picks.append(rng.choice(other))
        for code in picks:
            span = _months(code["start"], code["end"])
            s = _month_add(code["start"], rng.randrange(0, span))
            e = min(_month_add(s, rng.randint(1, 12) - 1), code["end"])
            n += 1
            status = "진행중" if e >= last else rng.choices(("확정완료", "미등록"), weights=(0.85, 0.15))[0]
            works.append({"person_id": p["person_id"], "work_id": f"W{n:06d}", "project_code": code["code"],
                          "start_date": s.isoformat(), "end_date": _month_end(e).isoformat(), "status": status,
                          "_start": s, "_end": e})
            used = rng.sample(p["_pool"], min(len(p["_pool"]), rng.randint(3, 6)))
            used += rng.sample([x for x in tech if x not in p["_pool"]], rng.randint(1, 4))   # ~20 skills per person (schema answer)
            used.append(code["domain"])
            for skill in dict.fromkeys(used):
                ss = max(s, llm_origin) if skill in LLM_ERA else s
                if ss <= e:
                    work_skills.append((p["person_id"], skill, ss, e))
    return works, work_skills


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
    for g in ("DP", "AI"):
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
    works, work_skills = _work_history(rng, people, last)
    availability, supply = [], [0.0] * HORIZON_MONTHS
    for p in people:
        for k, m in enumerate(horizon):
            mm = rng.choices((1.0, 0.7, 0.5, 0.3, 0.0), weights=(0.6, 0.1, 0.15, 0.05, 0.1))[0]
            supply[k] += mm
            availability.append({"person_id": p["person_id"], "month": m.strftime("%Y-%m"), "available_mm": f"{mm:.1f}"})
    projects, grade_reqs, skill_reqs, peak = _projects(rng, groups, horizon, supply)
    reviews, review_items = _reviews(rng, works, load_review_items(), _review_rounds(first))
    rate_card = [{"career_grade": g, "role_type": r,
                  "monthly_rate": str(round(BASE_RATE[g] * (CONSULTING_PREMIUM if r == "컨설팅" else 1)))}
                 for g in GRADES for r in ("개발", "컨설팅")]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {"people.csv": people, "rate_card.csv": rate_card, "person_skills.csv": _person_skills(work_skills),
              "work_history.csv": works, "availability.csv": availability, "projects.csv": projects,
              "project_grade_requirements.csv": grade_reqs, "project_skill_requirements.csv": skill_reqs,
              "reviews.csv": reviews, "review_items.csv": review_items}
    hashes = {name: _write_csv(out_dir / name, name, rows) for name, rows in tables.items()}
    manifest = {"dataset_id": f"org-n{size}-s{seed}", "schema_version": SCHEMA_VERSION,
                "horizon_start": horizon_start, "horizon_months": HORIZON_MONTHS, "cost_unit": "가상비용점(월)",
                "synthetic": True, "seed": seed, "generator": GENERATOR, "groups": groups,
                "flagship_project": FLAGSHIP["project_id"],
                "note": "조직 구성은 사용자 설명(2026-10-05)을 따른 가상 데이터. 최대 사업은 내부 정원만 표현(외주 제외). 고객명 익명.",
                "peak_demand_ratio": round(peak, 4), "files": hashes}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out_dir
