"""Organisation-shaped rehearsal bundles (core/ingest/org_profile.py)."""
import json
import re

import pytest

from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.org_profile import FLAGSHIP, LLM_ERA, LLM_ERA_MONTHS, generate_org_bundle


@pytest.fixture(scope="module", params=[100, 200, 300])
def bundle(request, tmp_path_factory):
    size = request.param
    root = generate_org_bundle(tmp_path_factory.mktemp(f"n{size}"), size, seed=7)
    b, report = load_bundle(root)
    ds, parsed = to_dataset(b, report)
    return size, root, b, report, ds


def test_bundle_passes_the_contract_with_no_errors(bundle):
    _, _, _, report, _ = bundle
    assert report.errors == [] and report.warnings == []


def test_group_composition_matches_the_size(bundle):
    size, _, b, _, ds = bundle
    groups = {}
    for row in b.tables["people.csv"]:
        groups[row["person_id"][:2]] = groups.get(row["person_id"][:2], 0) + 1
    assert groups == {100: {"DP": 100}, 200: {"DP": 100, "AI": 100}, 300: {"DP": 100, "AI": 100, "AU": 100}}[size]
    assert len(ds.projects) == size // 5


def test_flagship_keeps_its_internal_seats(bundle):
    size, _, _, _, ds = bundle
    flagship = next(j for j in ds.projects if j.id == FLAGSHIP["project_id"])
    assert sum(flagship.grade_headcount.values()) == (8 if size == 100 else 28)
    assert flagship.phase.value == "실행"


def test_no_real_client_names_are_written(bundle):
    _, root, _, _, _ = bundle
    text = "".join(p.read_text(encoding="utf-8") for p in root.iterdir())
    for banned in ("우리은행", "삼성", "Samsung", "Woori"):
        assert banned not in text


def test_llm_era_skills_never_exceed_the_era(bundle):
    _, _, b, _, _ = bundle
    for row in b.tables["person_skills.csv"]:
        if row["skill_name"] in LLM_ERA:
            assert row["experience_months"] <= LLM_ERA_MONTHS


def test_people_have_about_twenty_skills(bundle):
    _, _, _, _, ds = bundle
    avg = sum(len(p.skills) for p in ds.people) / len(ds.people)
    assert 15 <= avg <= 22


def test_projects_mix_execution_and_proposal_with_labels(bundle):
    _, _, b, _, _ = bundle
    rows = b.tables["projects.csv"]
    phases = [r["phase"] for r in rows]
    assert 0.25 <= phases.count("제안") / len(phases) <= 0.45
    assert all(re.match(r"^\[(제안|실행)\] ", r["project_name"]) for r in rows[1:])


def test_generation_is_deterministic(tmp_path):
    a = generate_org_bundle(tmp_path / "a", 200, seed=11)
    c = generate_org_bundle(tmp_path / "c", 200, seed=11)
    assert json.loads((a / "manifest.json").read_text())["files"] == json.loads((c / "manifest.json").read_text())["files"]


def test_unknown_size_is_refused(tmp_path):
    with pytest.raises(ValueError):
        generate_org_bundle(tmp_path, 150, seed=1)


def test_current_roster_respects_grades_availability_and_locks(bundle):
    size, _, b, _, ds = bundle
    rows = b.tables["current_assignments.csv"]
    assert rows and any(r["locked"] == "Y" for r in rows) or size == 100
    grade = {p["person_id"]: p["career_grade"] for p in b.tables["people.csv"]}
    asked = {(r["project_id"], r["career_grade"]) for r in b.tables["project_grade_requirements.csv"]}
    assert all((r["project_id"], grade[r["person_id"]]) in asked for r in rows)
    assert all(0.3 <= r["alloc"] <= 1.0 for r in rows)
    assert len(ds.current) == len(rows) and any(c.project_id == "J001" for c in ds.current)


def test_roster_does_not_change_the_other_tables(tmp_path, monkeypatch):
    """The roster has its own RNG stream: with it removed, every other file is byte-identical."""
    import core.ingest.org_profile as op
    a = generate_org_bundle(tmp_path / "a", 200, seed=5)
    monkeypatch.setattr(op, "_current_roster", lambda *args, **kw: [])
    c = op.generate_org_bundle(tmp_path / "c", 200, seed=5)
    for f in a.iterdir():
        if f.name not in ("current_assignments.csv", "manifest.json"):
            assert f.read_bytes() == (c / f.name).read_bytes(), f.name


