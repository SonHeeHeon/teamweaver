"""입력 단계의 기술 이름 사전 연결(core/ingest/convert.to_dataset, 2026-10-09) -- 별칭 통일, 부분 인정, 요구 중복 합치기, 모르는 이름 경고."""
import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from core.ingest.convert import level_from_months, to_dataset
from core.ingest.loader import load_bundle
from core.ingest.skills import load_dictionary

DEMO = Path(__file__).resolve().parents[2] / "demo" / "org-n100"


def _copy(tmp_path: Path, edit) -> Path:
    """시연 묶음 사본에 edit(파일 이름 → 행 목록 dict)를 적용하고 manifest의 sha256을 다시 쓴다."""
    root = tmp_path / "bundle"
    shutil.copytree(DEMO, root)
    tables = {}
    for f in ("person_skills.csv", "project_skill_requirements.csv"):
        with open(root / f, encoding="utf-8", newline="") as fh:
            r = csv.DictReader(fh)
            tables[f] = (r.fieldnames, list(r))
    edit({k: v[1] for k, v in tables.items()})
    for f, (cols, rows) in tables.items():
        with open(root / f, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    m["files"] = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in m["files"]}
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    return root


def _ds(root, **kw):
    b, rep = load_bundle(root)
    ds, _ = to_dataset(b, rep, **kw)
    return ds, rep


@pytest.fixture(scope="module")
def original():
    return _ds(DEMO)


@pytest.fixture(scope="module")
def plain():
    return _ds(DEMO, skill_dictionary=False)


def test_aliases_recover_the_original_dataset(tmp_path, original):
    sd = load_dictionary()
    alias = {c["label"]: c["aliases"][0] for c in sd.concepts.values() if c.get("aliases")}

    def edit(t):
        for f in t:
            for i, r in enumerate(t[f]):
                if i % 2 == 0 and r["skill_name"] in alias:
                    r["skill_name"] = alias[r["skill_name"]]
    root = _copy(tmp_path, edit)
    ds, rep = _ds(root)
    want, _ = original
    assert {p.id: p.skills for p in ds.people} == {p.id: p.skills for p in want.people}
    assert {j.id: [(r.skill, r.min_level, r.headcount) for r in j.requirements] for j in ds.projects} == \
           {j.id: [(r.skill, r.min_level, r.headcount) for r in j.requirements] for j in want.projects}
    assert any("별칭" in n and "대표 이름" in n for n in rep.notes)
    broken, _ = _ds(root, skill_dictionary=False)              # 사전 없이는 같은 사람의 같은 기술이 다른 이름으로 갈린다
    assert {p.id: p.skills for p in broken.people} != {p.id: p.skills for p in want.people}


def test_partial_credit_only_raises_levels(original, plain):
    ds, rep = original
    base = {p.id: p.skills for p in plain[0].people}
    raised = 0
    for p in ds.people:
        for s, lv in p.skills.items():
            assert lv >= base[p.id].get(s, 0)
            raised += lv > base[p.id].get(s, 0)
    assert raised > 0
    assert any("부분 인정" in n for n in rep.notes)


def test_partial_credit_example_spring_to_java(original):
    """시연 데이터에서 Java의 하위 기술은 Spring뿐이다: Java 레벨 = (본인 Java 경력, Spring 경력 × 계수(0.7) 내림) 중 긴 쪽의 레벨."""
    import math
    from core.ingest.convert import lookback_start
    from core.ingest.skills import load_dictionary
    k = load_dictionary().narrower_credit
    ds, _ = original
    b, _ = load_bundle(DEMO)
    since = lookback_start(b.horizon[0])
    months = {(r["person_id"], r["skill_name"]): min(r["experience_months"], 120) for r in b.tables["person_skills.csv"]
              if r["experience_months"] and (r.get("last_used_month") is None or r["last_used_month"] >= since)}
    springers = [p for p in ds.people if (p.id, "Spring") in months]
    assert springers
    for p in springers:
        want = max(months.get((p.id, "Java"), 0), math.floor(months[(p.id, "Spring")] * k + 1e-9))
        if want == 0:                                   # 인정분이 0개월이면 인정하지 않는다
            assert "Java" not in p.skills
        else:
            assert p.skills["Java"] == level_from_months(want)
    assert any(math.floor(months[(p.id, "Spring")] * k + 1e-9) > months.get((p.id, "Java"), 0) for p in springers)


