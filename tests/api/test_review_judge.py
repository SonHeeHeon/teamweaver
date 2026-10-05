"""리뷰 글 판정 방식 선택(사용자 결정 2026-10-06): 규칙 기반(기본) / Jev.

실제 Jev API는 부르지 않는다 -- httpx.MockTransport로 응답을 만들거나 판정 함수를 바꿔 끼운다.
(lifespan의 load_env가 .env의 실제 키를 읽어도 외부 호출이 나가지 않게 한다.)"""
import json

import httpx
import pytest

import api.review_judge as rj
from api.datasets import build_active
from api.review_judge import JevJudgeError, judge_reviews, judged_version, to_polarity
from api.settings import NON_SOLVER_FIELDS, PlacementSettings
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based

FAKE_KEY = "test-key-not-real"


def _answer(level: int) -> dict:
    probs = {str(i): (1.0 if i == level else 0.0) for i in range(5)}
    return {"answers": {"polarity": {"type": "score", "score": float(level), "probabilities": probs}},
            "model": "jev-test", "usage": {"input_tokens": 10}}


def _transport(level_for, calls: list, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        if status != 200:
            return httpx.Response(status, json={"error": "x"})
        return httpx.Response(200, json=_answer(level_for(body["state"])))
    return httpx.MockTransport(handler)


@pytest.fixture
def small():
    ds = generate_dataset(10, 2, seed=3)
    return ds, parse_reviews_rule_based(ds.reviews)


# --- 척도 변환 ---------------------------------------------------------------

def test_score_levels_map_to_polarity_range():
    assert to_polarity(_answer(0)["answers"]["polarity"]) == -1.0
    assert to_polarity(_answer(2)["answers"]["polarity"]) == 0.0
    assert to_polarity(_answer(4)["answers"]["polarity"]) == 1.0
    # 확률이 나뉘어 있으면 기댓값(0.5×3 + 0.5×4 = 3.5 → 0.75)
    mixed = {"score": 3.5, "probabilities": {"0": 0, "1": 0, "2": 0, "3": 0.5, "4": 0.5}}
    assert to_polarity(mixed) == pytest.approx(0.75)


@pytest.mark.parametrize("answer", [{"score": 7.0}, {"score": 0.5, "probabilities": {str(i): (1.0 if i == 4 else 0.0)
                                                                                          for i in range(5)}},
                                    {"nope": 1},
                                    {"score": 2.0, "probabilities": {str(i): 0.5 for i in range(5)}}])
def test_unexpected_scale_or_shape_stops(answer):
    with pytest.raises(JevJudgeError):
        to_polarity(answer)


# --- 판정·캐시 ---------------------------------------------------------------

def test_judge_replaces_only_polarity_and_caches(small, tmp_path):
    ds, parsed = small
    calls: list = []
    cache = tmp_path / "jev.json"
    out = judge_reviews(ds, parsed, cache_path=cache, key=FAKE_KEY, transport=_transport(lambda s: 4, calls))
    assert len(calls) == len({rj._state(r) for r in ds.reviews}) > 0
    assert all(p.text_polarity == 1.0 for p in out)
    assert [p.evidence for p in out] == [p.evidence for p in parsed]          # 근거 문장은 그대로
    assert [(p.reviewer_id, p.reviewee_id) for p in out] == [(p.reviewer_id, p.reviewee_id) for p in parsed]
    # 캐시에는 해시와 숫자만 -- 원문·키가 없다
    raw = cache.read_text("utf-8")
    assert FAKE_KEY not in raw and ds.reviews[0].positive.text not in raw
    # 같은 글은 다시 부르지 않는다
    again: list = []
    out2 = judge_reviews(ds, parsed, cache_path=cache, key=FAKE_KEY, transport=_transport(lambda s: 0, again))
    assert again == [] and [p.text_polarity for p in out2] == [p.text_polarity for p in out]


def test_rejected_key_raises_without_leaking_the_key(small, tmp_path):
    ds, parsed = small
    with pytest.raises(JevJudgeError) as exc:
        judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", key=FAKE_KEY,
                      transport=_transport(lambda s: 2, [], status=401))
    assert "401" in str(exc.value) and FAKE_KEY not in str(exc.value)


