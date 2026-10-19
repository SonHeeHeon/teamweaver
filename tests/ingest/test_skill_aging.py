"""진행 중 사업 기술 경력 늘리기(core/ingest/aging, 2026-10-19 사용자 요청): 진행 중 업무에 등록된 기술만, 그 경력의 마지막 사용 달 뒤
계획 직전 달까지, manifest skill_months_owner가 "source"면 늘리지 않는다."""
import datetime as dt

from core.ingest.aging import age_ongoing_skills, data_as_of
from core.ingest.report import IngestReport

D = dt.date


def _tables(status="진행중", end=D(2026, 12, 31), links=True):
    return {
        "person_skills.csv": [
            {"person_id": "P1", "skill_name": "Spark", "experience_months": 9, "last_used_month": D(2026, 9, 1), "__row__": 1},
            {"person_id": "P1", "skill_name": "Java", "experience_months": 30, "last_used_month": D(2025, 3, 1), "__row__": 2}],
        "work_history.csv": [
            {"person_id": "P1", "work_id": "W1", "project_code": "J1", "start_date": D(2026, 1, 1), "end_date": end, "status": status}],
        "work_skills.csv": [{"work_id": "W1", "skill_name": "Spark"}, {"work_id": "W1", "skill_name": "Kafka"}] if links else [],
    }


def _horizon(start=D(2026, 12, 1)):
    return [start]


def test_ongoing_skills_grow_from_the_data_month_to_the_month_before_the_plan():
    """사용자 예: 9월 기준 Spark 9개월, 12월 계획이면 10·11월 두 달을 더해 11개월. 업무에 등록 안 된 Java는 그대로,
    원천이 한 번도 세지 않은 Kafka(경력 행 없음)는 업무 시작 1월부터 11월까지 11개월."""
    t, r = _tables(), IngestReport()
    rec = age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), r)
    rows = {x["skill_name"]: x for x in t["person_skills.csv"]}
    assert rows["Spark"]["experience_months"] == 11 and rows["Spark"]["last_used_month"] == D(2026, 11, 1)
    assert rows["Java"]["experience_months"] == 30
    assert rows["Kafka"]["experience_months"] == 11 and rows["Kafka"]["last_used_month"] == D(2026, 11, 1)
    assert rec == {"owner": "service", "as_of": "2026-09", "as_of_from": "manifest as_of", "cutoff": "2026-11",
                   "people": 1, "skills": 2, "max_months": 11, "skipped": None, "held": 0}
    assert r.notes and "최대 +11개월" in r.notes[0]


def test_static_source_numbers_catch_up_from_their_own_last_used_month():
    """원천이 정적(등록 때 숫자에서 멈춤): 데이터는 9월 기준이지만 Spark는 5월까지만 센 숫자 -- 6~11월 여섯 달을 더한다.
    원천이 이미 갱신했으면(마지막 사용 달 = 9월) 두 번 세지 않는다(첫 시험)."""
    t = _tables()
    t["person_skills.csv"][0].update(experience_months=5, last_used_month=D(2026, 5, 1))
    age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport())
    assert t["person_skills.csv"][0]["experience_months"] == 11


def test_an_overdue_ongoing_work_runs_until_the_plan():
    """계획된 끝(8월)이 데이터 기준월(9월)보다 앞인데 아직 진행중 = 연장 -- 계획 직전 달까지."""
    t = _tables(end=D(2026, 8, 31))
    age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport())
    assert t["person_skills.csv"][0]["experience_months"] == 11


def test_work_planned_to_end_in_the_data_month_ends_as_planned():
    """기준월(9월)에 끝나기로 한 업무는 연장이 아니다 -- 9월까지 센 경력이면 더할 달이 없다."""
    t = _tables(end=D(2026, 9, 30))
    t["work_skills.csv"] = t["work_skills.csv"][:1]
    assert age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport())["skills"] == 0


def test_nothing_grows_when_the_plan_starts_right_after_the_data_month():
    t = _tables()
    t["work_skills.csv"] = t["work_skills.csv"][:1]                # Spark만(9월까지 센 경력)
    assert age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(D(2026, 10, 1)), IngestReport())["skills"] == 0
    assert t["person_skills.csv"][0]["experience_months"] == 9


def test_source_owner_and_closed_or_unlinked_work_do_not_grow():
    t, r = _tables(), IngestReport()
    assert age_ongoing_skills({"as_of": "2026-09", "skill_months_owner": "source"}, t, _horizon(), r)["skills"] == 0
    assert t["person_skills.csv"][0]["experience_months"] == 9 and "원천 시스템이 갱신" in r.notes[0]
    t = _tables(status="확정완료")
    assert age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport())["skills"] == 0
    t, r = _tables(links=False), IngestReport()
    rec = age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), r)
    assert rec["skills"] == 0 and rec["skipped"] == "no_work_skills"
    assert any("업무별 사용 기술" in i.message for i in r.warnings)          # 9월 데이터로 12월 계획 -- 빠진 달이 있다
    t, r = _tables(links=False), IngestReport()
    age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(D(2026, 10, 1)), r)
    assert not r.warnings and any("업무별 사용 기술" in n for n in r.notes)    # 계획 직전 달까지 센 데이터 -- 알림만


def test_a_planned_end_before_the_plan_stops_the_growth_and_the_data_month_falls_back_to_last_used():
    t = _tables(end=D(2026, 10, 31))                            # 10월에 끝나는 계획 -- 10월 한 달만
    age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport())
    assert t["person_skills.csv"][0]["experience_months"] == 10
    assert data_as_of({}, _tables()) == (D(2026, 9, 1), "기술 경력의 가장 최근 사용 달")


