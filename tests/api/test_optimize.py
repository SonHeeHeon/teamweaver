"""SSE 플러밍 테스트. small_graph_client(15명/3프로젝트 합성 데이터셋)를 써서
실제 CBC를 풀되 1초 미만으로 끝낸다 -- 동결 fixture(100명/20프로젝트) 규모의
진짜 흐름 검증은 slow 마커가 붙은 Task 8의 E2E 테스트가 맡는다."""
import json

import pytest
from fastapi.testclient import TestClient

from api.main import app


def test_optimize_streams_plan_a_then_alternatives(small_graph_client):
    with small_graph_client.stream(
            "POST", "/api/optimize", json={"weights": {}, "n_alternatives": 1}) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        events = []
        for line in res.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    labels = [e["label"] for e in events if "label" in e]
    assert labels[0] == "A"
    assert len(labels) >= 1
    assert events[-1].get("event") == "done" or "count" in events[-1]


def test_optimize_streams_error_event_when_solver_raises(small_graph_client, monkeypatch):
    """워커 스레드에서 발생한 예외가 event: error SSE 프레임으로 정상 전파되는지 확인한다.

    `api/routes/optimize.py`는 `from core.optimize.alternatives import
    generate_plans_streaming`로 이름을 자신의 모듈 네임스페이스에 바인딩해 두므로,
    monkeypatch도 그 바인딩("api.routes.optimize.generate_plans_streaming")을
    갈아끼워야 라우터가 실제로 가짜 함수를 호출한다(core.optimize.alternatives 쪽을
    패치해도 라우터는 이미 가진 원본 참조를 그대로 쓰므로 효과가 없다).

    `StreamingResponse`는 헤더를 스트림 시작 시 이미 흘려보내므로, 본문 중간에
    예외가 나도 HTTP status는 여전히 200이어야 한다 -- `api/routes/optimize.py`의
    `event_stream()`이 예외를 자신 안에서 잡아 `event: error` 프레임으로 바꿔
    정상 종료하기 때문(예외가 StreamingResponse 밖으로 새어나가 커넥션을 깨지
    않음). small_graph_client(15명/3프로젝트)로 빠르게 유지한다."""

    def fake_generate_plans_streaming(*args, **kwargs):
        raise RuntimeError("boom - simulated solver failure")
        yield  # pragma: no cover -- unreachable; keeps this a generator function

    monkeypatch.setattr(
        "api.routes.optimize.generate_plans_streaming", fake_generate_plans_streaming)

    with small_graph_client.stream(
            "POST", "/api/optimize", json={"weights": {}, "n_alternatives": 1}) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        event_types = []
        payloads = []
        for line in res.iter_lines():
            if line.startswith("event:"):
                event_types.append(line[len("event:"):].strip())
            elif line.startswith("data:"):
                payloads.append(json.loads(line[len("data:"):].strip()))

    assert event_types == ["error"], (
        "solver가 첫 순회에서 즉시 raise하므로 plan/done 없이 error 프레임 하나뿐이어야 함")
    assert len(payloads) == 1
    assert "boom" in payloads[0]["message"]


@pytest.mark.slow
def test_default_scenario_is_warmed_at_startup(monkeypatch):
    """conftest.py의 client 픽스처는 TEAMWEAVER_SKIP_WARM=1을 강제하므로 워밍
    경로 자체를 검증할 수 없다(의도적 -- 그래서 기본 스위트가 빠르다). 이
    테스트는 그 가드를 명시적으로 해제한 별도 TestClient로 실제 부팅 워밍이
    동작하는지 확인한다. 동결 fixture(100명/20프로젝트) 전체를 실제로 풀므로
    slow(실측 28.5초 안팎).

    K8 이후 사전계산은 저장된 배치 설정으로 한다 -- 웹처럼 그 설정을
    milp_params로 보내야 캐시에 맞는다(설정 파일은 conftest가 임시 경로로 돌린다)."""
    monkeypatch.delenv("TEAMWEAVER_SKIP_WARM", raising=False)
    with TestClient(app) as warmed_client:
        import time
        params = warmed_client.get("/api/settings").json()["settings"]
        start = time.monotonic()
        with warmed_client.stream(
                "POST", "/api/optimize",
                json={"weights": {}, "milp_params": params, "n_alternatives": 3}) as res:
            list(res.iter_lines())
        elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"캐시 히트인데 {elapsed:.1f}초 걸림 -- 워밍이 안 됐거나 캐시 미적중"


