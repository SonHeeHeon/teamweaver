import pytest

from core.domain.models import Grade
from core.graph.memory_graph import MemoryGraph
from core.ingest.convert import level_from_months, to_dataset
from core.ingest.loader import load_bundle
from core.optimize.milp import MilpParams, solve_milp
from core.scoring.engine import ScoringEngine
from tests.ingest.bundle_factory import base_tables, write_bundle


def _convert(tmp_path, tables=None):
    bundle, report = load_bundle(write_bundle(tmp_path / "b", tables))
    assert report.ok, report.summary()
    return (*to_dataset(bundle, report), report)


@pytest.mark.parametrize("months,level", [(0, 1), (11, 1), (12, 2), (35, 2), (36, 3), (59, 3),
                                          (60, 4), (95, 4), (96, 5), (400, 5)])
def test_experience_months_map_to_proxy_levels(months, level):
    assert level_from_months(months) == level


def test_people_projects_and_requirements(tmp_path):
    ds, parsed, report = _convert(tmp_path)
    p = {x.id: x for x in ds.people}
    assert p["P1"].skills == {"Java": 3, "SQL": 2}
    assert p["P2"].skills == {"Java": 1} and p["P3"].skills == {"SQL": 4}
    assert p["P1"].grade == Grade.SENIOR and p["P1"].monthly_rate == 1300
    assert p["P2"].monthly_rate == 1100                      # 중급 + 컨설팅 rate
    assert p["P1"].availability == [1.0] * 6
    j = ds.projects[0]
    assert (j.start_month, j.end_month) == (0, 2)
    assert j.grade_headcount == {Grade.SENIOR: 1, Grade.MID: 1}
    assert [(r.skill, r.min_level, r.headcount) for r in j.requirements] == [("Java", 3, 1)]
    assert any("경력 개월" in n for n in report.notes)


def test_cowork_months_come_from_overlapping_work_on_the_same_project(tmp_path):
    ds, _, _ = _convert(tmp_path)
    pairs = {(c.a_id, c.b_id): (c.co_months, c.project_count) for c in ds.coworks}
    assert pairs == {("P1", "P2"): (3, 1)}                  # X-1: Apr–Jun 2025 overlap, P3 alone on X-2


def test_same_month_on_two_shared_projects_counts_once(tmp_path):
    tables = base_tables()
    tables["work_history.csv"] += [
        {"person_id": "P1", "work_id": "W4", "project_code": "X-9", "start_date": "2025-05-01",
         "end_date": "2025-05-31", "status": "확정완료"},
        {"person_id": "P2", "work_id": "W5", "project_code": "X-9", "start_date": "2025-05-10",
         "end_date": "2025-07-31", "status": "미등록"},
    ]
    ds, _, _ = _convert(tmp_path, tables)
    c = next(c for c in ds.coworks if {c.a_id, c.b_id} == {"P1", "P2"})
    assert (c.co_months, c.project_count) == (3, 2)


def test_every_review_round_is_kept_oldest_first(tmp_path):
    tables = base_tables()
    tables["reviews.csv"].append({"review_id": "R2", "review_round": "2026H1", "project_code": "X-1",
                                  "reviewer_id": "P2", "reviewee_id": "P1", "reviewed_at": "2026-06-20",
                                  "positive_text": "최근 평가", "negative_text": "최근 단점"})
    tables["review_items.csv"] += [{"review_id": "R2", "polarity": "positive", "item": "리더십"},
                                   {"review_id": "R2", "polarity": "positive", "item": "소통"},
                                   {"review_id": "R2", "polarity": "negative", "item": "일정관리"}]
    ds, parsed, report = _convert(tmp_path, tables)
    assert [r.positive.text for r in ds.reviews] == ["설계가 꼼꼼했다", "최근 평가"]
    assert ds.reviews[1].positive.items == ["리더십", "소통"]
    assert [p.text_polarity for p in parsed] == pytest.approx([0.0, (2 - 1) / 3])
    assert any("2건" in n and "1쌍은 여러 회차" in n for n in report.notes)
    graph = MemoryGraph.build(ds, parsed)
    key = tuple(sorted((graph.pid_index["P1"], graph.pid_index["P2"])))
    assert graph.pair_review_score[key] == pytest.approx((0.5 * 0 + 0.5 * 0 + 0.5 * (1 / 3) + 0.5 * (1 / 3)) / 2)


def test_conversion_refuses_a_bundle_with_errors(tmp_path):
    tables = base_tables()
    tables["people.csv"][0]["career_grade"] = "부장"
    bundle, report = load_bundle(write_bundle(tmp_path / "b", tables))
    with pytest.raises(ValueError):
        to_dataset(bundle, report)


