"""원천 표 편집(api/workspace_edits): 신규 사업 등록·단계·떨어짐·고정(사업·사람)·확정 -- 고친 묶음이 업로드 검증을 통과한다."""
from pathlib import Path

import pytest

from api import workspace_edits as we

YEAREND = Path(__file__).resolve().parents[2] / "demo" / "org-n100-yearend"


@pytest.fixture
def files():
    return {p.name: p.read_text("utf-8") for p in YEAREND.iterdir() if p.is_file()}


def _form(**kw):
    base = dict(name="보험사 B 데이터 플랫폼 고도화", sector="대외금융", start_month="2027-01", end_month="2028-12",
                monthly_budget=6000, stage="예정", predecessor="J003", grades={"고급": 1, "중급": 2},
                skills=[{"name": "Python", "min_months": 24, "headcount": 2}, {"name": "없는기술XYZ", "headcount": 1}])
    base.update(kw)
    return we.NewProject(**base)


def _valid(files) -> None:
    from api.routes.datasets import validate_and_build
    from api.workspace import zip_files
    active, report, err = validate_and_build(zip_files(files))
    assert err is None and active is not None, (err, report and report.get("errors"))
    active.retire()


def test_add_project_writes_rows_and_pipeline_and_passes_ingest(files):
    new, pid = we.add_project(files, _form(), registered_at="2026-10-18")
    assert pid == "J016"
    _, rows = we.read_table(new, we.PROJECTS)
    row = next(r for r in rows if r["project_id"] == "J016")
    assert row["phase"] == "제안" and row["end_month"] == "2028-12"             # 실제 종료 달 그대로(계산은 창 안으로 자름)
    m = we.read_manifest(new)
    assert "J016" in m["proposals"] and m["pipeline"]["J016"]["predecessor"] == "J003"
    assert files != new and "J016" not in files[we.PROJECTS]                   # 원본은 그대로
    _valid(new)


def test_form_validation():
    with pytest.raises(ValueError):
        _form(end_month="2026-01")
    with pytest.raises(ValueError):
        _form(grades={"고급": 0})
    with pytest.raises(ValueError):
        _form(skills=[{"name": "Java"}, {"name": "java"}])
    with pytest.raises(ValueError):
        _form(start_month="2027-13")


def test_unknown_predecessor_is_refused(files):
    with pytest.raises(we.EditError):
        we.add_project(files, _form(predecessor="J999"))


def test_stage_change_and_copy_form(files):
    new, pid = we.add_project(files, _form())
    won = we.update_project(new, pid, stage="수주")
    _, rows = we.read_table(won, we.PROJECTS)
    assert next(r for r in rows if r["project_id"] == pid)["phase"] == "실행"
    assert we.read_manifest(won)["pipeline"][pid]["stage"] == "수주"
    form = we.project_form(files, "J003")                                     # 이전 사업 복사
    assert form.grades and form.skills
    with pytest.raises(we.EditError):
        we.update_project(files, "J001", stage="수주")                        # 진행 사업은 파이프라인이 아니다


def test_remove_project_frees_its_team_to_bench(files):
    _, cur = we.read_table(files, we.CURRENT)
    team = sorted({r["person_id"] for r in cur if r["project_id"] == "J014"})
    new, freed = we.remove_project(files, "J014")
    others = {r["person_id"] for r in cur if r["project_id"] != "J014"}
    assert freed == sorted(p for p in team if p not in others)
    assert set(freed) <= set(we.read_manifest(new)["bench"])
    with pytest.raises(we.EditError):
        we.remove_project(files, "J001")
    _valid(new)