def test_optimize_rejects_out_of_range_weight(small_graph_client):
    """weights 값은 PoC 설계의 1~5점 범위로 bound된다(Field(ge=1, le=5)).
    0은 범위 밖이라 라우터 코드가 실행되기도 전에 Pydantic이 422로 막아야
    한다 -- core/scoring/engine.py::skill_matrix의 den==0 -> NaN 크래시를
    core/를 건드리지 않고 API 경계에서 막는 방식."""
    res = small_graph_client.post(
        "/api/optimize", json={"weights": {"Java": 0}, "n_alternatives": 0})
    assert res.status_code == 422


def test_plan_events_carry_fulfillment_and_optimization_ratio(small_graph_client):
    """PoC 설계 §6이 요구하는 '점수 상세 + 매칭 충족률'. 프론트가 core의 지표
    로직을 복제하지 않으려면 서버가 실어 보내야 한다."""
    with small_graph_client.stream(
            "POST", "/api/optimize", json={"weights": {}, "n_alternatives": 0}) as res:
        events = []
        for line in res.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    plans = [e for e in events if "label" in e]
    assert plans, "plan 이벤트가 없다"
    for p in plans:
        assert 0.0 <= p["fulfillment"] <= 1.0
        assert 0.0 <= p["optimization_ratio"] <= 1.0 + 1e-6


def test_cors_headers_allow_the_vite_dev_server(client):
    """Vite dev 서버(:5173)가 API(:8000)를 부를 수 있어야 한다."""
    res = client.get("/api/meta", headers={"Origin": "http://localhost:5173"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_done_event_reports_requested_alternatives(small_graph_client):
    """C2: 조건을 만족하는 대안이 모자라면 서버는 덜 내고, done에 요청 수를 실어 화면이 알릴 수 있게 한다."""
    with small_graph_client.stream(
            "POST", "/api/optimize", json={"weights": {}, "n_alternatives": 2}) as res:
        events = [json.loads(line[len("data:"):].strip()) for line in res.iter_lines()
                  if line.startswith("data:")]
    done = events[-1]
    assert done["requested_alternatives"] == 2
    assert done["count"] == sum(1 for e in events if "label" in e)


def test_alternatives_cut_short_by_solver_failure_are_not_cached(small_graph_client, monkeypatch):
    """시간 초과처럼 부하에 따라 달라지는 결과(대안 solve 실패, 시간 한도에 걸린 해)는 캐시하지
    않는다(통합 리뷰 N2·SHOULD-2). 끝까지 푼 해가 조건(품질·미충원 등)으로 덜 나온 묶음은 캐시한다."""
    import api.routes.optimize as route
    from core.optimize.types import AssignEntry, PlanAssignment
    plan_a = PlanAssignment(entries=[AssignEntry(person_id="p000", project_id="j00", alloc=0.5)],
                            objective=1.0, unfilled=[], violations=[], label="A")

    def fake(graph, S, C, params, n, outcome=None):
        yield plan_a
        outcome.update(state)

    monkeypatch.setattr(route, "generate_plans_streaming", fake)
    cache = small_graph_client.app.state.cache
    for state, cached in (({"stop_reason": "solver_failed"}, False),
                          ({"stop_reason": "quality", "time_limited": True}, False),
                          ({"stop_reason": "quality"}, True)):
        cache._store.clear()
        with small_graph_client.stream("POST", "/api/optimize",
                                        json={"weights": {"Python": 2}, "n_alternatives": 3}) as res:
            list(res.iter_lines())
        assert (len(cache._store) == 1) is cached, state
