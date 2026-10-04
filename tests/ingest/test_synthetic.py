import csv
import datetime as dt
import json
import statistics
from collections import Counter

import pytest

from core.graph.memory_graph import MemoryGraph
from core.ingest.__main__ import main
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.synthetic import generate_bundle
from core.optimize.milp import MilpParams, solve_milp_diagnostic
from core.evaluate.plan_eval import evaluate_plan
from core.scoring.engine import ScoringEngine


def _rows(root, name):
    with (root / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory):
    return generate_bundle(tmp_path_factory.mktemp("syn"), 40, 8, seed=7)


def test_generated_bundle_loads_and_converts_without_errors(bundle_dir):
    bundle, report = load_bundle(bundle_dir)
    assert report.ok and not report.warnings, report.summary()
    ds, parsed = to_dataset(bundle, report)
    assert len(ds.people) == 40 and len(ds.projects) == 8 and ds.coworks and ds.reviews
    assert json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))["synthetic"] is True


def test_generated_bundle_solves_and_the_displayed_plan_respects_every_constraint(bundle_dir):
    # Raw CBC values can exceed a budget by ~1e-6 and fail the strict raw validator; that is the solver
    # tolerance issue Codex handles in C1, not a data problem. The plan users see must be feasible.
    bundle, report = load_bundle(bundle_dir)
    ds, parsed = to_dataset(bundle, report)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    params = MilpParams(time_limit=60)
    raw = solve_milp_diagnostic(graph, S, C, params)
    assert raw.plan.entries
    assert evaluate_plan(graph, S, C, params, raw.plan.entries).violations == ()


def test_same_seed_is_reproducible_and_another_seed_differs(tmp_path):
    a = json.loads((generate_bundle(tmp_path / "a", 30, 6, seed=3) / "manifest.json").read_text())["files"]
    b = json.loads((generate_bundle(tmp_path / "b", 30, 6, seed=3) / "manifest.json").read_text())["files"]
    c = json.loads((generate_bundle(tmp_path / "c", 30, 6, seed=4) / "manifest.json").read_text())["files"]
    assert a == b and a != c


def test_manifest_hashes_match_the_files(bundle_dir):
    import hashlib
    files = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert all(hashlib.sha256((bundle_dir / n).read_bytes()).hexdigest() == h for n, h in files.items())


def test_reviews_only_come_from_people_who_shared_that_project(bundle_dir):
    work = _rows(bundle_dir, "work_history.csv")
    spans = {}
    for w in work:
        spans.setdefault((w["person_id"], w["project_code"]), []).append(
            (dt.date.fromisoformat(w["start_date"]), dt.date.fromisoformat(w["end_date"])))
    for r in _rows(bundle_dir, "reviews.csv"):
        a = spans[(r["reviewer_id"], r["project_code"])]
        b = spans[(r["reviewee_id"], r["project_code"])]
        assert any(max(sa, sb) <= min(ea, eb) for sa, ea in a for sb, eb in b), r["review_id"]


def test_skill_experience_never_exceeds_the_persons_worked_months(bundle_dir):
    worked = Counter()
    seen = set()
    for w in _rows(bundle_dir, "work_history.csv"):
        s, e = dt.date.fromisoformat(w["start_date"]), dt.date.fromisoformat(w["end_date"])
        y, m = s.year, s.month
        while (y, m) <= (e.year, e.month):
            if (w["person_id"], y, m) not in seen:
                seen.add((w["person_id"], y, m))
                worked[w["person_id"]] += 1
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for s in _rows(bundle_dir, "person_skills.csv"):
        assert 0 < int(s["experience_months"]) <= worked[s["person_id"]]


def test_distributions_follow_the_schema_answers(bundle_dir):
    skills = Counter(r["person_id"] for r in _rows(bundle_dir, "person_skills.csv"))
    assert 12 <= statistics.median(skills.values()) <= 28            # "보통 20개"


def test_cli_generate_and_check(tmp_path, capsys):
    out = tmp_path / "cli"
    assert main(["generate", "--people", "20", "--projects", "4", "--seed", "1", "--out", str(out)]) == 0
    assert main(["check", str(out)]) == 0
    assert "OK: 20 people" in capsys.readouterr().out
    (out / "people.csv").write_text("person_id\nP0001\n", encoding="utf-8")
    assert main(["check", str(out)]) == 1


def test_too_small_requests_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        generate_bundle(tmp_path / "x", 2, 1, seed=1)


def test_history_and_reviews_stay_before_a_non_default_horizon(tmp_path):
    root = generate_bundle(tmp_path / "early", 20, 4, seed=1, horizon_start="2025-01")
    first = dt.date(2025, 1, 1)
    assert all(dt.date.fromisoformat(r["reviewed_at"]) < first for r in _rows(root, "reviews.csv"))
    assert all(dt.date.fromisoformat(w["end_date"]) < first for w in _rows(root, "work_history.csv"))
    assert all(r["last_used_month"] < "2025-01" for r in _rows(root, "person_skills.csv"))
    bundle, report = load_bundle(root)
    to_dataset(bundle, report)
    assert report.ok and not report.warnings, report.summary()


@pytest.mark.parametrize("bad", ["2026-1", "2026-13", "abcd"])
def test_malformed_horizon_start_is_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        generate_bundle(tmp_path / "x", 10, 2, seed=1, horizon_start=bad)


def test_cli_generate_reports_bad_input_with_exit_code_2(tmp_path, capsys):
    assert main(["generate", "--people", "2", "--projects", "1", "--seed", "1", "--out", str(tmp_path / "x")]) == 2
    assert "error:" in capsys.readouterr().err


def test_editing_a_file_after_generation_is_caught_by_the_manifest_hash(tmp_path):
    root = generate_bundle(tmp_path / "h", 10, 2, seed=1)
    with (root / "rate_card.csv").open("a", encoding="utf-8") as fh:
        fh.write("")
    rows = (root / "availability.csv").read_text(encoding="utf-8").replace(",1.0\n", ",0.5\n", 1)
    (root / "availability.csv").write_text(rows, encoding="utf-8")
    _, report = load_bundle(root)
    assert any(i.file == "availability.csv" and "sha256" in i.message for i in report.errors)


@pytest.mark.parametrize("n_people", [3, 12, 30, 57])
@pytest.mark.parametrize("seed", [0, 5, 11])
def test_generated_bundles_are_valid_across_sizes_and_seeds(tmp_path, n_people, seed):
    root = generate_bundle(tmp_path / "s", n_people, max(1, n_people // 5), seed=seed)
    bundle, report = load_bundle(root)
    to_dataset(bundle, report)
    assert report.ok, report.summary()


def test_mapping_file_may_be_listed_in_manifest_hashes(tmp_path):
    import hashlib
    root = generate_bundle(tmp_path / "m", 10, 2, seed=1)
    (root / "mapping.json").write_text("{}", encoding="utf-8")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["mapping.json"] = hashlib.sha256(b"{}").hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _, report = load_bundle(root)
    assert report.ok, report.summary()