def test_aliases_are_matched_through_the_dictionary_names():
    t = _tables()
    t["work_skills.csv"] = [{"work_id": "W1", "skill_name": "Apache Spark"}]
    age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport(),
                       canonical=lambda n: "Spark" if n in ("Spark", "Apache Spark") else n)
    assert [x["experience_months"] for x in t["person_skills.csv"]] == [11, 30]


def test_alias_rows_catch_up_from_their_own_months():
    """Codex 리뷰(2026-10-19): Spark 31개월·5월까지, Apache Spark 9개월·9월까지 센 두 행 -- 업무 1~12월, 12월 계획이면 각자 6·2달을 더해
    37·11개월(합칠 때 37). 가장 최근 달 하나를 같이 쓰면 33으로 36개월 경계를 못 넘는다."""
    t = _tables()
    t["person_skills.csv"] = [
        {"person_id": "P1", "skill_name": "Spark", "experience_months": 31, "last_used_month": D(2026, 5, 1), "__row__": 1},
        {"person_id": "P1", "skill_name": "Apache Spark", "experience_months": 9, "last_used_month": D(2026, 9, 1), "__row__": 2}]
    t["work_skills.csv"] = [{"work_id": "W1", "skill_name": "Spark"}]
    rec = age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport(),
                             canonical=lambda n: "Spark" if n in ("Spark", "Apache Spark") else n)
    assert [x["experience_months"] for x in t["person_skills.csv"]] == [37, 11]
    assert rec["skills"] == 1 and rec["max_months"] == 6


def test_unknown_counted_month_holds_the_row_and_warns():
    """Codex 종료 리뷰(2026-10-19): 마지막 사용 달도 데이터 기준월도 없으면 기존 9개월에 업무 11개월을 다 더해 20이 됐다 -- 늘리지 않고 경고.
    경력 행이 없는 기술(Kafka)은 이중 계산이 없으니 업무 시작부터 센다."""
    t, r = _tables(), IngestReport()
    t["person_skills.csv"] = [{"person_id": "P1", "skill_name": "Spark", "experience_months": 9, "last_used_month": None, "__row__": 1}]
    rec = age_ongoing_skills({}, t, _horizon(), r)
    rows = {x["skill_name"]: x for x in t["person_skills.csv"]}
    assert rows["Spark"]["experience_months"] == 9 and rows["Kafka"]["experience_months"] == 11
    assert rec["skills"] == 1 and rec["held"] == 1 and any("어디까지 셌는지" in i.message for i in r.warnings)
    # 늘린 것이 하나도 없으면 화면이 "최신"으로 읽지 않게 이유를 남긴다(폴백 리뷰)
    t, r = _tables(), IngestReport()
    t["person_skills.csv"] = [{"person_id": "P1", "skill_name": "Spark", "experience_months": 9, "last_used_month": None, "__row__": 1}]
    t["work_skills.csv"] = t["work_skills.csv"][:1]
    rec = age_ongoing_skills({}, t, _horizon(), r)
    assert rec["skills"] == 0 and rec["skipped"] == "unknown_counted_month" and rec["held"] == 1


def test_months_outside_the_ten_year_window_do_not_come_back():
    """Codex 종료 리뷰(2026-10-19): 2012년에 마지막으로 쓴 120개월 경력(입력 단계가 버림)에 최근 2개월을 더하며 되살리면 122개월·레벨 5였다 --
    최근에 쓴 달만 경력으로 남긴다."""
    t = _tables()
    t["work_history.csv"][0]["start_date"] = D(2026, 10, 1)
    t["person_skills.csv"][0].update(experience_months=120, last_used_month=D(2012, 6, 1))
    t["work_skills.csv"] = t["work_skills.csv"][:1]
    age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport())
    assert t["person_skills.csv"][0]["experience_months"] == 2 and t["person_skills.csv"][0]["last_used_month"] == D(2026, 11, 1)


def test_the_ten_year_window_boundary():
    """폴백 리뷰(2026-10-19): 12월 계획의 창 첫 달은 2016-12 -- 그 달에 마지막으로 쓴 경력은 창 안(120+2=122), 한 달 앞이면 창 밖(최근 2달만)."""
    for last, want in ((D(2016, 12, 1), 122), (D(2016, 11, 1), 2)):
        t = _tables()
        t["work_history.csv"][0]["start_date"] = D(2026, 10, 1)
        t["person_skills.csv"][0].update(experience_months=120, last_used_month=last)
        t["work_skills.csv"] = t["work_skills.csv"][:1]
        age_ongoing_skills({"as_of": "2026-09"}, t, _horizon(), IngestReport())
        assert t["person_skills.csv"][0]["experience_months"] == want, last


def test_loader_applies_the_aging_and_checks_the_manifest(tmp_path):
    """묶음 단위: 로더가 검증 뒤 늘리고(bundle.aging), as_of·skill_months_owner 형식이 틀리면 오류."""
    import json
    from core.ingest.loader import load_bundle
    from core.ingest.synthetic import generate_bundle
    root = generate_bundle(tmp_path / "b", n_people=6, n_projects=2, seed=3)
    m = json.loads((root / "manifest.json").read_text("utf-8"))
    for bad in ("2026-13", "0000-09", 202609):                 # 0000년은 정규식은 통과하지만 달력에 없다(예전엔 500)
        m["as_of"] = bad
        (root / "manifest.json").write_text(json.dumps(m), "utf-8")
        _, report = load_bundle(root)
        assert any(i.column == "as_of" for i in report.errors), bad
    m["as_of"], m["skill_months_owner"] = "2026-01", "nobody"
    (root / "manifest.json").write_text(json.dumps(m), "utf-8")
    _, report = load_bundle(root)
    assert any(i.column == "skill_months_owner" for i in report.errors)
