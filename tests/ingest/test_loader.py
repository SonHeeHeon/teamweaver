import datetime as dt
import json

import pytest

from core.ingest.loader import load_bundle
from tests.ingest.bundle_factory import MANIFEST, base_tables, write_bundle


def _load(tmp_path, tables=None, **kw):
    return load_bundle(write_bundle(tmp_path / "b", tables, **kw))


def _codes(report):
    return {(i.level, i.file, i.row, i.column) for i in report.issues}


def test_valid_bundle_loads_with_typed_values(tmp_path):
    bundle, report = _load(tmp_path)
    assert report.ok, report.summary()
    assert report.row_counts["people.csv"] == 3
    skill = bundle.tables["person_skills.csv"][0]
    assert skill["experience_months"] == 44 and skill["last_used_month"] == dt.date(2026, 8, 1)
    assert bundle.tables["person_skills.csv"][1]["last_used_month"] is None
    assert bundle.tables["availability.csv"][0]["available_mm"] == 1.0
    assert bundle.tables["work_history.csv"][0]["start_date"] == dt.date(2025, 1, 1)
    assert bundle.horizon == [dt.date(2026, 10, 1), dt.date(2026, 11, 1), dt.date(2026, 12, 1),
                              dt.date(2027, 1, 1), dt.date(2027, 2, 1), dt.date(2027, 3, 1)]


def test_excel_bom_is_accepted(tmp_path):
    _, report = _load(tmp_path, encoding="utf-8-sig")
    assert report.ok, report.summary()


def test_mapping_renames_real_export_headers(tmp_path):
    tables = base_tables()
    tables["people.csv"] = [{"사번": r["person_id"], "이름": r["display_name"], "등급": r["career_grade"],
                             "유형": r["role_type"]} for r in tables["people.csv"]]
    mapping = {"people.csv": {"person_id": "사번", "display_name": "이름", "career_grade": "등급", "role_type": "유형"}}
    bundle, report = _load(tmp_path, tables, mapping=mapping)
    assert report.ok, report.summary()
    assert bundle.tables["people.csv"][0]["person_id"] == "P1"


def test_missing_file_and_missing_column_are_errors(tmp_path):
    tables = base_tables()
    del tables["rate_card.csv"]
    for r in tables["projects.csv"]:
        del r["monthly_budget"]
    _, report = _load(tmp_path, tables)
    assert ("error", "rate_card.csv", None, None) in _codes(report)
    assert ("error", "projects.csv", None, "monthly_budget") in _codes(report)


def test_unknown_column_is_a_warning(tmp_path):
    tables = base_tables()
    tables["people.csv"][0]["memo"] = "x"
    for r in tables["people.csv"][1:]:
        r["memo"] = ""
    _, report = _load(tmp_path, tables)
    assert report.ok and ("warning", "people.csv", None, "memo") in _codes(report)


@pytest.mark.parametrize("file,row,column,value", [
    ("people.csv", 1, "display_name", ""),                    # required value missing
    ("people.csv", 2, "career_grade", "부장"),                 # not an allowed grade
    ("person_skills.csv", 1, "experience_months", "3.5"),      # not an integer
    ("person_skills.csv", 1, "experience_months", "-1"),       # below minimum
    ("availability.csv", 1, "available_mm", "1.2"),           # above maximum
    ("availability.csv", 1, "available_mm", "50%"),           # not a number
    ("work_history.csv", 1, "start_date", "2025/01/01"),      # not ISO date
    ("projects.csv", 1, "start_month", "2026-13"),            # not a month
    ("project_skill_requirements.csv", 1, "headcount", "0"),  # below minimum
])
def test_bad_values_are_reported_with_row_and_column(tmp_path, file, row, column, value):
    tables = base_tables()
    tables[file][row - 1][column] = value
    _, report = _load(tmp_path, tables)
    assert ("error", file, row, column) in _codes(report), report.summary()


