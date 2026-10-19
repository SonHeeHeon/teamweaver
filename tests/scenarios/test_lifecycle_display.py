"""사업 수명 주기 표시(2026-10-19, 사용자 "24년에 시작해서 아직 진행중인 플젝 … 추가 과제가 있어 6달만 회사 인력은 2명만 추가(외주는 여러명)"):
생성기 v4 manifest `lifecycle` + 화면 등록 추가 과제(pipeline)를 합쳐 /api/meta·/api/workspace가 단계·실제 시작·원래 기간·상위·외주를 싣는다.
신규 사업을 고치거나 떨어뜨리면 생성 때의 기록도 맞춘다(로더 경고가 남지 않게)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.lifecycle import lifecycle_of
from core.ingest import lifecycle as lc_check
from core.ingest.report import IngestReport

ANNUAL = Path(__file__).resolve().parents[2] / "demo" / "org-n100-annual"


@pytest.fixture
def annual(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(ANNUAL.parent))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(ANNUAL))
    return ANNUAL


def test_the_record_merges_generated_life_cycles_with_screen_add_ons():
    m = {"lifecycle": {"J1": {"stage": "운영", "started": "2024-03"},
                       "J2": {"stage": "추가 과제", "started": "2026-11", "parent": "J1", "external_headcount": 6},
                       "J3": {"stage": "유지보수", "started": "2024-01"},                 # 모르는 단계 -- 버림
                       "J4": {"stage": "고도화", "started": "25-01"},                    # 틀린 달 -- 단계만
                       "J5": {"stage": "추가 과제", "started": "2026-12", "parent": "J1", "external_headcount": 2},
                       "J6": "운영"},
         "pipeline": {"J2": {"stage": "제안", "parent": "J1", "external_headcount": 4},  # 화면에서 고친 외주 인원이 이긴다
                      "J5": {"stage": "예정", "predecessor": None},                     # 화면에서 추가 과제를 풀었다
                      "J7": {"stage": "예정", "parent": "J1", "external_headcount": 3},  # 화면에서만 등록한 추가 과제
                      "J8": {"stage": "예정", "predecessor": "J1"}}}
    out = lifecycle_of(m)
    assert out["J1"] == {"stage": "운영", "started": "2024-03", "parent": None, "external_headcount": None}
    assert out["J2"] == {"stage": "추가 과제", "started": "2026-11", "parent": "J1", "external_headcount": 4}
    assert out["J4"]["started"] is None and "J3" not in out and "J6" not in out
    assert "J5" not in out and "J8" not in out
    assert out["J7"] == {"stage": "추가 과제", "started": None, "parent": "J1", "external_headcount": 3}
    assert lifecycle_of({}) == {} and lifecycle_of(None) == {} and lifecycle_of({"lifecycle": []}) == {}


def test_meta_projects_carry_stage_real_start_and_original_period(yearend):
    from api.main import app
    with TestClient(app) as c:
        meta = c.get("/api/meta").json()
    by = {p["id"]: p for p in meta["projects"]}
    assert by["J002"]["lifecycle"] == {"stage": "운영", "started": "2024-06", "parent": None, "external_headcount": None}
    assert by["J002"]["period"] == {"start": "2024-06", "end": "2028-01"}          # 계획(2026-10~2027-03) 밖까지
    assert (by["J002"]["start_month"], by["J002"]["end_month"]) == (0, 5)         # 계산은 계획 안으로 자른 달
    assert by["J020"]["lifecycle"] == {"stage": "추가 과제", "started": "2026-11", "parent": "J011", "external_headcount": 6}
    assert by["J020"]["period"] == {"start": "2026-11", "end": "2027-04"}
    assert by["J001"]["lifecycle"] is None and by["J001"]["period"] is None      # 계획 안의 새 사업


def test_twelve_month_meta_keeps_the_original_period_of_long_running_projects(annual):
    from api.main import app
    with TestClient(app) as c:
        by = {p["id"]: p for p in c.get("/api/meta").json()["projects"]}
    assert by["J021"]["lifecycle"]["stage"] == "운영" and by["J021"]["period"] == {"start": "2024-02", "end": "2028-10"}
    assert by["J025"]["lifecycle"]["parent"] == "J021" and by["J025"]["lifecycle"]["external_headcount"] == 3


def test_workspace_items_carry_the_life_cycle(yearend):
    from api.main import app
    with TestClient(app) as c:
        ws = c.get("/api/workspace").json()
    ongoing = {o["project_id"]: o for o in ws["ongoing"]}
    pipe = {p["project_id"]: p for p in ws["pipeline"]}
    assert ongoing["J002"]["lifecycle"]["stage"] == "운영" and ongoing["J002"]["start"] == "2024-06"
    assert ongoing["J011"]["addons"] == ["J020"]
    assert pipe["J020"]["lifecycle"]["parent"] == "J011" and pipe["J018"]["lifecycle"] is None
    assert ongoing["J001"]["lifecycle"] is None


def _lifecycle_warnings(files) -> list[str]:
    from api.workspace_edits import PROJECTS, read_manifest, read_table
    r = IngestReport()
    lc_check.check(read_manifest(files), read_table(files, PROJECTS)[1], r)
    return [i.message for i in r.issues if i.level == "warning"]


def test_editing_or_dropping_a_generated_add_on_keeps_the_record_clean():
    from tests.scenarios.scenarios_paths import YEAREND_FILES
    from api.workspace_edits import project_form, read_manifest, remove_project, update_project
    files = YEAREND_FILES()
    assert _lifecycle_warnings(files) == []
    form = project_form(files, "J020").model_copy(update={"start_month": "2026-12", "external_headcount": 4})
    moved = update_project(files, "J020", form=form)
    assert read_manifest(moved)["lifecycle"]["J020"] == {"stage": "추가 과제", "started": "2026-12", "parent": "J011",
                                                         "external_headcount": 4}
    assert _lifecycle_warnings(moved) == []
    plain = update_project(files, "J020", form=form.model_copy(update={"parent": None}))
    assert "J020" not in read_manifest(plain)["lifecycle"] and _lifecycle_warnings(plain) == []
    dropped, _ = remove_project(files, "J020")
    assert "J020" not in read_manifest(dropped)["lifecycle"] and _lifecycle_warnings(dropped) == []


def test_projects_without_a_record_keep_the_old_manifest_shape():
    """기록이 없는 신규 사업(J018)을 고쳐도 manifest에 lifecycle 칸이 새로 생기지 않는다. 기록이 없는 묶음은 그대로."""
    from tests.scenarios.scenarios_paths import YEAREND_FILES
    from api.workspace_edits import MANIFEST, read_manifest, update_project, write_manifest
    files = YEAREND_FILES()
    before = read_manifest(files)["lifecycle"]
    after = read_manifest(update_project(files, "J018", stage="수주"))["lifecycle"]
    assert after == before
    m = read_manifest(files)
    del m["lifecycle"]
    write_manifest(files, m)
    assert "lifecycle" not in read_manifest(update_project(files, "J018", stage="수주"))
    assert MANIFEST in files
