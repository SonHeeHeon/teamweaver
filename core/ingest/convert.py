"""Convert a validated CSV bundle into the current pydantic Dataset.

The current model needs 1-5 skill levels, one review per reviewer→reviewee and cowork months per
pair; the real data has experience months, repeated review rounds and work history. The bridges
used here are stated in IngestReport.notes so nobody mistakes them for source facts.
(One review per reviewer→reviewee was the old model limit; all rounds are now passed on.)
"""
import datetime as dt
from collections import defaultdict
from itertools import combinations

from core.datagen.parse_reviews import parse_reviews_rule_based
from core.domain.models import (CoworkRecord, CurrentAssignment, Dataset, Grade, ParsedReview, PeerReview, Person,
                                Project, ProjectPhase, ReviewSection, Sector, SkillRequirement)
from core.ingest.loader import Bundle
from core.ingest.report import IngestReport

# Upper bounds (exclusive) in months for proxy levels 1..4; 96+ months is level 5.
MONTH_BANDS = (12, 36, 60, 96)


def level_from_months(months: int, bands: tuple[int, ...] = MONTH_BANDS) -> int:
    return 1 + sum(1 for b in bands if months >= b)


def _months_between(start: dt.date, end: dt.date) -> set[tuple[int, int]]:
    out, y, m = set(), start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.add((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _coworks(work: list[dict], cutoff: dt.date) -> list[CoworkRecord]:
    """Pairs who were on the same project code on the same days, counted up to `cutoff` (the day
    before the planning horizon), so open-ended or future-dated assignments never invent history."""
    by_code: dict[str, dict[str, list[tuple[dt.date, dt.date]]]] = defaultdict(lambda: defaultdict(list))
    for w in work:
        end = min(w["end_date"], cutoff)
        if w["start_date"] <= end:
            by_code[w["project_code"]][w["person_id"]].append((w["start_date"], end))
    shared_months: dict[tuple[str, str], set] = defaultdict(set)
    shared_codes: dict[tuple[str, str], int] = defaultdict(int)
    for members in by_code.values():
        for a, b in combinations(sorted(members), 2):
            overlap = set()
            for sa, ea in members[a]:
                for sb, eb in members[b]:
                    lo, hi = max(sa, sb), min(ea, eb)
                    if lo <= hi:
                        overlap |= _months_between(lo, hi)
            if overlap:
                shared_months[(a, b)] |= overlap
                shared_codes[(a, b)] += 1
    return [CoworkRecord(a_id=a, b_id=b, co_months=len(ms), project_count=shared_codes[(a, b)])
            for (a, b), ms in sorted(shared_months.items())]


def _all_reviews(reviews: list[dict], items: list[dict], report: IngestReport) -> list[PeerReview]:
    """Every review round is kept (oldest first). The scoring graph averages the rounds within each
    direction, then the two directions, because the rule-based parser returns one ParsedReview per review
    in the same order."""
    picked = defaultdict(lambda: {"positive": [], "negative": []})
    for it in items:
        picked[it["review_id"]][it["polarity"]].append(it["item"])
    rows = sorted(reviews, key=lambda r: (r["reviewed_at"], r["review_id"]))
    pairs = defaultdict(int)
    for r in rows:
        pairs[(r["reviewer_id"], r["reviewee_id"])] += 1
    repeated = sum(1 for n in pairs.values() if n > 1)
    report.notes.append(f"리뷰: 모든 회차 {len(rows)}건을 썼다(평가자→피평가자 {len(pairs)}쌍, 그중 {repeated}쌍은 여러 회차). "
                        "협업 점수는 같은 평가자→피평가자의 회차들을 먼저 평균하고, 그 두 방향을 반반 평균한다.")
    return [PeerReview(reviewer_id=r["reviewer_id"], reviewee_id=r["reviewee_id"],
                       positive=ReviewSection(items=picked[r["review_id"]]["positive"], text=r["positive_text"]),
                       negative=ReviewSection(items=picked[r["review_id"]]["negative"], text=r["negative_text"]))
            for r in rows]


def to_dataset(bundle: Bundle, report: IngestReport) -> tuple[Dataset, list[ParsedReview]]:
    """Raises ValueError (with the report summary) if the bundle or the conversion has errors.
    Appends its own warnings and notes to `report`, so pass the report from load_bundle once."""
    if not report.ok:
        raise ValueError("bundle has validation errors:\n" + report.summary())
    t = bundle.tables
    month_index = {m: i for i, m in enumerate(bundle.horizon)}
    rates = {(r["career_grade"], r["role_type"]): r["monthly_rate"] for r in t["rate_card.csv"]}

    skills: dict[str, dict[str, int]] = defaultdict(dict)
    for s in t["person_skills.csv"]:
        if s["experience_months"] == 0:
            report.warn("person_skills.csv", f"{s['person_id']}의 {s['skill_name']}은 경력 0개월이라 보유 기술로 보지 않았다",
                        row=s["__row__"], column="experience_months")
            continue
        skills[s["person_id"]][s["skill_name"]] = level_from_months(s["experience_months"])
    avail: dict[str, dict[dt.date, float]] = defaultdict(dict)
    for a in t["availability.csv"]:
        if a["month"] in month_index:
            avail[a["person_id"]][a["month"]] = a["available_mm"]

    people = []
    for p in t["people.csv"]:
        rate = rates.get((p["career_grade"], p["role_type"]))
        if rate is None:
            report.error("rate_card.csv", f"{p['person_id']}: {p['career_grade']}·{p['role_type']} 단가가 없다")
            continue
        missing = [m for m in bundle.horizon if m not in avail[p["person_id"]]]
        if missing:     # the loader already rejects this; never fill a gap even if called directly
            report.error("availability.csv", f"{p['person_id']}의 가용률이 없는 달: "
                         + ", ".join(m.strftime("%Y-%m") for m in missing))
            continue
        people.append(Person(id=p["person_id"], name=p["display_name"], grade=Grade(p["career_grade"]),
                             monthly_rate=rate, skills=skills[p["person_id"]],
                             availability=[avail[p["person_id"]][m] for m in bundle.horizon]))

    grade_req = defaultdict(dict)
    for g in t["project_grade_requirements.csv"]:
        # kept even when 0: the MILP grade constraint then forbids that grade, unlike an unlisted grade
        grade_req[g["project_id"]][Grade(g["career_grade"])] = g["headcount"]
    skill_req = defaultdict(list)
    for s in t["project_skill_requirements.csv"]:
        skill_req[s["project_id"]].append(SkillRequirement(
            skill=s["skill_name"], min_level=level_from_months(s["min_experience_months"]),
            headcount=s["headcount"]))

    first, last = bundle.horizon[0], bundle.horizon[-1]
    projects = []
    for j in t["projects.csv"]:
        pid = j["project_id"]
        if j["end_month"] < first or j["start_month"] > last:
            report.warn("projects.csv", f"{pid}는 계획 기간({first:%Y-%m}~{last:%Y-%m}) 밖이라 배치 대상에서 뺐다",
                        row=j["__row__"], column="start_month")
            continue
        start, end = max(j["start_month"], first), min(j["end_month"], last)
        if (start, end) != (j["start_month"], j["end_month"]):
            report.warn("projects.csv", f"{pid}의 기간을 계획 기간에 맞춰 {start:%Y-%m}~{end:%Y-%m}로 잘랐다",
                        row=j["__row__"], column="start_month" if start != j["start_month"] else "end_month")
        if not skill_req[pid]:
            report.error("project_skill_requirements.csv", f"{pid}에 기술 요구가 없다(현행 점수 계산에 1건 이상 필요)")
            continue
        if not grade_req[pid]:
            report.warn("project_grade_requirements.csv", f"{pid}에 등급별 정원이 없다(정원 제약 없이 배치된다)")
        projects.append(Project(id=pid, name=j["project_name"], sector=Sector(j["sector"]),
                                phase=ProjectPhase(j["phase"]), start_month=month_index[start],
                                end_month=month_index[end], grade_headcount=grade_req[pid],
                                requirements=skill_req[pid], monthly_budget=j["monthly_budget"]))

    if not projects:
        report.error("projects.csv", "계획 기간 안에 배치할 프로젝트가 하나도 없다")
    held = {s for p in people for s in p.skills}
    for pid in (p.id for p in projects):
        for r in skill_req[pid]:
            if r.skill not in held:
                report.warn("project_skill_requirements.csv", f"{pid}가 요구하는 {r.skill}을 가진 사람이 없다")

    reviews = _all_reviews(t["reviews.csv"], t["review_items.csv"], report)
    if not report.ok:
        raise ValueError("bundle cannot be converted:\n" + report.summary())

    report.notes.append(f"숙련도: 원천에 레벨이 없어 경력 개월을 대리 레벨로 바꿨다"
                        f"(경계 {MONTH_BANDS}개월 → 1~5). 요구 경력도 같은 구간을 쓴다.")
    cutoff = first - dt.timedelta(days=1)
    report.notes.append(f"협업: 같은 project_code에 같은 날 함께 투입된 기간이 걸친 달을 셌다(상태 무관, "
                        f"{cutoff.isoformat()}까지만 — 진행 중·미래 종료일은 거기서 자름). 같은 달은 여러 프로젝트에서 겹쳐도 1개월이다.")
    planned = {j.id for j in projects}
    current = [CurrentAssignment(person_id=r["person_id"], project_id=r["project_id"], alloc=r["alloc"],
                                 locked=r["locked"] == "Y")
               for r in t.get("current_assignments.csv") or [] if r["project_id"] in planned]
    dropped = len(t.get("current_assignments.csv") or []) - len(current)
    if dropped:
        report.warn("current_assignments.csv", f"{dropped} current assignments point at projects outside the "
                    "planning horizon and are ignored")
    if current:
        report.notes.append(f"현재 투입 {len(current)}건(잠금 {sum(c.locked for c in current)}건)을 연속성 입력으로 넘겼다.")
    ds = Dataset(people=people, projects=projects, coworks=_coworks(t["work_history.csv"], cutoff),
                 reviews=reviews, current=current)
    return ds, parse_reviews_rule_based(ds.reviews)