def test_duplicate_keys_are_errors(tmp_path):
    tables = base_tables()
    tables["person_skills.csv"].append(dict(tables["person_skills.csv"][0]))
    _, report = _load(tmp_path, tables)
    assert ("error", "person_skills.csv", 5, "person_id,skill_name") in _codes(report)


def test_dangling_references_are_errors(tmp_path):
    tables = base_tables()
    tables["work_history.csv"][0]["person_id"] = "NOBODY"
    tables["review_items.csv"][0]["review_id"] = "R404"
    _, report = _load(tmp_path, tables)
    assert ("error", "work_history.csv", 1, "person_id") in _codes(report)
    assert ("error", "review_items.csv", 1, "review_id") in _codes(report)


def test_missing_horizon_month_is_an_error_never_assumed_available(tmp_path):
    tables = base_tables()
    tables["availability.csv"] = [r for r in tables["availability.csv"]
                                   if not (r["person_id"] == "P2" and r["month"] == "2026-12")]
    _, report = _load(tmp_path, tables)
    assert any(i.level == "error" and i.file == "availability.csv" and "P2" in i.message and "2026-12" in i.message
               for i in report.issues), report.summary()


def test_availability_outside_horizon_is_a_warning(tmp_path):
    tables = base_tables()
    tables["availability.csv"].append({"person_id": "P1", "month": "2027-04", "available_mm": "1.0"})
    _, report = _load(tmp_path, tables)
    assert report.ok and ("warning", "availability.csv", 19, "month") in _codes(report)


def test_end_before_start_is_an_error(tmp_path):
    tables = base_tables()
    tables["work_history.csv"][0]["end_date"] = "2024-12-31"
    tables["projects.csv"][0]["end_month"] = "2026-09"
    _, report = _load(tmp_path, tables)
    assert ("error", "work_history.csv", 1, "end_date") in _codes(report)
    assert ("error", "projects.csv", 1, "end_month") in _codes(report)


def test_self_review_is_an_error(tmp_path):
    tables = base_tables()
    tables["reviews.csv"][0]["reviewer_id"] = "P1"
    _, report = _load(tmp_path, tables)
    assert ("error", "reviews.csv", 1, "reviewer_id") in _codes(report)


@pytest.mark.parametrize("positives", [0, 6])
def test_review_item_count_outside_1_to_5_is_an_error(tmp_path, positives):
    tables = base_tables()
    tables["review_items.csv"] = [{"review_id": "R1", "polarity": "negative", "item": "문서화"}] + [
        {"review_id": "R1", "polarity": "positive", "item": f"항목{i}"} for i in range(positives)]
    _, report = _load(tmp_path, tables)
    assert any(i.level == "error" and i.file == "review_items.csv" and "R1" in i.message for i in report.issues)


@pytest.mark.parametrize("patch", [
    {"schema_version": 2},
    {"horizon_start": "2026/10"},
    {"horizon_months": 12},
    {"synthetic": "yes"},
])
def test_manifest_problems_are_errors(tmp_path, patch):
    _, report = _load(tmp_path, manifest={**MANIFEST, **patch})
    assert any(i.level == "error" and i.file == "manifest.json" for i in report.issues)


def test_missing_manifest_key_is_an_error(tmp_path):
    m = dict(MANIFEST)
    del m["cost_unit"]
    _, report = _load(tmp_path, manifest=m)
    assert ("error", "manifest.json", None, "cost_unit") in _codes(report)


def test_unreadable_manifest_is_an_error(tmp_path):
    root = write_bundle(tmp_path / "b")
    (root / "manifest.json").write_text("{not json", encoding="utf-8")
    _, report = load_bundle(root)
    assert ("error", "manifest.json", None, None) in _codes(report)