def test_missing_key_and_misaligned_reviews_are_errors(small, tmp_path, monkeypatch):
    ds, parsed = small
    monkeypatch.delenv(rj.KEY_ENV, raising=False)
    with pytest.raises(JevJudgeError, match=rj.KEY_ENV):
        judge_reviews(ds, parsed, cache_path=tmp_path / "c.json")
    with pytest.raises(JevJudgeError, match="순서"):
        judge_reviews(ds, parsed[::-1] if len(parsed) > 1 else [], cache_path=tmp_path / "c.json", key=FAKE_KEY)


def test_partial_failure_keeps_received_judgments_in_cache(small, tmp_path):
    ds, parsed = small
    first = rj._state(ds.reviews[0])

    def handler(request):
        state = json.loads(request.content)["state"]
        return httpx.Response(200, json=_answer(3)) if state == first else httpx.Response(400, json={})
    cache = tmp_path / "c.json"
    with pytest.raises(JevJudgeError):
        judge_reviews(ds, parsed, cache_path=cache, key=FAKE_KEY, transport=httpx.MockTransport(handler), workers=1)
    # 작업자 1명이면 요청이 순서대로 끝난다: 첫 글은 받았고 두 번째에서 실패 -- 받은 것만 캐시에 남는다.
    assert json.loads(cache.read_text("utf-8")) == {rj._cache_key(first, rj.MODEL): 0.5}


# --- 데이터셋 만들기 ----------------------------------------------------------

def test_build_active_with_jev_changes_version_but_keeps_content_version(small, tmp_path, monkeypatch):
    ds, parsed = small
    monkeypatch.setattr(rj, "judge_reviews", lambda ds, parsed, **kw: [p.model_copy(update={"text_polarity": 1.0})
                                                                      for p in parsed])
    rule = build_active(ds, parsed, dataset_id="d", version="a" * 64, source="fixture", synthetic=True)
    jev = build_active(ds, parsed, dataset_id="d", version="a" * 64, source="fixture", synthetic=True,
                       review_judge="jev", judge_cache=tmp_path / "c.json")
    assert rule.info.version == rule.info.content_version == "a" * 64 and rule.info.review_judge == "rule"
    judged = [p.model_copy(update={"text_polarity": 1.0}) for p in parsed]
    assert jev.info.version == judged_version("a" * 64, "jev", judged) != "a" * 64
    assert jev.info.content_version == "a" * 64 and jev.info.review_judge == "jev" and jev.info.judge_error is None


def test_build_active_falls_back_to_rule_when_jev_fails(small, tmp_path, monkeypatch):
    ds, parsed = small

    def boom(*a, **k):
        raise JevJudgeError("Jev API가 키를 거절했다(HTTP 401)")
    monkeypatch.setattr(rj, "judge_reviews", boom)
    out = build_active(ds, parsed, dataset_id="d", version="b" * 64, source="fixture", synthetic=True,
                       review_judge="jev", judge_cache=tmp_path / "c.json")
    assert out.info.review_judge == "rule" and out.info.version == "b" * 64
    assert "규칙 기반" in out.info.judge_error and "401" in out.info.judge_error


def test_review_judge_is_not_a_solver_parameter():
    assert "review_judge" in NON_SOLVER_FIELDS
    assert PlacementSettings(review_judge="jev").to_milp_params() == PlacementSettings().to_milp_params()


# --- 설정 화면 → 데이터셋 다시 만들기 ----------------------------------------

def _put(client, **over):
    cur = client.get("/api/settings").json()
    return client.put("/api/settings", json={"settings": {**cur["settings"], **over}, "based_on": cur["updated_at"]})