def test_project_outside_the_horizon_is_skipped_and_partial_overlap_is_clipped(tmp_path):
    tables = base_tables()
    tables["projects.csv"].append({"project_id": "J2", "project_name": "지난 사업", "sector": "대내", "phase": "실행",
                                   "start_month": "2025-01", "end_month": "2025-06", "monthly_budget": "1000"})
    tables["project_grade_requirements.csv"].append({"project_id": "J2", "career_grade": "중급", "headcount": "1"})
    tables["project_skill_requirements.csv"].append({"project_id": "J2", "skill_name": "SQL",
                                                     "min_experience_months": "0", "headcount": "1"})
    ds, _, report = _convert(tmp_path, tables)
    assert [j.id for j in ds.projects] == ["J1"]
    assert any(i.file == "projects.csv" and i.level == "warning" and "J2" in i.message for i in report.issues)
    assert not any("SQL" in i.message and "가진 사람이 없다" in i.message for i in report.issues)

    tables["projects.csv"][1].update(start_month="2027-02", end_month="2027-08")
    ds, _, report = _convert(tmp_path / "again", tables)
    j2 = next(j for j in ds.projects if j.id == "J2")
    assert (j2.start_month, j2.end_month) == (4, 5)
    assert any(i.level == "warning" and "J2" in i.message for i in report.issues)


def test_project_without_skill_requirements_is_an_error(tmp_path):
    tables = base_tables()
    tables["project_skill_requirements.csv"] = []
    bundle, report = load_bundle(write_bundle(tmp_path / "b", tables))
    with pytest.raises(ValueError):
        to_dataset(bundle, report)
    assert any("J1" in i.message and "기술 요구" in i.message for i in report.errors)


def test_missing_rate_card_entry_is_an_error(tmp_path):
    tables = base_tables()
    tables["rate_card.csv"] = [r for r in tables["rate_card.csv"]
                               if not (r["career_grade"] == "중급" and r["role_type"] == "컨설팅")]
    bundle, report = load_bundle(write_bundle(tmp_path / "b", tables))
    with pytest.raises(ValueError):
        to_dataset(bundle, report)
    assert any("P2" in i.message and "단가" in i.message for i in report.errors)


def test_zero_month_skill_is_dropped_with_a_warning(tmp_path):
    tables = base_tables()
    tables["person_skills.csv"].append({"person_id": "P2", "skill_name": "Python", "skill_category": "",
                                        "project_count": "0", "experience_months": "0", "last_used_month": ""})
    ds, _, report = _convert(tmp_path, tables)
    assert "Python" not in next(p for p in ds.people if p.id == "P2").skills
    assert any(i.level == "warning" and "Python" in i.message for i in report.issues)


def test_converted_dataset_runs_through_scoring_and_milp(tmp_path):
    ds, parsed, _ = _convert(tmp_path)
    graph = MemoryGraph.build(ds, parsed)
    eng = ScoringEngine(graph)
    plan = solve_milp(graph, eng.skill_matrix({}), eng.synergy_matrix(), MilpParams(time_limit=30))
    assert plan.entries and plan.unfilled == []


def test_converter_itself_never_fills_a_missing_month(tmp_path):
    from core.ingest.report import IngestReport
    bundle, report = load_bundle(write_bundle(tmp_path / "b"))
    assert report.ok
    bundle.tables["availability.csv"] = [a for a in bundle.tables["availability.csv"]
                                         if not (a["person_id"] == "P3" and a["month"] == bundle.horizon[2])]
    fresh = IngestReport()
    with pytest.raises(ValueError):
        to_dataset(bundle, fresh)
    assert any("P3" in i.message and "2026-12" in i.message for i in fresh.errors)


def test_explicit_zero_headcount_is_kept_so_the_grade_is_forbidden(tmp_path):
    tables = base_tables()
    tables["project_grade_requirements.csv"].append({"project_id": "J1", "career_grade": "특급", "headcount": "0"})
    ds, _, _ = _convert(tmp_path, tables)
    assert ds.projects[0].grade_headcount[Grade.SPECIAL] == 0


def test_open_ended_or_future_assignments_are_cut_at_the_horizon_start(tmp_path):
    tables = base_tables()
    tables["work_history.csv"] = [
        {"person_id": "P1", "work_id": "W1", "project_code": "X-1", "start_date": "2026-07-01",
         "end_date": "9999-12-31", "status": "진행중"},
        {"person_id": "P2", "work_id": "W2", "project_code": "X-1", "start_date": "2026-08-15",
         "end_date": "2027-02-28", "status": "미등록"},
    ]
    ds, _, report = _convert(tmp_path, tables)
    assert [(c.a_id, c.b_id, c.co_months) for c in ds.coworks] == [("P1", "P2", 2)]   # Aug–Sep 2026 only
    assert any("2026-09-30" in n for n in report.notes)