def test_invalid_mapping_file_is_an_error(tmp_path):
    root = write_bundle(tmp_path / "b")
    (root / "mapping.json").write_text(json.dumps({"people.csv": ["not", "a", "dict"]}), encoding="utf-8")
    _, report = load_bundle(root)
    assert any(i.level == "error" and i.file == "mapping.json" for i in report.issues)


def test_duplicate_header_is_an_error(tmp_path):
    root = write_bundle(tmp_path / "b")
    (root / "availability.csv").write_text("person_id,month,available_mm,available_mm\nP1,2026-10,1.0,0.0\n",
                                           encoding="utf-8")
    _, report = load_bundle(root)
    assert any(i.level == "error" and i.file == "availability.csv" and "duplicate header" in i.message
               for i in report.issues)


@pytest.mark.parametrize("mapping", [
    {"people.csv": {"display_name": "person_id"}},           # points at another canonical column's header
    {"people.csv": {"display_name": "x", "job_family": "x"}},  # two columns, one header
])
def test_colliding_mappings_are_errors(tmp_path, mapping):
    _, report = _load(tmp_path, mapping=mapping)
    assert any(i.level == "error" and i.file == "people.csv" and "same header" in i.message for i in report.issues)


def test_unknown_mapping_file_or_column_is_an_error(tmp_path):
    _, report = _load(tmp_path, mapping={"peple.csv": {"person_id": "id"}, "people.csv": {"nmae": "이름"}})
    msgs = [i.message for i in report.issues if i.file == "mapping.json"]
    assert any("peple.csv" in m for m in msgs) and any("nmae" in m for m in msgs)


def test_non_utf8_csv_is_an_error_not_a_crash(tmp_path):
    root = write_bundle(tmp_path / "b")
    (root / "people.csv").write_bytes("person_id,display_name,career_grade,role_type\nP1,가상,고급,개발\n"
                                      .encode("cp949"))
    _, report = load_bundle(root)
    assert any(i.level == "error" and i.file == "people.csv" and "UTF-8" in i.message for i in report.issues)


@pytest.mark.parametrize("line", ["P1,2026-10,1.0,extra", "P1,2026-10"])
def test_rows_with_the_wrong_number_of_values_are_errors(tmp_path, line):
    root = write_bundle(tmp_path / "b")
    with (root / "availability.csv").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    _, report = load_bundle(root)
    assert any(i.level == "error" and i.file == "availability.csv" and "values but the header" in i.message
               for i in report.issues)


def test_a_blank_person_id_does_not_produce_misleading_follow_up_errors(tmp_path):
    tables = base_tables()
    tables["people.csv"][2]["person_id"] = ""
    _, report = _load(tmp_path, tables)
    assert ("error", "people.csv", 3, "person_id") in _codes(report)
    assert not any("None" in i.message for i in report.issues), report.summary()


@pytest.mark.parametrize("patch", [{"schema_version": True}, {"horizon_months": 6.0}, {"cost_unit": ""}])
def test_manifest_types_are_strict(tmp_path, patch):
    _, report = _load(tmp_path, manifest={**MANIFEST, **patch})
    assert any(i.level == "error" and i.file == "manifest.json" for i in report.issues)


@pytest.mark.parametrize("value", ["１.0", "1_0", "nan", "+0.5"])
def test_only_plain_ascii_numbers_are_accepted(tmp_path, value):
    tables = base_tables()
    tables["availability.csv"][0]["available_mm"] = value
    _, report = _load(tmp_path, tables)
    assert ("error", "availability.csv", 1, "available_mm") in _codes(report)


def test_json_files_with_a_bom_and_decomposed_hangul_are_read(tmp_path):
    import unicodedata
    tables = base_tables()
    tables["people.csv"][0]["career_grade"] = unicodedata.normalize("NFD", "고급")
    root = write_bundle(tmp_path / "b", tables)
    (root / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8-sig")
    bundle, report = load_bundle(root)
    assert report.ok, report.summary()
    assert bundle.tables["people.csv"][0]["career_grade"] == "고급"