def test_reviews_are_long_contextual_and_use_the_org_item_list(bundle):
    from core.ingest.review_text import ORG_REVIEW_ITEMS
    _, _, b, _, _ = bundle
    reviews = b.tables["reviews.csv"]
    assert all(r["positive_text"].count("다.") >= 2 for r in reviews)           # opener + at least one strength
    assert sum(len(r["positive_text"]) for r in reviews) / len(reviews) > 120
    assert {i["item"] for i in b.tables["review_items.csv"]} <= set(ORG_REVIEW_ITEMS)
    assert "기획력" in ORG_REVIEW_ITEMS and "친화력" in ORG_REVIEW_ITEMS and len(ORG_REVIEW_ITEMS) == 20


def test_about_ten_reviews_per_person_a_year_and_ten_year_history(bundle):
    size, _, b, _, _ = bundle
    per_year = len(b.tables["reviews.csv"]) / size / 1.5              # three half-year rounds
    assert 6 <= per_year <= 12
    first = min(w["start_date"] for w in b.tables["work_history.csv"])
    horizon = b.horizon[0]
    assert (horizon.year - first.year) * 12 + horizon.month - first.month <= 120


def _bundle_with_abilities(tmp_path, monkeypatch, n=200, seed=3):
    import core.ingest.org_profile as op
    seen = {}
    real = op._person_traits

    def spy(rng, people):
        real(rng, people)
        seen.update({p["person_id"]: p["_ability"] for p in people})
    monkeypatch.setattr(op, "_person_traits", spy)
    b, rep = load_bundle(op.generate_org_bundle(tmp_path / "b", n, seed=seed))
    assert rep.errors == []
    return b, seen


def _corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5


def test_outcomes_follow_a_hidden_rule_that_reviews_only_partly_reveal(tmp_path, monkeypatch):
    """Outcomes cover scores 1-5, customer replacements only on poor projects, better hidden ability -> better
    outcomes. Reviews reflect ability only partly (measured 0.3-0.45; it was 0.97 before the Opus review MUST),
    so calibrating on outcomes cannot just read the answer back from the review term."""
    import collections
    b, ability = _bundle_with_abilities(tmp_path, monkeypatch)
    out = {r["project_code"]: r for r in b.tables["project_outcomes.csv"]}
    assert {r["customer_score"] for r in out.values()} == {1, 2, 3, 4, 5}
    for r in b.tables["replacements.csv"]:
        if r["requested_by"] == "고객":
            assert out[r["project_code"]]["customer_score"] <= 2
    team = collections.defaultdict(set)
    for w in b.tables["work_history.csv"]:
        team[w["project_code"]].add(w["person_id"])
    codes = sorted(out)
    team_ability = [sum(ability[m] for m in team[c]) / len(team[c]) for c in codes]
    assert _corr(team_ability, [out[c]["customer_score"] for c in codes]) > 0.3
    n_items = collections.Counter((i["review_id"], i["polarity"]) for i in b.tables["review_items.csv"])
    pol = collections.defaultdict(list)
    for r in b.tables["reviews.csv"]:
        pol[r["reviewee_id"]].append(n_items[(r["review_id"], "positive")] - n_items[(r["review_id"], "negative")])
    ids = sorted(pol)
    c = _corr([sum(pol[p]) / len(pol[p]) for p in ids], [ability[p] for p in ids])
    assert 0.15 < c < 0.7


def test_replaced_people_leave_mid_stint_and_have_no_later_row(bundle):
    _, _, b, _, _ = bundle
    rows = {}
    for w in b.tables["work_history.csv"]:
        rows.setdefault((w["project_code"], w["person_id"]), []).append(w)
    reps = b.tables["replacements.csv"]
    assert reps
    for r in reps:
        stints = rows[(r["project_code"], r["person_id"])]
        assert max(w["end_date"] for w in stints) == r["replaced_at"]


def test_pre_llm_work_summaries_do_not_mention_llm_skills(bundle):
    from core.ingest.org_profile import LLM_ERA
    _, _, b, _, _ = bundle
    horizon = b.horizon[0]
    for w in b.tables["work_history.csv"]:
        months_before = (horizon.year - w["end_date"].year) * 12 + horizon.month - w["end_date"].month
        if months_before > 36:
            assert not any(s in (w["summary"] or "") for s in LLM_ERA), w["summary"]