@pytest.fixture
def fake_jev(monkeypatch):
    calls = []
    monkeypatch.setattr(rj, "api_key", lambda: FAKE_KEY)
    monkeypatch.setattr("api.routes.settings.api_key", lambda: FAKE_KEY)

    def fake(ds, parsed, **kw):
        calls.append(len(parsed))
        return [p.model_copy(update={"text_polarity": 1.0}) for p in parsed]
    monkeypatch.setattr(rj, "judge_reviews", fake)
    return calls


def test_choosing_jev_without_a_key_is_422(client, monkeypatch):
    monkeypatch.setattr("api.routes.settings.api_key", lambda: None)
    assert client.get("/api/settings").json()["jev_available"] is False
    res = _put(client, review_judge="jev")
    assert res.status_code == 422 and rj.KEY_ENV in res.json()["detail"]
    assert client.get("/api/settings").json()["settings"]["review_judge"] == "rule"


def test_switching_judge_rebuilds_the_active_dataset_and_back(client, fake_jev):
    before = client.get("/api/datasets/active").json()
    assert before["review_judge"] == "rule"
    res = _put(client, review_judge="jev")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["settings"]["review_judge"] == "jev" and body["jev_available"] is True
    assert body["dataset"]["review_judge"] == "jev" and body["dataset"]["version"] != before["version"]
    assert body["dataset"]["content_version"] == before["content_version"]
    assert fake_jev, "판정 함수가 불려야 한다"
    now = client.get("/api/datasets/active").json()
    assert now["version"] == body["dataset"]["version"]
    assert client.get("/api/meta").json()["dataset_version"] == now["version"]
    # 되돌리면 원래 버전 그대로(캐시·저장 교체 호환)
    back = _put(client, review_judge="rule").json()
    assert back["dataset"]["version"] == before["version"] and back["dataset"]["review_judge"] == "rule"


def test_other_settings_changes_do_not_rebuild(client, fake_jev):
    before = client.get("/api/datasets/active").json()
    res = _put(client, min_alloc=0.4)
    assert res.status_code == 200 and res.json()["dataset"] is None
    assert client.get("/api/datasets/active").json()["activated_at"] == before["activated_at"]
    assert fake_jev == []


def test_screen_without_judge_field_keeps_current_judge(client, fake_jev):
    assert _put(client, review_judge="jev").status_code == 200
    cur = client.get("/api/settings").json()
    legacy = {k: v for k, v in cur["settings"].items() if k != "review_judge"}
    res = client.put("/api/settings", json={"settings": {**legacy, "min_alloc": 0.35}, "based_on": cur["updated_at"]})
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["review_judge"] == "jev" and res.json()["dataset"] is None


def test_stale_screen_conflict_is_checked_before_rebuilding(client, fake_jev):
    cur = client.get("/api/settings").json()
    assert _put(client, min_alloc=0.4).status_code == 200
    res = client.put("/api/settings", json={"settings": {**cur["settings"], "review_judge": "jev"},
                                            "based_on": cur["updated_at"]})
    assert res.status_code == 409 and fake_jev == []


def test_uploaded_data_is_rebuilt_from_the_saved_bundle(client, fake_jev, tmp_path):
    from core.ingest.synthetic import generate_bundle
    from tests.api.test_datasets import ZIP, _zip_dir
    up = client.post("/api/datasets", content=_zip_dir(generate_bundle(tmp_path / "b", 12, 3, 5)), headers=ZIP).json()
    res = _put(client, review_judge="jev")
    assert res.status_code == 200, res.text
    info = res.json()["dataset"]
    assert info["source"] == "upload" and info["review_judge"] == "jev"
    assert info["content_version"] == up["dataset"]["version"] != info["version"]
    # 재기동해도 같은 묶음·같은 판정으로 복원된다(저장은 원천 해시 기준)
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as again:
        restored = again.get("/api/datasets/active").json()
    assert restored["source"] == "upload" and restored["version"] == info["version"]
    assert restored["restore_error"] is None


