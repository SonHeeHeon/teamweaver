"""K8: 관리자 배치 설정(GET/PUT /api/settings)과 milp_params 요청 계약.

설정은 서버 파일에 영속하지만 /api/optimize·/api/whatif는 stateless로 남는다 --
웹이 설정을 읽어 milp_params로 명시적으로 보낸다(.omc/plan/2026-10-05-k8-milp-settings.md).
"""
import json

import pytest

from api.cache import ResultCache
from api.settings import PlacementSettings, SettingsStore
from core.optimize.milp import MilpParams


# --- 설정 모델 -----------------------------------------------------------

def test_setting_default_min_alloc_is_30_percent_but_model_default_is_untouched():
    """실데이터 답변의 30%는 *설정의* 기본값이다. MilpParams 기본값 변경은 C6 소관."""
    assert PlacementSettings().min_alloc == pytest.approx(0.30)
    assert MilpParams().min_alloc == pytest.approx(0.20)


def test_settings_convert_to_milp_params_keeping_hidden_fields_at_model_default():
    s = PlacementSettings(min_alloc=0.4, lam=0.1, time_limit=30)
    p = s.to_milp_params()
    assert (p.min_alloc, p.lam, p.time_limit) == (0.4, 0.1, 30)
    # 화면에 노출하지 않는 내부 근사값은 모델 기본값 그대로다.
    assert p.pair_keep_ratio == MilpParams().pair_keep_ratio
    assert p.max_pairs == MilpParams().max_pairs
    assert p.slack_penalty == MilpParams().slack_penalty


@pytest.mark.parametrize("field, value", [
    ("min_alloc", 0.0), ("min_alloc", 1.01), ("lam", -0.1), ("mu", 1.5),
    ("clique_threshold_months", 0), ("time_limit", 4), ("time_limit", 901), ("gap", 0.5),
    ("pair_keep_ratio", 0.5),            # 노출하지 않는 필드는 받지 않는다
])
def test_out_of_range_or_unknown_setting_is_rejected(field, value):
    with pytest.raises(ValueError):
        PlacementSettings(**{field: value})


# --- 저장소 --------------------------------------------------------------

def test_store_round_trips_through_file(tmp_path):
    path = tmp_path / "s" / "settings.json"
    store = SettingsStore(path)
    assert store.current().settings == PlacementSettings()
    assert store.current().updated_at is None
    store.save(PlacementSettings(min_alloc=0.25))
    again = SettingsStore(path)
    assert again.current().settings.min_alloc == pytest.approx(0.25)
    assert again.current().updated_at is not None
    assert again.current().load_error is None


def test_store_reports_corrupt_file_instead_of_silently_defaulting(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    state = SettingsStore(path).current()
    assert state.settings == PlacementSettings()
    assert state.load_error and "settings.json" in state.load_error


def test_store_reports_out_of_range_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"settings": {"min_alloc": 7}}), encoding="utf-8")
    state = SettingsStore(path).current()
    assert state.settings == PlacementSettings()
    assert state.load_error


def test_save_is_atomic_and_clears_load_error(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    store = SettingsStore(path)
    store.save(PlacementSettings(min_alloc=0.5))
    assert store.current().load_error is None
    assert json.loads(path.read_text("utf-8"))["settings"]["min_alloc"] == 0.5
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]   # 임시 파일 잔존 없음


# --- API ---------------------------------------------------------------

def test_get_settings_returns_values_defaults_and_bounds(client):
    body = client.get("/api/settings").json()
    assert body["settings"]["min_alloc"] == pytest.approx(0.30)
    assert body["defaults"] == body["settings"]
    assert body["bounds"]["min_alloc"] == {"min": 0.05, "max": 1.0}
    assert body["bounds"]["time_limit"] == {"min": 5, "max": 900}
    assert set(body["bounds"]) == set(body["settings"])
    assert body["updated_at"] is None and body["load_error"] is None


def test_put_settings_persists_across_app_restart(client, settings_path):
    new = dict(client.get("/api/settings").json()["settings"], min_alloc=0.35)
    res = client.put("/api/settings", json={"settings": new, "based_on": None})
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["min_alloc"] == pytest.approx(0.35)
    assert json.loads(settings_path.read_text("utf-8"))["settings"]["min_alloc"] == 0.35

    from fastapi.testclient import TestClient

    from api.main import app
    with TestClient(app) as again:                      # lifespan이 파일에서 다시 읽는다
        assert again.get("/api/settings").json()["settings"]["min_alloc"] == pytest.approx(0.35)