def test_locks_whole_project_or_people(files):
    new = we.set_lock(files, "J001", None, True)
    _, cur = we.read_table(new, we.CURRENT)
    assert all(r["locked"] == "Y" for r in cur if r["project_id"] == "J001")
    _, orig = we.read_table(files, we.CURRENT)
    already = {r["person_id"] for r in orig if r["project_id"] == "J002" and r["locked"] == "Y"}   # 원본에도 고정이 있다
    one = sorted(r["person_id"] for r in cur if r["project_id"] == "J002" and r["person_id"] not in already)[0]
    new2 = we.set_lock(files, "J002", [one], True)
    _, cur2 = we.read_table(new2, we.CURRENT)
    assert {r["person_id"] for r in cur2 if r["project_id"] == "J002" and r["locked"] == "Y"} == already | {one}
    with pytest.raises(we.EditError):
        we.set_lock(files, "J002", ["DP0999"], True)
    _valid(new2)


def test_apply_keeps_locks_refuses_dropping_them_and_writes_increases(files):
    locked = we.set_lock(files, "J001", None, True)
    _, cur = we.read_table(locked, we.CURRENT)
    entries = [{"person_id": r["person_id"], "project_id": r["project_id"], "alloc": float(r["alloc"])} for r in cur]
    with pytest.raises(we.EditError):
        we.apply_entries(locked, [e for e in entries if e["project_id"] != "J001"])
    with pytest.raises(we.EditError):
        we.apply_entries(locked, entries + [entries[0]])
    bench = we.read_manifest(files)["bench"][0]
    new, changes = we.apply_entries(locked, entries + [{"person_id": bench, "project_id": "J007", "alloc": 1.0}],
                                    headcount_add={"J007": {"중급": 1}}, budget_add={"J007": 500},
                                    skills_add={"J007": [{"name": "Kubernetes", "min_months": 12, "headcount": 1}]})
    assert changes["added"] == [f"{bench}@J007"] and not changes["removed"]
    _, cur2 = we.read_table(new, we.CURRENT)
    assert all(r["locked"] == "Y" for r in cur2 if r["project_id"] == "J001")
    assert bench not in we.read_manifest(new)["bench"]
    _, skills = we.read_table(new, we.SKILL_REQS)
    assert any(r["project_id"] == "J007" and r["skill_name"] == "Kubernetes" for r in skills)
    _valid(new)


def test_explicit_zero_headcount_rows_are_kept(files):
    """적힌 0명은 '그 등급 금지' -- 행이 사라지면 정원 제한 없음으로 바뀐다(Codex 리뷰 2026-10-19)."""
    new, pid = we.add_project(files, _form(grades={"고급": 1, "초급": 0}))
    _, grows = we.read_table(new, we.GRADE_REQS)
    assert {(r["career_grade"], r["headcount"]) for r in grows if r["project_id"] == pid} == {("고급", "1"), ("초급", "0")}
    staged = we.update_project(new, pid, stage="제안")
    _, grows2 = we.read_table(staged, we.GRADE_REQS)
    assert ("초급", "0") in {(r["career_grade"], r["headcount"]) for r in grows2 if r["project_id"] == pid}


def test_spaced_lock_flags_still_protect(files):
    """입력 검증은 " Y "를 고정으로 읽는다 -- 편집도 같게(Codex 리뷰 2026-10-19)."""
    h, cur = we.read_table(files, we.CURRENT)
    target = next(r for r in cur if r["project_id"] == "J001")
    target["locked"] = " Y "
    we.write_table(files, we.CURRENT, h, cur)
    entries = [{"person_id": r["person_id"], "project_id": r["project_id"], "alloc": float(r["alloc"])} for r in cur
               if r is not target]
    with pytest.raises(we.EditError):
        we.apply_entries(files, entries)


def test_first_apply_on_a_bundle_without_current_assignments(files):
    """현재 배치 파일은 선택이다 -- 없는 백지 묶음도 첫 확정이 된다(Codex 리뷰 2026-10-19)."""
    files = {k: v for k, v in files.items() if k != we.CURRENT}
    _, people = we.read_table(files, "people.csv")
    new, changes = we.apply_entries(files, [{"person_id": people[0]["person_id"], "project_id": "J001", "alloc": 1.0}])
    assert changes["added"] == [f"{people[0]['person_id']}@J001"]
    assert we.read_table(new, we.CURRENT)[0] == we.CURRENT_HEADER
