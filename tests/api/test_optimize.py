"""SSE 플러밍 테스트. small_graph_client(15명/3프로젝트 합성 데이터셋)를 써서
실제 CBC를 풀되 1초 미만으로 끝낸다 -- 동결 fixture(100명/20프로젝트) 규모의
진짜 흐름 검증은 slow 마커가 붙은 Task 8의 E2E 테스트가 맡는다."""
import json


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