@pytest.mark.parametrize("patch", [{"min_alloc": 0}, {"min_alloc": "x"}, {"typo_field": 1},
                                   {"pair_keep_ratio": 0.2}])
def test_put_invalid_settings_is_422_and_does_not_persist(client, settings_path, patch):
    body = dict(client.get("/api/settings").json()["settings"], **patch)
    assert client.put("/api/settings", json={"settings": body, "based_on": None}).status_code == 422
    assert not settings_path.exists()


def test_put_requires_every_field(client):
    """일부만 보내면 나머지가 조용히 기본값으로 바뀐다 -- 전체를 요구한다."""
    assert client.put("/api/settings", json={"settings": {"min_alloc": 0.4},
                                             "based_on": None}).status_code == 422


# --- milp_params 요청 계약 ----------------------------------------------

@pytest.mark.parametrize("params", [{"min_alloc": -1}, {"min_alloc": 2}, {"mni_alloc": 0.3},
                                    {"time_limit": 0}, {"gap": -0.1}])
def test_optimize_rejects_invalid_milp_params_before_streaming(small_graph_client, params):
    res = small_graph_client.post("/api/optimize", json={"weights": {}, "milp_params": params,
                                                         "n_alternatives": 0})
    assert res.status_code == 422, res.text


def test_whatif_rejects_unknown_milp_param(client):
    res = client.post("/api/whatif", json={
        "entries": [{"person_id": "p000", "project_id": "j00", "alloc": 0.5}],
        "swap": {"out_person_id": "p000", "in_person_id": "p001", "project_id": "j00"},
        "milp_params": {"mni_alloc": 0.3}})
    assert res.status_code == 422


def test_cache_key_uses_effective_params_not_raw_request():
    """{}과 기본값을 명시한 요청은 같은 계산이다 -- 같은 키여야 한다."""
    explicit = MilpParams().model_dump()
    assert ResultCache.key({}, MilpParams(), 3, "v") == ResultCache.key({}, MilpParams(**explicit), 3, "v")
    assert ResultCache.key({}, MilpParams(), 3, "v") != ResultCache.key({}, MilpParams(min_alloc=0.3), 3, "v")


def test_optimize_uses_sent_min_alloc(small_graph_client, monkeypatch):
    """보낸 milp_params가 실제로 솔버까지 전달된다(설정 → 계산 연결)."""
    seen = []

    def fake(graph, S, C, params, n, outcome=None):
        seen.append(params)
        return iter(())

    monkeypatch.setattr("api.routes.optimize.generate_plans_streaming", fake)
    monkeypatch.setattr("api.routes.optimize._skill_relaxation_upper_bound",
                        lambda graph, S, params: 1.0)
    with small_graph_client.stream("POST", "/api/optimize", json={
            "weights": {}, "milp_params": {"min_alloc": 0.3}, "n_alternatives": 0}) as res:
        list(res.iter_lines())
    assert seen and seen[0].min_alloc == pytest.approx(0.3)


def test_warmup_uses_stored_settings(monkeypatch, settings_path):
    """부팅 사전계산은 저장된 설정으로 한다 -- 웹이 그 설정을 보내므로 첫 실행이 캐시 히트."""
    from fastapi.testclient import TestClient

    import api.main as main

    SettingsStore(settings_path).save(PlacementSettings(min_alloc=0.45))
    seen = []
    monkeypatch.delenv("TEAMWEAVER_SKIP_WARM", raising=False)
    monkeypatch.setattr(main, "generate_plans",
                        lambda graph, S, C, params, n_alternatives, outcome=None: seen.append(params) or [])
    with TestClient(main.app) as c:
        cache = c.app.state.cache
        n = len(c.app.state.dataset.graph.people)           # 자동 계산 시간(기본)은 인원수로 정해진다
        key = ResultCache.key({}, PlacementSettings(min_alloc=0.45).to_milp_params(n_people=n), 3,
                              c.app.state.dataset.info.version)
        assert cache.get(key) == []
    assert seen and seen[0].min_alloc == pytest.approx(0.45)