def test_adjacent_assignments_in_the_same_month_are_not_coworking(tmp_path):
    tables = base_tables()
    tables["work_history.csv"] = [
        {"person_id": "P1", "work_id": "W1", "project_code": "X-1", "start_date": "2025-01-01",
         "end_date": "2025-03-01", "status": "확정완료"},
        {"person_id": "P2", "work_id": "W2", "project_code": "X-1", "start_date": "2025-03-31",
         "end_date": "2025-05-31", "status": "확정완료"},
    ]
    ds, _, _ = _convert(tmp_path, tables)
    assert ds.coworks == []


def test_several_rows_for_one_person_on_a_code_and_year_crossing(tmp_path):
    tables = base_tables()
    tables["work_history.csv"] = [
        {"person_id": "P1", "work_id": "W1", "project_code": "X-1", "start_date": "2024-11-01",
         "end_date": "2024-12-31", "status": "확정완료"},
        {"person_id": "P1", "work_id": "W1b", "project_code": "X-1", "start_date": "2025-02-01",
         "end_date": "2025-02-28", "status": "확정완료"},
        {"person_id": "P2", "work_id": "W2", "project_code": "X-1", "start_date": "2024-12-15",
         "end_date": "2025-02-10", "status": "확정완료"},
    ]
    ds, _, _ = _convert(tmp_path, tables)
    assert [(c.co_months, c.project_count) for c in ds.coworks] == [(2, 1)]          # Dec 2024 + Feb 2025


def test_two_reviews_of_one_pair_on_the_same_date_are_both_kept(tmp_path):
    tables = base_tables()
    tables["reviews.csv"].append({**tables["reviews.csv"][0], "review_id": "R2", "review_round": "9"})
    tables["review_items.csv"] += [{"review_id": "R2", "polarity": "positive", "item": "소통"},
                                   {"review_id": "R2", "polarity": "negative", "item": "일정관리"}]
    ds, _, _ = _convert(tmp_path, tables)
    assert len(ds.reviews) == 2


def test_requirement_months_on_a_band_boundary(tmp_path):
    tables = base_tables()
    tables["project_skill_requirements.csv"][0]["min_experience_months"] = "12"
    ds, _, _ = _convert(tmp_path, tables)
    assert ds.projects[0].requirements[0].min_level == 2


def test_review_order_does_not_depend_on_row_order(tmp_path):
    texts = []
    for order in ("old-first", "new-first"):
        tables = base_tables()
        r1 = tables["reviews.csv"][0]
        new3 = {**r1, "review_id": "R3", "reviewed_at": "2026-06-01", "positive_text": "최신"}
        tables["reviews.csv"] = [r1, new3] if order == "old-first" else [new3, r1]
        tables["review_items.csv"] += [{"review_id": "R3", "polarity": "positive", "item": "소통"},
                                       {"review_id": "R3", "polarity": "negative", "item": "일정관리"}]
        ds, _, _ = _convert(tmp_path / order, tables)
        texts.append([r.positive.text for r in ds.reviews])
    assert texts[0] == texts[1] == ["설계가 꼼꼼했다", "최신"]


def test_no_project_inside_the_horizon_is_an_error(tmp_path):
    tables = base_tables()
    tables["projects.csv"][0].update(start_month="2025-01", end_month="2025-03")
    bundle, report = load_bundle(write_bundle(tmp_path / "b", tables))
    with pytest.raises(ValueError):
        to_dataset(bundle, report)
    assert any("하나도 없다" in i.message for i in report.errors)


def test_multi_round_datasets_are_refused_by_paths_without_a_round_id(tmp_path):
    import sqlite3
    from core.datagen.llm_checkpoint import apply_checkpoint, generate_and_parse_checkpointed
    from core.graph.rehydrate import from_sqlite
    from core.graph.sqlite_store import build_sqlite
    tables = base_tables()
    tables["reviews.csv"].append({**tables["reviews.csv"][0], "review_id": "R2", "reviewed_at": "2026-06-20"})
    tables["review_items.csv"] += [{"review_id": "R2", "polarity": "positive", "item": "소통"},
                                   {"review_id": "R2", "polarity": "negative", "item": "일정관리"}]
    ds, parsed, _ = _convert(tmp_path, tables)
    build_sqlite(ds, parsed, tmp_path / "g.db")
    conn = sqlite3.connect(tmp_path / "g.db")
    with pytest.raises(ValueError, match="several rounds"):
        from_sqlite(conn)
    with pytest.raises(ValueError, match="one review per direction"):
        apply_checkpoint(ds, {})
    with pytest.raises(ValueError, match="one review per direction"):
        generate_and_parse_checkpointed(ds, None, "g", "p", 0, tmp_path / "ck.json")
