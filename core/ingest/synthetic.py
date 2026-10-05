"""Generate a synthetic CSV bundle in the contract format (core/ingest/contract.py).

Work history is generated first; skill experience, coworking and peer reviews are all derived from
it, so the files never contradict each other. Every value is invented (manifest: synthetic=true).
Distributions follow the user's schema answers: about 20 skills per person, peer reviews every half
year between people who worked on the same project, 1-5 positive and 1-5 negative items per review.
"""
import csv
import datetime as dt
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path

from core.config import load_review_items
from core.ingest.contract import FILES, GRADES, HORIZON_MONTHS, SCHEMA_VERSION

GENERATOR = "core.ingest.synthetic v1"
GRADE_WEIGHTS = (0.10, 0.30, 0.40, 0.20)
BASE_RATE = {"특급": 1600, "고급": 1300, "중급": 1000, "초급": 750}
CONSULTING_PREMIUM = 1.1
CATALOG = {
    "Programming Language": ["Java", "Python", "JavaScript", "TypeScript", "Go", "Kotlin", "C#", "SQL"],
    "DBMS": ["Oracle Database", "PostgreSQL", "MySQL", "MongoDB", "Redis"],
    "Framework": ["Spring", "React", "Vue", "Django", "FastAPI", "Node.js"],
    "DevOps Tools": ["Git", "Jenkins", "Docker", "Kubernetes", "Terraform"],
    "Cloud": ["AWS", "Azure", "Private Cloud"],
    "AI": ["Text Analysis", "LLM Application", "Computer Vision", "ML Ops", "Recommendation"],
    "Data": ["Spark", "Data Lake & Data Catalog", "ETL", "BI Reporting"],
    "Domain": ["금융 업무", "공공 업무", "제조 업무", "그룹사 업무"],
}
SKILL_CATEGORY = {s: c for c, skills in CATALOG.items() for s in skills}
SPECIALTIES = {
    "백엔드": ["Java", "SQL", "Spring", "Oracle Database", "PostgreSQL", "Git", "Jenkins", "Docker", "Redis", "Kotlin"],
    "프론트엔드": ["JavaScript", "TypeScript", "React", "Vue", "Node.js", "Git"],
    "데이터": ["Python", "SQL", "Spark", "ETL", "Data Lake & Data Catalog", "BI Reporting", "PostgreSQL", "AWS"],
    "AI": ["Python", "Text Analysis", "LLM Application", "Computer Vision", "ML Ops", "Recommendation", "Docker"],
    "클라우드": ["AWS", "Azure", "Private Cloud", "Kubernetes", "Terraform", "Docker", "Go", "Git"],
}
SECTORS = (("대외금융", 0.5, "금융 업무"), ("대외공공", 0.25, "공공 업무"), ("대내", 0.25, "그룹사 업무"))
HISTORY_MONTHS = 100          # test-only generic generator; demo and rehearsal data (org_profile) use 10 years


def _review_rounds(first: dt.date) -> list[tuple[str, dt.date]]:
    """The three most recent half-year review dates (Jun 20 / Dec 15) strictly before the horizon."""
    out, year = [], first.year
    while len(out) < 3:
        for label, d in ((f"{year}H2", dt.date(year, 12, 15)), (f"{year}H1", dt.date(year, 6, 20))):
            if d < first and len(out) < 3:
                out.append((label, d))
        year -= 1
    return sorted(out, key=lambda r: r[1])


def _month_add(d: dt.date, k: int) -> dt.date:
    y, m = divmod(d.month - 1 + k, 12)
    return dt.date(d.year + y, m + 1, 1)


def _month_end(d: dt.date) -> dt.date:
    return _month_add(d, 1) - dt.timedelta(days=1)


def _months(start: dt.date, end: dt.date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _people(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"person_id": f"P{i + 1:04d}", "display_name": f"가상인력{i + 1:04d}",
                    "career_grade": rng.choices(GRADES, weights=GRADE_WEIGHTS)[0],
                    "role_type": "컨설팅" if rng.random() < 0.3 else "개발",
                    "job_family": rng.choice(list(SPECIALTIES))})
    return out


