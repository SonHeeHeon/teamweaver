import random
from core.config import load_review_items
from core.domain.models import (HORIZON_MONTHS, CoworkRecord, Dataset, Grade,
    PeerReview, Person, Project, ProjectPhase, ReviewSection, Sector, SkillRequirement)

GRADE_RATES = {Grade.SPECIAL: 1600, Grade.SENIOR: 1300, Grade.MID: 1000, Grade.JUNIOR: 750}
GRADE_WEIGHTS = [0.10, 0.30, 0.40, 0.20]          # 특/고/중/초 분포
TECH_SKILLS = ["Java", "Spring", "Python", "React", "Vue", "OracleDB", "MSA",
               "AWS", "Kubernetes", "Spark", "AI/ML", "iOS", "Android", "QA", "PM"]
DOMAIN_SKILLS = ["금융도메인", "공공SI", "제조도메인", "그룹사업무"]
SECTOR_DOMAIN = {Sector.FINANCE: "금융도메인", Sector.PUBLIC: "공공SI", Sector.INTERNAL: "그룹사업무"}
_SURNAMES = list("김이박최정강조윤장임한오서신권황안")
_GIVEN = ["민준", "서연", "지훈", "수빈", "예은", "도현", "하은", "지우", "준서", "유진",
          "현우", "소율", "재원", "다은", "시우", "채원", "건우", "나윤", "태윤", "가은"]

def _template_text(items: list[str], positive: bool) -> str:
    tone = "뛰어나 함께 일하기 좋았습니다" if positive else "아쉬워 협업에 어려움이 있었습니다"
    return f"{', '.join(items)} 측면이 {tone}."

def generate_dataset(n_people: int, n_projects: int, seed: int) -> Dataset:
    if n_people < 3:
        raise ValueError("n_people must be >= 3 (need at least 2 reviewers per person)")
    rng = random.Random(seed)
    review_items = load_review_items()

    people = []
    for i in range(n_people):
        grade = rng.choices(list(Grade), weights=GRADE_WEIGHTS)[0]
        n_sk = rng.randint(3, 6)
        skills = {s: rng.randint(2, 5) for s in rng.sample(TECH_SKILLS, n_sk)}
        if rng.random() < 0.6:
            skills[rng.choice(DOMAIN_SKILLS)] = rng.randint(2, 5)
        avail = [round(rng.choices([1.0, 0.5, 0.0], weights=[0.7, 0.2, 0.1])[0], 1)
                 for _ in range(HORIZON_MONTHS)]
        people.append(Person(id=f"p{i:03d}", name=rng.choice(_SURNAMES) + rng.choice(_GIVEN),
                             grade=grade, monthly_rate=GRADE_RATES[grade],
                             skills=skills, availability=avail))

    n_prop = round(n_projects * 0.35)              # 20개면 제안 7
    projects = []
    for j in range(n_projects):
        phase = ProjectPhase.PROPOSAL if j < n_prop else ProjectPhase.EXECUTION
        sector = rng.choices(list(Sector), weights=[0.5, 0.25, 0.25])[0]
        if phase == ProjectPhase.PROPOSAL:
            start = rng.randint(0, HORIZON_MONTHS - 2); end = start + rng.randint(0, 1)
            hc = {Grade.SPECIAL: rng.randint(0, 1), Grade.SENIOR: 1, Grade.MID: rng.randint(1, 2)}
        else:
            start = rng.randint(0, 1); end = min(HORIZON_MONTHS - 1, start + rng.randint(3, 5))
            hc = {Grade.SPECIAL: rng.randint(0, 1), Grade.SENIOR: rng.randint(1, 2),
                  Grade.MID: rng.randint(2, 3), Grade.JUNIOR: rng.randint(1, 2)}
        hc = {g: n for g, n in hc.items() if n > 0}
        req_skills = rng.sample(TECH_SKILLS, rng.randint(2, 4))
        reqs = [SkillRequirement(skill=s, min_level=rng.randint(2, 4),
                                 headcount=rng.randint(1, 2)) for s in req_skills]
        if sector != Sector.INTERNAL:
            reqs.append(SkillRequirement(skill=SECTOR_DOMAIN[sector], min_level=3, headcount=1))
        budget = int(sum(GRADE_RATES[g] * n for g, n in hc.items()) * rng.uniform(0.85, 1.15))
        projects.append(Project(id=f"j{j:02d}", name=f"{sector.value} 프로젝트 {j:02d}",
                                sector=sector, phase=phase, start_month=start, end_month=end,
                                grade_headcount=hc, requirements=reqs, monthly_budget=budget))

    _calibrate_capacity(people, projects)

    coworks, seen = [], set()
    for _ in range(n_people * 2):
        a, b = rng.sample(range(n_people), 2)
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        coworks.append(CoworkRecord(a_id=f"p{key[0]:03d}", b_id=f"p{key[1]:03d}",
                                    co_months=rng.randint(1, 18), project_count=rng.randint(1, 3)))

    reviews = []
    for i in range(n_people):
        reviewee = f"p{i:03d}"
        partners = [c for c in coworks if reviewee in (c.a_id, c.b_id)]
        rng.shuffle(partners)
        reviewers = [(c.a_id if c.b_id == reviewee else c.b_id) for c in partners]
        while len(reviewers) < 2:                  # 협업자가 부족하면 무작위 보충
            cand = f"p{rng.randrange(n_people):03d}"
            if cand != reviewee and cand not in reviewers:
                reviewers.append(cand)
        for rv in reviewers[: rng.randint(2, 4)]:
            pos = rng.sample(review_items, rng.randint(1, 5))
            neg = rng.sample([x for x in review_items if x not in pos], rng.randint(1, 5))
            reviews.append(PeerReview(
                reviewer_id=rv, reviewee_id=reviewee,
                positive=ReviewSection(items=pos, text=_template_text(pos, True)),
                negative=ReviewSection(items=neg, text=_template_text(neg, False))))

    return Dataset(people=people, projects=projects, coworks=coworks, reviews=reviews)

def capacity_ratio(ds: Dataset) -> float:
    ratios = []
    for m in range(HORIZON_MONTHS):
        demand = sum(sum(p.grade_headcount.values()) for p in ds.projects if m in p.months)
        supply = sum(pr.availability[m] for pr in ds.people)
        if supply > 0:
            ratios.append(demand / supply)
    return max(ratios)

def _calibrate_capacity(people: list[Person], projects: list[Project]) -> None:
    ds = Dataset(people=people, projects=projects, coworks=[], reviews=[])
    bottomed_out = set()
    while capacity_ratio(ds) > 0.85:
        # Find the largest project not yet bottomed out
        shrinkable = [p for p in projects if id(p) not in bottomed_out]
        if not shrinkable:
            raise ValueError("cannot calibrate: demand floor exceeds 0.85 * supply")
        pj = max(shrinkable, key=lambda p: sum(p.grade_headcount.values()))
        g = max(pj.grade_headcount, key=pj.grade_headcount.get)
        if pj.grade_headcount[g] > 1:
            pj.grade_headcount[g] -= 1
        else:
            pj.grade_headcount.pop(g)
        if not pj.grade_headcount:
            pj.grade_headcount[Grade.MID] = 1
            bottomed_out.add(id(pj))
