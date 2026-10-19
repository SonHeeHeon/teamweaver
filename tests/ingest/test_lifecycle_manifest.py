"""manifest `lifecycle`(사업 수명 주기, 2026-10-19 생성기 v4) 검사 -- 계산에 안 쓰는 기록이라 경고만."""
from core.ingest import lifecycle
from core.ingest.report import IngestReport

ROWS = [{"project_id": "J1", "start_month": "2024-03", "end_month": "2027-03"},
        {"project_id": "J2", "start_month": "2026-11", "end_month": "2027-03"}]


def _warn(entries):
    r = IngestReport()
    lifecycle.check({"lifecycle": entries}, ROWS, r)
    return [i.message for i in r.issues if i.level == "warning"]


def test_valid_long_running_and_add_on_pass():
    assert _warn({"J1": {"stage": "운영", "started": "2024-03"},
                  "J2": {"stage": "추가 과제", "started": "2026-11", "parent": "J1", "external_headcount": 4}}) == []
    r = IngestReport()
    lifecycle.check({}, ROWS, r)
    assert r.issues == []


def test_bad_entries_warn_but_never_error():
    msgs = _warn({"J9": {"stage": "운영", "started": "2024-03"},                    # 없는 사업
                  "J1": {"stage": "유지보수", "started": "2024-04"},                  # 단계·시작 달 어긋남
                  "J2": {"stage": "추가 과제", "started": "2026-11", "parent": "J7", "external_headcount": -1}})
    assert any("없는 사업" in m for m in msgs) and any("stage" in m for m in msgs)
    assert any("시작 달과 다르다" in m for m in msgs) and any("external_headcount" in m for m in msgs)
    assert any("상위 사업" in m for m in msgs)
    r = IngestReport()
    lifecycle.check({"lifecycle": ["J1"]}, ROWS, r)
    assert [i.level for i in r.issues] == ["warning"]


def test_add_on_outside_the_parent_period_warns():
    rows = [{"project_id": "J1", "start_month": "2024-03", "end_month": "2026-12"}, ROWS[1]]
    r = IngestReport()
    lifecycle.check({"lifecycle": {"J2": {"stage": "추가 과제", "started": "2026-11", "parent": "J1"}}}, rows, r)
    assert any("기간 밖" in i.message for i in r.issues)