def test_report_accepts_plan_basis_and_rejects_bad_one(client, monkeypatch, tmp_path):
    """PDF 요청의 milp_params(계산 기준)는 설정과 같은 계약으로 검사한다."""
    import api.routes.report as report_route

    index = tmp_path / "index.html"
    index.write_text("<div id=\"root\"></div>")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    seen = []

    async def fake_render(payload, *args, **kwargs):
        seen.append(payload)
        return b"%PDF-fake"

    monkeypatch.setattr(report_route, "render_report_pdf", fake_render)
    base = {"plan_label": "A", "entries": [], "objective": 1.0, "fulfillment": 1.0,
            "optimization_ratio": 1.0}
    ok = client.post("/api/report", json={**base, "milp_params": PlacementSettings().model_dump()})
    assert ok.status_code == 200, ok.text
    assert seen[0]["milp_params"]["min_alloc"] == pytest.approx(0.30)
    bad = client.post("/api/report", json={**base, "milp_params": {"min_alloc": 3}})
    assert bad.status_code == 422
    # 자동 시간 칸이 생기기 전의 화면은 time_limit_auto를 보내지 않는다 -- 그래도 PDF는 받는다(리뷰 S4).
    legacy = {k: v for k, v in PlacementSettings().model_dump().items() if k != "time_limit_auto"}
    assert client.post("/api/report", json={**base, "milp_params": legacy}).status_code == 200


# --- 리뷰 반영(1라운드) ----------------------------------------------------

def test_unreadable_settings_folder_does_not_crash_boot(tmp_path):
    """폴더 권한이 없으면 exists()부터 PermissionError -- 서버가 안 뜨면 안 된다."""
    import os
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0)
    try:
        state = SettingsStore(locked / "settings.json").current()
    finally:
        os.chmod(locked, 0o700)
    assert state.settings == PlacementSettings()
    assert state.load_error


def test_non_string_updated_at_is_a_load_error(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"settings": PlacementSettings().model_dump(), "updated_at": 123}))
    assert SettingsStore(path).current().load_error


