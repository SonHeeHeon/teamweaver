"""A small valid CSV bundle that tests mutate one problem at a time."""
import csv
import json
from pathlib import Path

from core.ingest.contract import FILE_SPECS

MANIFEST = {"dataset_id": "test-bundle", "schema_version": 1, "horizon_start": "2026-10",
            "horizon_months": 6, "cost_unit": "가상비용점", "synthetic": True}

MONTHS = ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]


def base_tables() -> dict[str, list[dict]]:
    people = [
        {"person_id": "P1", "display_name": "가상1", "career_grade": "고급", "role_type": "개발", "job_family": "개발"},
        {"person_id": "P2", "display_name": "가상2", "career_grade": "중급", "role_type": "컨설팅", "job_family": ""},
        {"person_id": "P3", "display_name": "가상3", "career_grade": "중급", "role_type": "개발", "job_family": ""},
    ]
    return {
        "people.csv": people,
        "rate_card.csv": [
            {"career_grade": g, "role_type": r, "monthly_rate": str(rate)}
            for g, base in (("특급", 1600), ("고급", 1300), ("중급", 1000), ("초급", 750))
            for r, rate in (("개발", base), ("컨설팅", base + 100))],
        "person_skills.csv": [
            {"person_id": "P1", "skill_name": "Java", "skill_category": "Programming Language",
             "project_count": "4", "experience_months": "44", "last_used_month": "2026-08"},
            {"person_id": "P1", "skill_name": "SQL", "skill_category": "DBMS",
             "project_count": "2", "experience_months": "20", "last_used_month": ""},
            {"person_id": "P2", "skill_name": "Java", "skill_category": "Programming Language",
             "project_count": "1", "experience_months": "8", "last_used_month": "2026-05"},
            {"person_id": "P3", "skill_name": "SQL", "skill_category": "DBMS",
             "project_count": "3", "experience_months": "70", "last_used_month": "2026-09"},
        ],
        "work_history.csv": [
            {"person_id": "P1", "work_id": "W1", "project_code": "X-1", "start_date": "2025-01-01",
             "end_date": "2025-06-30", "status": "확정완료"},
            {"person_id": "P2", "work_id": "W2", "project_code": "X-1", "start_date": "2025-04-01",
             "end_date": "2025-09-30", "status": "확정완료"},
            {"person_id": "P3", "work_id": "W3", "project_code": "X-2", "start_date": "2025-03-01",
             "end_date": "2025-12-31", "status": "진행중"},
        ],
        "availability.csv": [
            {"person_id": p["person_id"], "month": m, "available_mm": "1.0"}
            for p in people for m in MONTHS],
        "projects.csv": [
            {"project_id": "J1", "project_name": "가상 프로젝트", "sector": "대외금융", "phase": "실행",
             "start_month": "2026-10", "end_month": "2026-12", "monthly_budget": "3000"},
        ],
        "project_grade_requirements.csv": [
            {"project_id": "J1", "career_grade": "고급", "headcount": "1"},
            {"project_id": "J1", "career_grade": "중급", "headcount": "1"},
        ],
        "project_skill_requirements.csv": [
            {"project_id": "J1", "skill_name": "Java", "min_experience_months": "36", "headcount": "1"},
        ],
        "reviews.csv": [
            {"review_id": "R1", "review_round": "2025H2", "project_code": "X-1", "reviewer_id": "P2",
             "reviewee_id": "P1", "reviewed_at": "2025-12-15", "positive_text": "설계가 꼼꼼했다",
             "negative_text": "문서가 늦었다"},
        ],
        "review_items.csv": [
            {"review_id": "R1", "polarity": "positive", "item": "전문성"},
            {"review_id": "R1", "polarity": "negative", "item": "문서화"},
        ],
    }


def write_bundle(root: Path, tables: dict[str, list[dict]] | None = None, manifest: dict | None = None,
                 mapping: dict | None = None, encoding: str = "utf-8") -> Path:
    tables = base_tables() if tables is None else tables
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(MANIFEST if manifest is None else manifest,
                                                   ensure_ascii=False), encoding="utf-8")
    if mapping is not None:
        (root / "mapping.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    for name, rows in tables.items():
        headers = list(rows[0].keys()) if rows else [c.name for c in FILE_SPECS[name].columns]
        with (root / name).open("w", encoding=encoding, newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=headers)
            w.writeheader()
            w.writerows(rows)
    return root