def test_credit_zero_equals_no_hierarchy(original, plain):
    ds, _ = _ds(DEMO, partial_credit=0.0)
    assert {p.id: p.skills for p in ds.people} == {p.id: p.skills for p in plain[0].people}


def test_duplicate_requirement_by_alias_is_merged(tmp_path):
    def edit(t):
        rows = t["project_skill_requirements.csv"]
        first = rows[0]
        rows.insert(1, {**first, "skill_name": load_dictionary().lookup(first["skill_name"])["aliases"][0],
                        "min_experience_months": str(int(first["min_experience_months"]) + 40), "headcount": "1"})
    root = _copy(tmp_path, edit)
    ds, rep = _ds(root)
    first = next(r for r in csv.DictReader(open(DEMO / "project_skill_requirements.csv", encoding="utf-8")))
    j = next(j for j in ds.projects if j.id == first["project_id"])
    same = [r for r in j.requirements if r.skill == first["skill_name"]]
    assert len(same) == 1
    # 더 높은 레벨을 요구한 행(별칭 행: +40개월, 1명)을 그대로 쓴다 -- 다른 행의 인원과 섞지 않는다
    assert (same[0].min_level, same[0].headcount) == (level_from_months(int(first["min_experience_months"]) + 40), 1)
    assert any("두 번 요구" in i.message and "1명" in i.message for i in rep.warnings)


def test_unknown_names_are_reported_not_merged(tmp_path):
    def edit(t):
        t["person_skills.csv"][0]["skill_name"] = "사내 전용 솔루션 Z"
    root = _copy(tmp_path, edit)
    ds, rep = _ds(root)
    pid = next(r for r in csv.DictReader(open(DEMO / "person_skills.csv", encoding="utf-8")))["person_id"]
    assert "사내 전용 솔루션 Z" in next(p for p in ds.people if p.id == pid).skills
    assert any("사전에 없는 기술 이름" in i.message and "사내 전용 솔루션 Z" in i.message for i in rep.warnings)


# ---------------------------------------------------------------- 인정 경력 구분(2026-10-11, Person.skill_credit)
def test_skill_credit_marks_exactly_the_skills_whose_months_came_from_a_narrower_skill(original):
    import math
    from core.ingest.convert import lookback_start
    from core.ingest.skills import load_dictionary, person_skill_months
    ds, _ = original
    b, _ = load_bundle(DEMO)
    since = lookback_start(b.horizon[0])
    rows = {}
    for r in b.tables["person_skills.csv"]:
        if r["experience_months"] and (r.get("last_used_month") is None or r["last_used_month"] >= since):
            rows.setdefault(r["person_id"], []).append((r["skill_name"], min(r["experience_months"], 120)))
    sd = load_dictionary()
    total = 0
    for p in ds.people:
        sm = person_skill_months(rows.get(p.id, []), sd)
        want = {k for k, imp in sm.implied.items() if imp["months"] > sm.own.get(k, 0)}
        assert set(p.skill_credit) == want
        for k, c in p.skill_credit.items():
            assert c.months == sm.months[k] > c.own_months and c.source == sm.implied[k]["from"]
            assert c.months == math.floor(sm.own[c.source] * sd.narrower_credit ** c.depth + 1e-9)
        total += len(p.skill_credit)
    assert total > 0


def test_skill_credit_is_empty_without_the_hierarchy(plain):
    assert all(p.skill_credit == {} for p in plain[0].people)


def test_skill_credit_must_name_a_held_skill():
    from core.domain.models import Grade, Person, SkillCredit
    with pytest.raises(ValueError):
        Person(id="P1", name="a", grade=Grade.MID, monthly_rate=1, skills={"Python": 2}, availability=[1.0] * 6,
               skill_credit={"Java": SkillCredit(source="Spring", months=12, depth=1)})
