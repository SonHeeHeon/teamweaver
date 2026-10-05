"""Jev 실험 장치: 기록·재생, 지어내기 금지, 같은 채점 경로. 실제 API는 부르지 않는다(가짜 transport)."""
import json

import httpx
import pytest

from experiments.jev import e1_solver, e3_swap
from experiments.jev.client import JevClient, MissingRecording, choice
from experiments.jev.common import synthetic_instance


def _fake_transport(pick_first=True, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        if calls is not None:
            calls.append(body)
        answers = {}
        for key, q in body["questions"].items():
            if q["type"] == "choice":
                opts = list(q["criteria"])
                answers[key] = {"type": "choice", "choice": opts[0] if pick_first else opts[-1], "confidence": 0.6}
            else:
                answers[key] = {"type": "score", "score": 2.0}
        return httpx.Response(200, json={"model": "jev-test", "answers": answers, "usage": {"input_tokens": 100}})
    return httpx.MockTransport(handler)


def test_records_then_replays_without_key(tmp_path):
    tape = tmp_path / "t.json"
    calls = []
    live = JevClient(tape, api_key="test-key", transport=_fake_transport(calls=calls))
    a = live.ask("state", {"q": choice("pick", {"x": "X", "y": "Y"})})
    assert a.answers["q"]["choice"] == "x" and not a.replayed and len(calls) == 1
    assert "test-key" not in tape.read_text("utf-8")                    # 키는 기록하지 않는다
    replay = JevClient(tape, api_key=None)
    b = replay.ask("state", {"q": choice("pick", {"x": "X", "y": "Y"})})
    assert b.replayed and b.answers == a.answers and b.latency_s == a.latency_s


def test_missing_recording_without_key_is_an_error_not_a_guess(tmp_path, monkeypatch):
    # .env defines TYPESAFE_API_KEY and other tests load it into os.environ (load_env), so remove it here
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(MissingRecording):
        JevClient(tmp_path / "none.json", api_key=None).ask("s", {"q": choice("p", {"x": "X"})})


def test_e1_fill_respects_the_same_rules_for_every_picker(tmp_path):
    inst = synthetic_instance(15, 3, 1)
    client = JevClient(tmp_path / "e1.json", api_key="test-key", transport=_fake_transport())
    res = e1_solver.run(inst, client, seeds=(1,))
    for name in ("milp_plan_a", "skill_best", "jev"):
        assert res["methods"][name]["violations"] == 0, name        # 산수 조건은 코드가 지킨다
    assert res["methods"]["jev"]["calls"] == res["methods"]["jev"]["picked_by_model"]


def test_e1_rejects_a_choice_outside_the_candidates(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"answers": {"pick": {"type": "choice", "choice": "nobody"}}, "usage": {}})
    inst = synthetic_instance(15, 3, 1)
    client = JevClient(tmp_path / "e1.json", api_key="test-key", transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="후보 밖"):
        e1_solver.fill_slots(inst, e1_solver.make_jev_picker(client, []))


def test_e3_ground_truth_ranks_feasible_best_first(tmp_path):
    inst = synthetic_instance(40, 6, 3)
    client = JevClient(tmp_path / "e3.json", api_key="test-key", transport=_fake_transport())
    res = e3_swap.run(inst, client, k=2, seed=1)
    assert res["n_cases"] > 0
    for case in res["cases"]:
        order = e3_swap._ranked(case)
        best = next(c for c in case["candidates"] if c["id"] == order[0])
        assert all(best["violations"] <= c["violations"] or c["violations"] > 0 for c in case["candidates"])
    m = res["methods"]["jev"]
    assert 0 <= m["top1"] <= 1 and 1 <= m["mean_rank"] <= 2
    assert all(c["violations"] == 0 for case in res["cases"] for c in case["candidates"])   # 숫자 조건은 코드가 걸렀다


def test_answer_of_the_wrong_type_is_rejected(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"answers": {"q": {"type": "score", "score": 1.0}}, "usage": {}})
    c = JevClient(tmp_path / "t.json", api_key="test-key", transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="맞지 않는다"):
        c.ask("s", {"q": choice("p", {"x": "X"})})
    assert not (tmp_path / "t.json").exists()                       # 잘못된 답은 기록하지 않는다


def test_e2_score_outside_the_assumed_scale_stops(tmp_path):
    from experiments.jev import e2_reviews

    def handler(request):
        return httpx.Response(200, json={"answers": {"polarity": {"type": "score", "score": 7.0}}, "usage": {}})
    c = JevClient(tmp_path / "t.json", api_key="test-key", transport=httpx.MockTransport(handler))
    from core.config import FIXTURES_DIR
    from core.datagen.fixtures_io import load_fixtures
    ds, _ = load_fixtures(FIXTURES_DIR)
    with pytest.raises(ValueError, match="척도"):
        e2_reviews.judge_with_jev(c, ds.reviews[:1])


def test_e3_tied_best_counts_as_a_hit():
    case = {"candidates": [{"id": "a", "objective": 5.0, "violations": 0},
                           {"id": "b", "objective": 5.0, "violations": 0},
                           {"id": "c", "objective": 4.0, "violations": 0}]}
    s = e3_swap._summary([(case, "b")])
    assert s["top1"] == 1.0 and s["mean_rank"] == 1 and s["mean_regret"] == 0
    assert e3_swap._random_expected([case], 3)["top1"] == pytest.approx(2 / 3)
    assert e3_swap._rank(case, "c") == 3


def _score_transport(score, probs=None):
    def handler(request):
        a = {"type": "score", "score": score}
        if probs is not None:
            a["probabilities"] = probs
        return httpx.Response(200, json={"answers": {"polarity": a}, "usage": {"input_tokens": 1}})
    return httpx.MockTransport(handler)


def _one_review():
    from core.config import FIXTURES_DIR
    from core.datagen.fixtures_io import load_fixtures
    return load_fixtures(FIXTURES_DIR)[0].reviews[:1]


def test_e2_uses_the_probability_expectation(tmp_path):
    from experiments.jev import e2_reviews
    probs = {"0": 0.0, "1": 0.0, "2": 0.2, "3": 0.8, "4": 0.0}           # 기댓값 2.8
    c = JevClient(tmp_path / "t.json", api_key="test-key", transport=_score_transport(3.0, probs))
    pol, log = e2_reviews.judge_with_jev(c, _one_review())
    assert pol[0] == pytest.approx(2.8 / 4 * 2 - 1)


def test_e2_falls_back_to_score_without_probabilities(tmp_path):
    from experiments.jev import e2_reviews
    c = JevClient(tmp_path / "t.json", api_key="test-key", transport=_score_transport(1.0))
    pol, _ = e2_reviews.judge_with_jev(c, _one_review())
    assert pol[0] == pytest.approx(-0.5)


def test_e2_score_and_probabilities_on_different_scales_stop(tmp_path):
    from experiments.jev import e2_reviews
    probs = {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0}           # 기댓값 4, score 0.9(0..1 척도로 보임)
    c = JevClient(tmp_path / "t.json", api_key="test-key", transport=_score_transport(0.9, probs))
    with pytest.raises(ValueError, match="척도"):
        e2_reviews.judge_with_jev(c, _one_review())
