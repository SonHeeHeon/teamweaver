"""CSV bundle contract v1: which files, which columns, and how each value is parsed.

A bundle is one folder: manifest.json + the CSV files below (+ optional mapping.json that maps
the real export headers onto these canonical column names). Values are parsed strictly; nothing
missing is ever filled in silently.
"""
from dataclasses import dataclass

SCHEMA_VERSION = 1
HORIZON_MONTHS = 6

GRADES = ("특급", "고급", "중급", "초급")
ROLE_TYPES = ("개발", "컨설팅")
SECTORS = ("대내", "대외금융", "대외공공")
PHASES = ("실행", "제안")
WORK_STATUSES = ("미등록", "진행중", "확정완료")
YES_NO = ("Y", "N")
POLARITIES = ("positive", "negative")


@dataclass(frozen=True)
class Column:
    name: str
    kind: str                       # str | int | float | date | month | enum
    required: bool = True
    choices: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    ref: str | None = None          # "file.csv:column" that must contain this value


@dataclass(frozen=True)
class FileSpec:
    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]            # columns that must be unique together
    optional: bool = False          # a missing optional file is an empty table, not an error


FILES: tuple[FileSpec, ...] = (
    FileSpec("people.csv", (
        Column("person_id", "str"),
        Column("display_name", "str"),
        Column("career_grade", "enum", choices=GRADES),
        Column("role_type", "enum", choices=ROLE_TYPES),
        Column("job_family", "str", required=False),
    ), key=("person_id",)),
    FileSpec("rate_card.csv", (
        Column("career_grade", "enum", choices=GRADES),
        Column("role_type", "enum", choices=ROLE_TYPES),
        Column("monthly_rate", "int", min=1),
    ), key=("career_grade", "role_type")),
    FileSpec("person_skills.csv", (
        Column("person_id", "str", ref="people.csv:person_id"),
        Column("skill_name", "str"),
        Column("skill_category", "str", required=False),
        Column("project_count", "int", required=False, min=0),
        Column("experience_months", "int", min=0),
        Column("last_used_month", "month", required=False),
    ), key=("person_id", "skill_name")),
    FileSpec("work_history.csv", (
        Column("person_id", "str", ref="people.csv:person_id"),
        Column("work_id", "str"),
        Column("project_code", "str"),
        Column("start_date", "date"),
        Column("end_date", "date"),
        Column("status", "enum", choices=WORK_STATUSES),
        # optional context the real 업무이력 also carries (업무명·원청사·업종·업무개요); not used by the model yet
        Column("work_name", "str", required=False),
        Column("client", "str", required=False),
        Column("industry", "str", required=False),
        Column("summary", "str", required=False),
    ), key=("work_id",)),
    FileSpec("availability.csv", (
        Column("person_id", "str", ref="people.csv:person_id"),
        Column("month", "month"),
        Column("available_mm", "float", min=0.0, max=1.0),
    ), key=("person_id", "month")),
    FileSpec("projects.csv", (
        Column("project_id", "str"),
        Column("project_name", "str"),
        Column("sector", "enum", choices=SECTORS),
        Column("phase", "enum", choices=PHASES),
        Column("start_month", "month"),
        Column("end_month", "month"),
        Column("monthly_budget", "int", min=1),
    ), key=("project_id",)),
    FileSpec("project_grade_requirements.csv", (
        Column("project_id", "str", ref="projects.csv:project_id"),
        Column("career_grade", "enum", choices=GRADES),
        Column("headcount", "int", min=0),
    ), key=("project_id", "career_grade")),
    FileSpec("project_skill_requirements.csv", (
        Column("project_id", "str", ref="projects.csv:project_id"),
        Column("skill_name", "str"),
        Column("min_experience_months", "int", min=0),
        Column("headcount", "int", min=1),
    ), key=("project_id", "skill_name")),
    FileSpec("reviews.csv", (
        Column("review_id", "str"),
        Column("review_round", "str"),
        Column("project_code", "str", required=False),
        Column("reviewer_id", "str", ref="people.csv:person_id"),
        Column("reviewee_id", "str", ref="people.csv:person_id"),
        Column("reviewed_at", "date"),
        Column("positive_text", "str"),
        Column("negative_text", "str"),
    ), key=("review_id",)),
    FileSpec("review_items.csv", (
        Column("review_id", "str", ref="reviews.csv:review_id"),
        Column("polarity", "enum", choices=POLARITIES),
        Column("item", "str"),
    ), key=("review_id", "polarity", "item")),
    # Who is on which planned project right now (the staffing system's current roster). Optional: without it
    # every seat is planned from scratch, as before. locked=Y must be kept (e.g. a customer-named person).
    FileSpec("current_assignments.csv", (
        Column("person_id", "str", ref="people.csv:person_id"),
        Column("project_id", "str", ref="projects.csv:project_id"),
        Column("alloc", "float", min=0.0, max=1.0),
        Column("locked", "enum", choices=YES_NO),
    ), key=("person_id", "project_id"), optional=True),
    # Past project outcomes (the company records customer evaluations, customer-requested replacements and
    # follow-on projects -- schema answers). Optional; the model does not read them, the model lab calibrates on them.
    FileSpec("project_outcomes.csv", (
        Column("project_code", "str"),
        Column("client", "str"),
        Column("industry", "str", required=False),
        Column("closed_month", "month"),
        Column("customer_score", "int", min=1, max=5),
        Column("schedule", "enum", choices=("준수", "지연")),
        Column("follow_on", "enum", choices=YES_NO),
    ), key=("project_code",), optional=True),
    FileSpec("replacements.csv", (
        Column("project_code", "str"),
        # no ref: real records include people who have left (reason 이직); unknown ids are a warning (loader)
        Column("person_id", "str"),
        Column("requested_by", "enum", choices=("고객", "내부")),
        Column("reason", "str"),
        Column("replaced_at", "date"),
    ), key=("project_code", "person_id"), optional=True),
)

FILE_SPECS = {f.name: f for f in FILES}

MANIFEST_KEYS = ("dataset_id", "schema_version", "horizon_start", "horizon_months", "cost_unit", "synthetic")
