"""진행 사업에 추가 과제 붙이기(2026-10-19, 사용자 예: "26년엔 운영에 클라우드 마이그레이션 … 추가 과제가 있어 6달만 회사 인력은
2명만 추가(외주는 여러명)"). 연말형 시연 묶음으로 서버를 띄워 미리 채우기 → 등록 → 후보(상위 사업 팀원) → 단계 바꾸기 → 떨어짐."""
from fastapi.testclient import TestClient


def _ws(c):
    return c.get("/api/workspace").json()


def test_an_addon_form_is_prefilled_from_the_running_project(yearend):
    from api.main import app
    with TestClient(app) as c:
        f = c.get("/api/workspace/projects/J001/form", params={"as": "addon", "start": "2026-11"}).json()
        skills = c.get("/api/workspace/projects/J001/form").json()["skills"]
    assert f["parent"] == "J001" and f["predecessor"] is None and f["name"].endswith("추가 과제")
    assert (f["start_month"], f["end_month"]) == ("2026-11", "2027-03")          # 6개월이지만 상위 사업(~2027-03) 안으로
    assert f["grades"] == {"특급": 0, "고급": 1, "중급": 1, "초급": 0} and f["monthly_budget"] > 0
    assert [s["name"] for s in f["skills"]] == [s["name"] for s in skills]


