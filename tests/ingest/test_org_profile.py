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
    groups = {"DP": 0, "AI": 0}
    for row in b.tables["people.csv"]:
        groups[row["person_id"][:2]] += 1
    assert groups == {100: {"DP": 100, "AI": 0}, 200: {"DP": 100, "AI": 100}, 300: {"DP": 100, "AI": 200}}[size]
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
