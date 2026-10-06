"""demo/org-n100-broken.zip: the upload check points at each planted mistake (demo scene D, 2026-10-06)."""
import tempfile
import zipfile
from pathlib import Path

from core.ingest.loader import load_bundle
from rehearsal.make_broken_demo import OUT, build


def test_every_planted_mistake_is_reported_with_file_row_and_column():
    d = Path(tempfile.mkdtemp())
    (d / "b.zip").write_bytes(build())
    zipfile.ZipFile(d / "b.zip").extractall(d / "b")
    _, report = load_bundle(d / "b")
    got = {(e.file, e.row, e.column) for e in report.errors}
    assert got == {("people.csv", 2, "career_grade"), ("availability.csv", 3, "available_mm"),
                   ("reviews.csv", 1, "reviewer_id"), ("projects.csv", 2, "end_month"),
                   ("current_assignments.csv", 2, "person_id,project_id"),
                   ("person_skills.csv", 1, "experience_months")}
    assert OUT.read_bytes() == build()                  # the committed zip is current


def test_guard_demo_accepts_the_faithful_quote_and_rejects_the_rest():
    from rehearsal.guard_demo import cases
    rows = cases()
    assert [r["verdict"].startswith("채택") for r in rows] == [True, False, False]
    assert [r["code"] for r in rows[1:]] == ["quote_not_verbatim", "unknown_marker"]
