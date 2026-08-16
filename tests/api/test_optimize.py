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