def test_unknown_people_in_replacements_are_a_warning_not_an_error(tmp_path):
    import csv
    from core.ingest.org_profile import generate_org_bundle
    root = generate_org_bundle(tmp_path / "b", 100, seed=5)
    path = root / "replacements.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[0]["person_id"] = "LEFT-0001"                      # someone who has left the company
    import json
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    m.pop("files", None)                                    # the edit would otherwise fail the hash check
    (root / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    _, rep = load_bundle(root)
    assert rep.errors == []
    assert any("LEFT-0001" in str(x) for x in rep.warnings)


def test_committed_demo_bundle_validates_and_matches_the_generator(tmp_path):
    """demo/org-n100 is what run_poc.sh boots; regenerate with `python -m rehearsal.make_demo` after
    changing the generator."""
    from pathlib import Path
    from core.ingest.org_profile import generate_org_bundle
    from rehearsal.run import SEED
    demo = Path(__file__).resolve().parents[2] / "demo" / "org-n100"
    _, rep = load_bundle(demo)
    assert rep.errors == []
    fresh = generate_org_bundle(tmp_path / "fresh", 100, seed=SEED)
    for f in sorted(fresh.iterdir()):
        assert (demo / f.name).read_bytes() == f.read_bytes(), f"{f.name} is stale: rerun python -m rehearsal.make_demo"
    operating = demo.parent / "org-n100-operating"
    fresh_op = generate_org_bundle(tmp_path / "fresh-op", 100, seed=SEED, scenario="operating")
    for f in sorted(fresh_op.iterdir()):
        assert (operating / f.name).read_bytes() == f.read_bytes(), f"operating {f.name} is stale: rerun make_demo"
    import io
    import zipfile
    for n, scenario, suffix in [(n, sc, sf) for n in (200, 300) for sc, sf in (("planning", ""), ("operating", "-operating"))]:
        root = generate_org_bundle(tmp_path / f"n{n}{suffix}", n, seed=SEED, scenario=scenario)
        with zipfile.ZipFile(demo.parent / f"org-n{n}{suffix}.zip") as zf:
            assert sorted(zf.namelist()) == sorted(p.name for p in root.iterdir())
            for p in root.iterdir():
                assert zf.read(p.name) == p.read_bytes(), f"org-n{n}{suffix}.zip is stale: rerun python -m rehearsal.make_demo"


def test_senior_careers_fill_the_window_and_juniors_start_late(bundle):
    """Careers are longer than 10 years for seniors (up to 25), but only the last 10 years are exported."""
    _, _, b, _, _ = bundle
    grade = {p["person_id"]: p["career_grade"] for p in b.tables["people.csv"]}
    first = {}
    for w in b.tables["work_history.csv"]:
        first[w["person_id"]] = min(first.get(w["person_id"], w["start_date"]), w["start_date"])
    window = min(first.values())
    seniors = [d for pid, d in first.items() if grade[pid] == "특급"]
    juniors = [d for pid, d in first.items() if grade[pid] == "초급"]
    # 13+ years of service: the window is full, except someone between assignments right at its start
    assert sum(d == window for d in seniors) / len(seniors) > 0.8
    assert all((d.year - window.year) * 12 + d.month - window.month <= 12 for d in seniors)
    assert all(d > window for d in juniors)                              # at most 6 years of service
    assert max(int(s["experience_months"]) for s in b.tables["person_skills.csv"]) <= 120



def test_operating_scenario_matches_the_described_situation(tmp_path):
    """User: most people are already on running projects; 2-3 new proposals are staffed from ~10 people just freed."""
    import json
    from core.ingest.org_profile import generate_org_bundle
    root = generate_org_bundle(tmp_path / "op", 100, seed=7, scenario="operating")
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    b, report = load_bundle(root)
    ds, _ = to_dataset(b, report)
    assert report.errors == [] and m["scenario"] == "operating" and len(m["proposals"]) == 2
    on = {c.person_id for c in ds.current}
    assert len(on) == 90 and set(m["bench"]) == {p.id for p in ds.people} - on
    for p in ds.projects:
        team = [c for c in ds.current if c.project_id == p.id]
        if p.id in m["proposals"]:
            assert team == [] and p.start_month == 1
        else:                                   # a running project's seats are exactly its current team
            grades = {}
            for c in team:
                g = next(pp.grade for pp in ds.people if pp.id == c.person_id)
                grades[g] = grades.get(g, 0) + 1
            assert grades == {g: k for g, k in p.grade_headcount.items() if k}
    plan = generate_org_bundle(tmp_path / "plan", 100, seed=7)
    assert (root / "people.csv").read_bytes() == (plan / "people.csv").read_bytes()      # same organisation