def test_jev_failure_on_switch_saves_choice_but_reports_rule_fallback(client, monkeypatch):
    monkeypatch.setattr("api.routes.settings.api_key", lambda: FAKE_KEY)

    def boom(*a, **k):
        raise JevJudgeError("Jev API에 연결하지 못했다: ConnectError")
    monkeypatch.setattr(rj, "judge_reviews", boom)
    before = client.get("/api/datasets/active").json()
    res = _put(client, review_judge="jev").json()
    assert res["settings"]["review_judge"] == "jev"
    assert res["dataset"]["review_judge"] == "rule" and res["dataset"]["version"] == before["version"]
    assert "ConnectError" in client.get("/api/datasets/active").json()["judge_error"]


# --- 리뷰 반영(적대적 리뷰 M1·M2·S1~S5) ------------------------------------------

def test_version_depends_on_the_judged_values():
    ds = generate_dataset(6, 2, seed=1)
    parsed = parse_reviews_rule_based(ds.reviews)
    a = [p.model_copy(update={"text_polarity": 0.5}) for p in parsed]
    b = [p.model_copy(update={"text_polarity": 0.25}) for p in parsed]
    # 캐시가 사라져 다시 판정한 값이 다르면 다른 데이터다(서명·저장 교체가 섞이지 않게).
    assert judged_version("v", "jev", a) != judged_version("v", "jev", b)
    assert judged_version("v", "jev", a) == judged_version("v", "jev", list(a))
    assert judged_version("v", "rule", a) == "v"


def test_unwritable_cache_does_not_break_judging_or_building(small, tmp_path, monkeypatch):
    ds, parsed = small
    import api.storage as storage

    def deny(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(storage, "atomic_write", deny)
    out = judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", key=FAKE_KEY, transport=_transport(lambda s: 3, []))
    assert all(p.text_polarity == 0.5 for p in out)


def test_unexpected_error_falls_back_to_rule(small, tmp_path, monkeypatch):
    ds, parsed = small

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rj, "judge_reviews", boom)
    out = build_active(ds, parsed, dataset_id="d", version="c" * 64, source="fixture", synthetic=True,
                       review_judge="jev", judge_cache=tmp_path / "c.json")
    assert out.info.review_judge == "rule" and "OSError" in out.info.judge_error


def test_cache_is_private_and_keeps_only_the_current_data(small, tmp_path):
    ds, parsed = small
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({"stale-hash-of-old-upload": 0.1}), "utf-8")
    judge_reviews(ds, parsed, cache_path=cache, key=FAKE_KEY, transport=_transport(lambda s: 4, []))
    saved = json.loads(cache.read_text("utf-8"))
    assert "stale-hash-of-old-upload" not in saved and len(saved) > 0
    assert (cache.stat().st_mode & 0o777) == 0o600


def test_overall_deadline_stops_waiting(small, tmp_path):
    import threading
    ds, parsed = small
    release = threading.Event()

    def slow(request):
        release.wait(5)
        return httpx.Response(200, json=_answer(2))
    try:
        with pytest.raises(JevJudgeError, match="초 안에 끝나지 않았다"):
            judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", key=FAKE_KEY,
                          transport=httpx.MockTransport(slow), deadline_s=0.2)
    finally:
        release.set()


def test_error_messages_do_not_echo_the_response():
    with pytest.raises(JevJudgeError) as exc:
        to_polarity({"rationale": "김철수는 협업이 어렵다", "nope": 1})
    assert "김철수" not in str(exc.value)