def test_saving_over_unreadable_file_keeps_a_copy(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{hand edited but broken", encoding="utf-8")
    SettingsStore(path).save(PlacementSettings(min_alloc=0.4))
    kept = [p for p in tmp_path.iterdir() if p.name.startswith("settings.json.unreadable-")]
    assert len(kept) == 1 and kept[0].read_text("utf-8") == "{hand edited but broken"


def test_put_reports_unwritable_location_as_500_with_reason(client, settings_path, monkeypatch):
    from api.settings import SettingsStore as Store

    def boom(self, settings):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Store, "_save_locked", boom)
    res = client.put("/api/settings", json={"settings": PlacementSettings().model_dump(),
                                            "based_on": None})
    assert res.status_code == 500
    assert "Permission denied" in res.json()["detail"]


def test_report_basis_must_be_complete(client, monkeypatch, tmp_path):
    import api.routes.report as report_route

    index = tmp_path / "index.html"
    index.write_text("x")
    monkeypatch.setattr(report_route, "_DIST_INDEX", index)
    base = {"plan_label": "A", "entries": [], "objective": 1.0, "fulfillment": 1.0,
            "optimization_ratio": 1.0}
    res = client.post("/api/report", json={**base, "milp_params": {"min_alloc": 0.25}})
    assert res.status_code == 422


def test_milp_params_in_mirrors_every_model_field():
    """C6에서 MilpParams 필드가 늘면 HTTP 계약(extra=forbid)도 같이 늘려야 한다."""
    from api.schemas import MilpParamsIn
    # solver는 일부러 HTTP 계약 밖에 둔다: 서비스 솔버는 HiGHS로 고정(사용자 결정 2026-10-05), "cbc"는 측정용.
    # seat_fit_weight도 HTTP 밖: 로드맵 3번 실험에서 기각된 측정용 항(기본 0)이라 클라이언트가 켤 수 없게 한다.
    # time_limit_auto는 그 반대: 관리자 설정의 표시 칸이라 HTTP에는 있지만 모델에는 없다(계산엔 time_limit 숫자만).
    # review_judge는 없앤 칸이다(2026-10-06 LLM 통일) -- 이전 화면이 보내도 무시하려고 HTTP에만 남긴다.
    # solver_seeds는 HTTP 밖(2026-10-06 claude-a): 동시에 쓰는 CPU 코어 수라 요청마다 정하지 않고 서버 설정이 정한다.
    from api.settings import NON_SOLVER_FIELDS
    assert set(MilpParamsIn.model_fields) - NON_SOLVER_FIELDS - {"review_judge"} == \
        set(MilpParams.model_fields) - {"solver", "seat_fit_weight", "solver_seeds",
                                        # 실험 G(파트너 다양성 하한, 2026-10-06 claude-a): 측정 단계, 채택 결정 전
                                        "partner_floor", "partner_floor_weight"}
    with pytest.raises(Exception):
        MilpParamsIn(solver="cbc")
    with pytest.raises(Exception):
        MilpParamsIn(seat_fit_weight=1.0)


def test_zero_pair_keep_ratio_is_a_valid_experiment_value():
    from api.schemas import MilpParamsIn
    assert MilpParamsIn(pair_keep_ratio=0.0).to_milp_params().pair_keep_ratio == 0.0


def test_warmup_is_skipped_when_stored_time_limit_is_large(monkeypatch, settings_path):
    """사전계산이 기동을 막으므로 큰 시간 한도(최대 600초 × 4회)면 생략한다."""
    from fastapi.testclient import TestClient

    import api.main as main

    SettingsStore(settings_path).save(PlacementSettings(time_limit=300, time_limit_auto=False))
    seen = []
    monkeypatch.delenv("TEAMWEAVER_SKIP_WARM", raising=False)
    monkeypatch.setattr(main, "generate_plans", lambda *a, **k: seen.append(1) or [])
    with TestClient(main.app):
        pass
    assert seen == []



# --- 리뷰 반영(2라운드) ----------------------------------------------------

def test_put_based_on_stale_read_is_409_and_keeps_other_change(client, settings_path):
    """A·B가 같은 화면을 열고 B가 먼저 저장하면, A의 저장은 B의 변경을 덮지 못한다."""
    read = client.get("/api/settings").json()
    b = client.put("/api/settings", json={"settings": dict(read["settings"], gap=0.1),
                                         "based_on": read["updated_at"]})
    assert b.status_code == 200
    a = client.put("/api/settings", json={"settings": dict(read["settings"], min_alloc=0.4),
                                         "based_on": read["updated_at"]})
    assert a.status_code == 409
    now = client.get("/api/settings").json()["settings"]
    assert now["gap"] == pytest.approx(0.1) and now["min_alloc"] == pytest.approx(0.30)
    ok = client.put("/api/settings", json={"settings": dict(now, min_alloc=0.4),
                                          "based_on": b.json()["updated_at"]})
    assert ok.status_code == 200


def test_put_without_based_on_is_422(client):
    assert client.put("/api/settings", json={"settings": PlacementSettings().model_dump()}
                      ).status_code == 422


def test_warmup_is_skipped_when_stored_gap_is_small(monkeypatch, settings_path):
    """gap이 작으면 대안마다 시간 한도까지 간다(실측 gap 0·120초 → 부팅 377초)."""
    from fastapi.testclient import TestClient

    import api.main as main

    SettingsStore(settings_path).save(PlacementSettings(gap=0.0))
    seen = []
    monkeypatch.delenv("TEAMWEAVER_SKIP_WARM", raising=False)
    monkeypatch.setattr(main, "generate_plans", lambda *a, **k: seen.append(1) or [])
    with TestClient(main.app):
        pass
    assert seen == []


def test_max_concurrent_projects_setting_reaches_the_solver(small_graph_client, monkeypatch):
    """C6: 동시 프로젝트 상한은 관리자 설정(1~6, 기본 3)이며 최적화까지 전달된다."""
    from api.settings import PlacementSettings
    assert PlacementSettings().max_concurrent_projects == 3
    assert PlacementSettings.bounds()["max_concurrent_projects"] == {"min": 1, "max": 6}
    assert PlacementSettings(max_concurrent_projects=1).to_milp_params().max_concurrent_projects == 1
    seen = []

    def fake(graph, S, C, params, n, outcome=None):
        seen.append(params)
        return iter(())

    monkeypatch.setattr("api.routes.optimize.generate_plans_streaming", fake)
    monkeypatch.setattr("api.routes.optimize._skill_relaxation_upper_bound", lambda graph, S, params: 1.0)
    with small_graph_client.stream("POST", "/api/optimize", json={
            "weights": {}, "milp_params": {"max_concurrent_projects": 1}, "n_alternatives": 0}) as res:
        list(res.iter_lines())
    assert seen[0].max_concurrent_projects == 1


def test_settings_carry_the_recommended_time_for_the_active_dataset(client):
    """claude-a 요청: 설정 화면에 지금 데이터 규모의 권장 계산 시간을 안내한다(core/optimize/time_budget)."""
    from core.optimize.time_budget import recommend
    body = client.get("/api/settings").json()
    rt = body["recommended_time"]
    n = len(client.get("/api/meta").json()["people"])
    assert rt["n_people"] == n and rt["per_solve_s"] == recommend(n).per_solve_s
    assert rt["worst_case_total_s"] == 4 * rt["per_solve_s"] and rt["basis"]


def test_settings_also_carry_the_monthly_recommended_time(client):
    from core.optimize.time_budget import recommend
    body = client.get("/api/settings").json()
    n = body["recommended_time"]["n_people"]
    assert body["recommended_time_monthly"]["per_solve_s"] == recommend(n, allocation_mode="monthly").per_solve_s
    assert body["recommended_time_monthly"]["per_solve_s"] >= body["recommended_time"]["per_solve_s"]



def test_auto_time_limit_uses_the_recommendation_for_the_active_dataset(client):
    """claude-a 요청: 계산 시간 기본은 '자동(인원 기준)'. 서버가 실제로 쓸 값(effective)을 함께 준다."""
    from core.optimize.time_budget import recommend
    body = client.get("/api/settings").json()
    n = body["recommended_time"]["n_people"]
    assert body["settings"]["time_limit_auto"] is True
    assert body["effective_time_limit"] == recommend(n).per_solve_s
    manual = PlacementSettings(time_limit=77, time_limit_auto=False)
    assert manual.to_milp_params(n_people=n).time_limit == 77
    monthly = PlacementSettings(allocation_mode="monthly")
    assert monthly.to_milp_params(n_people=n).time_limit == recommend(n, allocation_mode="monthly").per_solve_s


def test_time_limited_plans_are_flagged_in_the_stream(small_graph_client, monkeypatch):
    """claude-a 요청: 시간 한도에서 멈춘 해(최선 증명 전)는 플랜에 표시가 붙는다."""
    import json as _json
    import api.routes.optimize as route
    from core.optimize.types import AssignEntry, PlanAssignment
    plan = PlanAssignment(entries=[AssignEntry(person_id="p000", project_id="j00", alloc=0.5)],
                          objective=1.0, unfilled=[], violations=[], label="A", time_limited=True)
    monkeypatch.setattr(route, "generate_plans_streaming", lambda *a, **k: iter([plan]))
    with small_graph_client.stream("POST", "/api/optimize", json={"weights": {"Python": 3}, "n_alternatives": 0}) as res:
        events = [_json.loads(l[5:].strip()) for l in res.iter_lines() if l.startswith("data:")]
    assert next(e for e in events if e.get("label") == "A")["time_limited"] is True


def test_settings_file_saved_before_auto_time_keeps_its_manual_time(tmp_path):
    # 자동 계산 시간 이전에 저장된 파일(칸 없음)은 관리자가 정한 시간을 그대로 쓴다(리뷰 S1).
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"settings": {"time_limit": 300}, "updated_at": "2026-10-01T00:00:00+00:00"}),
                    encoding="utf-8")
    state = SettingsStore(path).current()
    assert state.load_error is None
    assert state.settings.time_limit_auto is False
    assert state.settings.to_milp_params(n_people=100).time_limit == 300


def test_saved_auto_flag_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    SettingsStore(path).save(PlacementSettings(time_limit_auto=True))
    assert SettingsStore(path).current().settings.time_limit_auto is True