def _work_history(rng: random.Random, people: list[dict], last: dt.date) -> tuple[list[dict], list[dict]]:
    """Past project codes with a time window ending by `last` (the month before the horizon); people join
    codes inside the window, so members overlap. Nothing is dated after `last`."""
    codes = []
    origin = _month_add(last, -HISTORY_MONTHS)
    for k in range(max(4, len(people) // 2)):
        start = _month_add(origin, rng.randrange(0, HISTORY_MONTHS - 4))
        length = rng.randint(4, 24)
        end = min(_month_add(start, length - 1), last)
        domain = rng.choices([s[2] for s in SECTORS], weights=[s[1] for s in SECTORS])[0]
        codes.append({"code": f"X-{k + 1:04d}", "start": start, "end": end, "domain": domain})
    works, work_skills = [], []
    n = 0
    for p in people:
        pool = SPECIALTIES[p["job_family"]]
        for code in rng.sample(codes, min(len(codes), rng.randint(3, 10))):
            span = _months(code["start"], code["end"])
            s = _month_add(code["start"], rng.randrange(0, span))
            e = min(_month_add(s, rng.randint(1, 12) - 1), code["end"])
            n += 1
            work_id = f"W{n:06d}"
            status = "진행중" if e >= last else rng.choices(("확정완료", "미등록"), weights=(0.85, 0.15))[0]
            works.append({"person_id": p["person_id"], "work_id": work_id, "project_code": code["code"],
                          "start_date": s.isoformat(), "end_date": _month_end(e).isoformat(), "status": status,
                          "_start": s, "_end": e})
            used = rng.sample(pool, min(len(pool), rng.randint(3, 6)))
            others = [x for x in SKILL_CATEGORY if x not in pool and SKILL_CATEGORY[x] != "Domain"]
            used += rng.sample(others, rng.randint(0, 3))       # people also pick up skills outside their track
            used.append(code["domain"])
            work_skills.extend((p["person_id"], skill, s, e) for skill in dict.fromkeys(used))
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


def _projects(rng: random.Random, n: int, horizon: list[dt.date], supply: list[float]):
    projects, grade_reqs, skill_reqs = [], [], []
    tech = [s for s in SKILL_CATEGORY if SKILL_CATEGORY[s] != "Domain"]
    n_prop = round(n * 0.35)
    for j in range(n):
        pid = f"J{j + 1:03d}"
        phase = "제안" if j < n_prop else "실행"
        sector, _, domain = rng.choices(SECTORS, weights=[s[1] for s in SECTORS])[0]
        if phase == "제안":
            start = rng.randint(0, HORIZON_MONTHS - 2)
            end = start + rng.randint(0, 1)
            hc = {"특급": rng.randint(0, 1), "고급": 1, "중급": rng.randint(1, 2)}
        else:
            start = rng.randint(0, 1)
            end = min(HORIZON_MONTHS - 1, start + rng.randint(3, 5))
            hc = {"특급": rng.randint(0, 1), "고급": rng.randint(1, 2), "중급": rng.randint(2, 3), "초급": rng.randint(1, 2)}
        projects.append({"project_id": pid, "project_name": f"가상 {sector} 사업 {j + 1:03d}", "sector": sector,
                         "phase": phase, "start_month": horizon[start].strftime("%Y-%m"),
                         "end_month": horizon[end].strftime("%Y-%m"), "_start": start, "_end": end, "_hc": hc})
        for skill in rng.sample(tech, rng.randint(2, 4)) + ([domain] if sector != "대내" else []):
            skill_reqs.append({"project_id": pid, "skill_name": skill,
                               "min_experience_months": str(rng.choice((12, 24, 36, 60))),
                               "headcount": str(rng.randint(1, 2))})
    # Keep peak monthly demand at or below 85% of supply (headcount vs available man-months), as in core/datagen.
    def peak_ratio():
        return max((sum(sum(p["_hc"].values()) for p in projects if p["_start"] <= m <= p["_end"]) / supply[m]
                    for m in range(HORIZON_MONTHS) if supply[m] > 0), default=0.0)
    while peak_ratio() > 0.85:
        p = max((p for p in projects if sum(p["_hc"].values()) > 1), key=lambda p: sum(p["_hc"].values()), default=None)
        if p is None:
            break
        g = max(p["_hc"], key=p["_hc"].get)
        p["_hc"][g] -= 1
    peak = peak_ratio()        # may stay above 0.85 when every project is already down to one person
    for p in projects:
        budget = sum(BASE_RATE[g] * k for g, k in p["_hc"].items())   # base (개발) rates: 컨설팅 staff cost 10% more
        p["monthly_budget"] = str(max(1, int(budget * rng.uniform(0.85, 1.15))))
        grade_reqs += [{"project_id": p["project_id"], "career_grade": g, "headcount": str(k)}
                       for g, k in p["_hc"].items() if k > 0]
    return projects, grade_reqs, skill_reqs, peak


def _reviews(rng: random.Random, works: list[dict], items: list[str], rounds: list[tuple[str, dt.date]]):
    """Each half-year round, people review some of the colleagues they worked with on a shared project."""
    by_code = defaultdict(list)
    for w in works:
        by_code[w["project_code"]].append(w)
    reviews, review_items = [], []
    n = 0
    for round_id, reviewed_at in rounds:
        lo = _month_add(dt.date(reviewed_at.year, reviewed_at.month, 1), -5)
        hi = dt.date(reviewed_at.year, reviewed_at.month, 1)
        pairs = {}
        for code, ws in by_code.items():
            active = [w for w in ws if w["_start"] <= hi and w["_end"] >= lo]
            for a in active:
                for b in active:
                    if a["person_id"] != b["person_id"] and a["_start"] <= b["_end"] and b["_start"] <= a["_end"]:
                        pairs.setdefault((a["person_id"], b["person_id"]), code)
        for (reviewer, reviewee), code in sorted(pairs.items()):
            if rng.random() > 0.9:
                continue
            n += 1
            rid = f"R{n:06d}"
            pos = rng.sample(items, rng.randint(1, 5))
            neg = rng.sample([x for x in items if x not in pos], rng.randint(1, 5))
            reviews.append({"review_id": rid, "review_round": round_id, "project_code": code,
                            "reviewer_id": reviewer, "reviewee_id": reviewee, "reviewed_at": reviewed_at.isoformat(),
                            "positive_text": f"{', '.join(pos)} 측면에서 프로젝트에 기여했다.",
                            "negative_text": f"{', '.join(neg)} 측면은 보완이 필요하다."})
            review_items += [{"review_id": rid, "polarity": "positive", "item": x} for x in pos]
            review_items += [{"review_id": rid, "polarity": "negative", "item": x} for x in neg]
    return reviews, review_items


def _write_csv(path: Path, name: str, rows: list[dict]) -> str:
    spec = next(f for f in FILES if f.name == name)
    headers = [c.name for c in spec.columns]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(headers)
        for r in rows:
            w.writerow([r.get(h, "") for h in headers])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_bundle(out_dir: Path, n_people: int, n_projects: int, seed: int, horizon_start: str = "2026-10") -> Path:
    if n_people < 3 or n_projects < 1:
        raise ValueError("need at least 3 people and 1 project")
    from core.ingest.loader import _parse_month
    first = _parse_month(horizon_start)                    # raises ValueError for anything but YYYY-MM
    rng = random.Random(seed)
    last = _month_add(first, -1)
    horizon = [_month_add(first, k) for k in range(HORIZON_MONTHS)]
    people = _people(rng, n_people)
    works, work_skills = _work_history(rng, people, last)
    availability = []
    supply = [0.0] * HORIZON_MONTHS
    for p in people:
        for k, m in enumerate(horizon):
            mm = rng.choices((1.0, 0.7, 0.5, 0.3, 0.0), weights=(0.6, 0.1, 0.15, 0.05, 0.1))[0]
            supply[k] += mm
            availability.append({"person_id": p["person_id"], "month": m.strftime("%Y-%m"), "available_mm": f"{mm:.1f}"})
    projects, grade_reqs, skill_reqs, peak = _projects(rng, n_projects, horizon, supply)
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
    manifest = {"dataset_id": f"synthetic-n{n_people}-p{n_projects}-s{seed}", "schema_version": SCHEMA_VERSION,
                "horizon_start": horizon_start, "horizon_months": HORIZON_MONTHS, "cost_unit": "가상비용점(월)",
                "synthetic": True, "seed": seed, "generator": GENERATOR,
                "peak_demand_ratio": round(peak, 4), "files": hashes}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out_dir