def test_saving_jev_again_after_a_failure_retries(client, monkeypatch):
    monkeypatch.setattr("api.routes.settings.api_key", lambda: FAKE_KEY)
    calls = []

    def flaky(ds, parsed, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise JevJudgeError("Jev API 오류(HTTP 503)")
        return [p.model_copy(update={"text_polarity": 1.0}) for p in parsed]
    monkeypatch.setattr(rj, "judge_reviews", flaky)
    first = _put(client, review_judge="jev").json()
    assert first["dataset"]["review_judge"] == "rule" and "503" in first["dataset"]["judge_error"]
    # 다른 칸만 저장하면 다시 만들지 않는다(저장마다 외부 호출·재구성이 일어나지 않게)
    other = _put(client, review_judge="jev", min_alloc=0.4).json()
    assert len(calls) == 1 and other["dataset"] is None
    cur = client.get("/api/settings").json()
    second = client.put("/api/settings", json={"settings": cur["settings"], "based_on": cur["updated_at"],
                                               "retry_judge": True}).json()    # "판정 다시 시도"
    assert len(calls) == 2 and second["dataset"]["review_judge"] == "jev" and second["dataset"]["judge_error"] is None


def test_missing_key_does_not_block_other_settings_when_jev_is_saved(client, fake_jev, monkeypatch):
    assert _put(client, review_judge="jev").status_code == 200
    monkeypatch.setattr("api.routes.settings.api_key", lambda: None)
    res = _put(client, min_alloc=0.4)
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["min_alloc"] == pytest.approx(0.4)


def test_reset_to_rule_data_clears_the_judgment_cache(client, tmp_path):
    from api.datasets import jev_cache_path
    path = jev_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"x": 0.5}), "utf-8")
    res = client.post("/api/datasets/reset", json={})
    assert res.status_code == 200 and not path.exists()


def test_tests_cannot_reach_the_real_api():
    assert rj.URL.startswith("http://jev.invalid")


def test_rate_limit_retries_follow_retry_after_with_a_cap(small, tmp_path, monkeypatch):
    ds, parsed = small
    ds.reviews[:] = ds.reviews[:1]
    parsed = parsed[:1]
    sleeps, n = [], {"calls": 0}
    monkeypatch.setattr(rj.time, "sleep", lambda s: sleeps.append(s))

    def handler(request):
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(429, headers={"Retry-After": "60"}, json={})
        return httpx.Response(200, json=_answer(4))
    out = judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", key=FAKE_KEY, transport=httpx.MockTransport(handler))
    assert out[0].text_polarity == 1.0 and n["calls"] == 3 and sleeps == [10.0, 10.0]


def test_gives_up_after_three_attempts(small, tmp_path, monkeypatch):
    ds, parsed = small
    ds.reviews[:] = ds.reviews[:1]
    monkeypatch.setattr(rj.time, "sleep", lambda s: None)
    calls = []
    with pytest.raises(JevJudgeError, match="503"):
        judge_reviews(ds, parsed[:1], cache_path=tmp_path / "c.json", key=FAKE_KEY,
                      transport=_transport(lambda s: 2, calls, status=503))
    assert len(calls) == 3


@pytest.mark.parametrize("answer", [{"score": float("nan")}, {"score": True},
                                    {"score": 2.0, "probabilities": {"0": float("nan"), "1": 0, "2": 1, "3": 0, "4": 0}},
                                    {"score": 2.0, "probabilities": {"0": True, "1": 0, "2": 0, "3": 0, "4": 0}}])
def test_non_finite_or_boolean_values_are_rejected(answer):
    with pytest.raises(JevJudgeError):
        to_polarity(answer)


def test_fallback_state_without_a_key_still_saves_other_settings_quickly(client, monkeypatch):
    monkeypatch.setattr("api.routes.settings.api_key", lambda: FAKE_KEY)

    def boom(*a, **k):
        raise JevJudgeError("Jev API 오류(HTTP 503)")
    monkeypatch.setattr(rj, "judge_reviews", boom)
    assert _put(client, review_judge="jev").json()["dataset"]["review_judge"] == "rule"
    monkeypatch.setattr("api.routes.settings.api_key", lambda: None)
    before = client.get("/api/datasets/active").json()["activated_at"]
    res = _put(client, min_alloc=0.45)
    assert res.status_code == 200 and res.json()["dataset"] is None
    assert client.get("/api/datasets/active").json()["activated_at"] == before