def test_register_an_addon_and_staff_it_from_the_parent_team(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _ws(c)["dataset_version"]
        form = {**c.get("/api/workspace/projects/J001/form", params={"as": "addon", "start": "2026-11"}).json(),
                "name": "J001 클라우드 마이그레이션", "external_headcount": 6}
        r = c.post("/api/workspace/projects", json={"dataset_version": v, "project": form})
        assert r.status_code == 200, r.text
        pid, ws = r.json()["project_id"], r.json()["workspace"]
        item = next(p for p in ws["pipeline"] if p["project_id"] == pid)
        assert item["parent"] == "J001" and item["external_headcount"] == 6 and item["predecessor"] is None
        assert next(o for o in ws["ongoing"] if o["project_id"] == "J001")["addons"] == [pid]
        assert c.get(f"/api/workspace/projects/{pid}/form").json()["parent"] == "J001"
        team = {t["person_id"] for t in next(o for o in ws["ongoing"] if o["project_id"] == "J001")["team"]}
        cands = c.get(f"/api/pipeline/{pid}/candidates", params={"dataset_version": ws["dataset_version"]}).json()
        assert cands["parent"] == "J001"
        rows = [r for g in ("bench", "ending", "pull") for r in cands[g]]
        assert {r["person_id"] for r in rows if r["parent_team"]} == team & {r["person_id"] for r in rows}
        for g in ("bench", "ending", "pull"):                                   # 갈래마다 상위 사업 팀원이 맨 위
            flags = [r["parent_team"] for r in cands[g]]
            assert flags == sorted(flags, reverse=True)
        # 단계를 바꿔도 연결이 남는다
        ws = c.patch(f"/api/workspace/projects/{pid}", json={"dataset_version": ws["dataset_version"], "stage": "수주"}).json()["workspace"]
        item = next(p for p in ws["pipeline"] if p["project_id"] == pid)
        assert item["stage"] == "수주" and item["parent"] == "J001" and item["external_headcount"] == 6
        # 떨어지면 상위 사업의 추가 과제 목록에서도 빠진다
        ws = c.delete(f"/api/workspace/projects/{pid}", params={"dataset_version": ws["dataset_version"]}).json()["workspace"]
        assert next(o for o in ws["ongoing"] if o["project_id"] == "J001")["addons"] == []


def test_addon_rules_are_checked_on_the_server(yearend):
    from api.main import app
    with TestClient(app) as c:
        v = _ws(c)["dataset_version"]
        base = c.get("/api/workspace/projects/J001/form", params={"as": "addon"}).json()

        def post(**kw):
            return c.post("/api/workspace/projects", json={"dataset_version": v, "project": {**base, **kw}})
        late = post(end_month="2027-06")                                      # 상위 사업(~2027-03) 밖
        assert late.status_code == 422 and "상위 사업 기간" in late.json()["detail"]
        assert "신규 사업" in post(parent="J014").json()["detail"]             # 신규 사업에는 붙이지 않는다
        assert post(parent="J999").status_code == 422
        assert post(predecessor="J003").status_code == 422                    # 고도화와 추가 과제는 함께 못 고른다
        assert post(external_headcount=-1).status_code == 422
        assert _ws(c)["dataset_version"] == v                                  # 거절은 판을 바꾸지 않는다


def test_plain_projects_keep_the_old_manifest_shape(yearend):
    """추가 과제 칸은 추가 과제에만 -- 예전 신규 사업 등록의 manifest 모양(데이터 버전)이 그대로."""
    from api.main import app
    from api.workspace import current_files
    from api.workspace_edits import pipeline_of, read_manifest
    with TestClient(app) as c:
        v = _ws(c)["dataset_version"]
        form = {**c.get("/api/workspace/projects/J003/form").json(), "name": "보험사 B 고도화", "start_month": "2027-01",
                "end_month": "2027-06", "stage": "예정", "predecessor": "J003"}
        pid = c.post("/api/workspace/projects", json={"dataset_version": v, "project": form}).json()["project_id"]
        entry = pipeline_of(read_manifest(current_files(app, app.state.dataset)))[pid]
    assert set(entry) == {"stage", "predecessor", "registered_at"}


def test_the_parent_period_check_reads_months_like_the_loader():
    """Codex 리뷰(2026-10-19): 입력 검증은 CSV 값의 공백을 걷는데 기간 검사는 원문을 비교해 " 2026-11" 시작이 뚫렸다."""
    import pytest
    from tests.scenarios.scenarios_paths import YEAREND_FILES
    from api.workspace_edits import EditError, NewProject, add_project, addon_form, read_table, write_table, PROJECTS
    files = YEAREND_FILES()
    h, rows = read_table(files, PROJECTS)
    for r in rows:
        if r["project_id"] == "J001":
            r["start_month"], r["end_month"] = " 2026-11", "2027-03 "
    write_table(files, PROJECTS, h, rows)
    f = addon_form(files, "J001")                                      # 공백이 있어도 미리 채운다
    assert (f.start_month, f.end_month) == ("2026-11", "2027-03")
    with pytest.raises(EditError, match="상위 사업 기간"):
        add_project(files, NewProject(**{**f.model_dump(), "start_month": "2026-10"}))
    add_project(files, f)                                               # 경계 안은 된다


def test_the_workspace_view_sends_trimmed_months(yearend, monkeypatch):
    """Codex 리뷰 2라운드: 화면은 상위 사업 기간을 작업 데이터 보기의 start·end와 비교한다 -- 공백 원문이면 정상 기간도 막혔다."""
    from api.main import app
    from api.workspace_edits import PROJECTS, read_table, write_table
    import api.routes.workspace as wsr
    real = wsr.current_files

    def spaced(app_, dataset):
        files = dict(real(app_, dataset))
        h, rows = read_table(files, PROJECTS)
        for r in rows:
            if r["project_id"] == "J001":
                r["start_month"], r["end_month"] = " 2026-10", "2027-03 "
        write_table(files, PROJECTS, h, rows)
        return files
    monkeypatch.setattr(wsr, "current_files", spaced)
    with TestClient(app) as c:
        j001 = next(o for o in c.get("/api/workspace").json()["ongoing"] if o["project_id"] == "J001")
    assert (j001["start"], j001["end"]) == ("2026-10", "2027-03")


def test_an_addon_keeps_working_after_its_parent_team_moved_into_it():
    """폴백 리뷰(2026-10-19): 상위 사업 팀원을 모두 추가 과제로 옮긴 뒤에도 그 과제의 단계를 바꿀 수 있다. 새로 붙이는 것은 여전히 막는다."""
    import pytest
    from tests.scenarios.scenarios_paths import YEAREND_FILES
    from api.workspace_edits import (CURRENT, EditError, NewProject, add_project, addon_form, read_table, update_project,
                                     write_table, PROJECTS)
    files = YEAREND_FILES()
    files, pid = add_project(files, addon_form(files, "J001"))
    h, cur = read_table(files, CURRENT)
    write_table(files, CURRENT, h, [r for r in cur if r["project_id"] != "J001"])       # 상위 팀이 비었다
    files = update_project(files, pid, stage="수주")
    with pytest.raises(EditError, match="배치된 사람이 없다"):
        add_project(files, addon_form(files, "J001"))
    # 아주 긴 상위 사업 이름도 양식이 만들어진다(120자 상한)
    h, rows = read_table(files, PROJECTS)
    for r in rows:
        if r["project_id"] == "J002":
            r["project_name"] = "가" * 200
    write_table(files, PROJECTS, h, rows)
    assert len(addon_form(files, "J002").name) == 120
