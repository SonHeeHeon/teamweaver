"""리뷰 글 판정 LLM 통일(사용자 결정 2026-10-06): CSV 묶음의 평가 사유를 LLM이 읽어 글 극성을 매긴다.

실제 LLM은 부르지 않는다 -- conftest가 서비스 경로의 판정을 가짜로 바꾸고, 여기서는 원래 함수(import 시점에 잡은
judge_reviews)를 httpx.MockTransport로 시험한다."""
import json
import threading

import httpx
import pytest

import api.review_judge as rj
from api.datasets import build_active
from api.review_judge import JudgeError, judge_reviews, judged_version, to_polarity
from api.settings import PlacementSettings, SettingsStore
from core.datagen.generator import generate_dataset
from core.datagen.parse_reviews import parse_reviews_rule_based

FAKE_KEY = "test-key-not-real"
URL = "http://llm.test/v1"


def _completion(pol) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"text_polarity": pol, "evidence": []})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _transport(pol_for, calls: list, status: int = 200, key: str | None = FAKE_KEY):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path == "/v1/chat/completions"
        if key:
            assert request.headers["Authorization"] == f"Bearer {key}"
        else:
            assert "Authorization" not in request.headers
        if status != 200:
            return httpx.Response(status, json={"error": "x"})
        user = json.loads(body["messages"][1]["content"])
        return httpx.Response(200, json=_completion(pol_for(user)))
    return httpx.MockTransport(handler)


@pytest.fixture
def small():
    ds = generate_dataset(10, 2, seed=3)
    return ds, parse_reviews_rule_based(ds.reviews)


def _judge(ds, parsed, tmp_path, transport, **kw):
    return judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", key=kw.pop("key", FAKE_KEY), url=URL,
                         model_name="m-test", transport=transport, **kw)


# --- 응답 해석 ------------------------------------------------------------------

def test_polarity_is_read_and_clamped():
    assert to_polarity('{"text_polarity": -0.5}') == -0.5
    assert to_polarity('{"text_polarity": 3}') == 1.0


@pytest.mark.parametrize("content", ['not json', '[]', '{"text_polarity": "높음"}', '{"text_polarity": true}',
                                     '{"text_polarity": NaN}', '{"evidence": ["김철수는 협업이 어렵다"]}'])
def test_bad_responses_stop_without_echoing_them(content):
    with pytest.raises(JudgeError) as exc:
        to_polarity(content)
    assert "김철수" not in str(exc.value)


# --- 판정·캐시 ---------------------------------------------------------------

def test_judge_sends_the_same_instruction_and_replaces_only_polarity(small, tmp_path):
    ds, parsed = small
    calls: list = []
    out = _judge(ds, parsed, tmp_path, _transport(lambda u: -0.4, calls))
    assert calls and calls[0]["messages"][0]["content"] == rj.INSTRUCTION
    assert calls[0]["model"] == "m-test" and calls[0]["response_format"] == {"type": "json_object"}
    assert all(p.text_polarity == -0.4 for p in out)
    assert [p.evidence for p in out] == [p.evidence for p in parsed]
    raw = (tmp_path / "c.json").read_text("utf-8")
    assert FAKE_KEY not in raw and ds.reviews[0].positive.text not in raw
    assert ((tmp_path / "c.json").stat().st_mode & 0o777) == 0o600
    again: list = []
    _judge(ds, parsed, tmp_path, _transport(lambda u: 0.9, again))
    assert again == []                                            # 같은 글은 다시 부르지 않는다


def test_on_prem_llm_without_a_key_sends_no_auth_header(small, tmp_path, monkeypatch):
    ds, parsed = small
    monkeypatch.delenv(rj.KEY_ENV, raising=False)
    out = _judge(ds, parsed, tmp_path, _transport(lambda u: 0.2, [], key=None), key=None)
    assert out[0].text_polarity == 0.2


def test_openai_without_a_key_is_an_error(small, tmp_path, monkeypatch):
    ds, parsed = small
    monkeypatch.delenv(rj.KEY_ENV, raising=False)
    with pytest.raises(JudgeError, match=rj.KEY_ENV):
        judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", url=rj.DEFAULT_BASE_URL)


def test_rejected_key_and_misaligned_reviews(small, tmp_path):
    ds, parsed = small
    with pytest.raises(JudgeError) as exc:
        _judge(ds, parsed, tmp_path, _transport(lambda u: 0, [], status=401))
    assert "401" in str(exc.value) and FAKE_KEY not in str(exc.value)
    with pytest.raises(JudgeError, match="순서"):
        _judge(ds, parsed[::-1], tmp_path, _transport(lambda u: 0, []))


def test_rate_limit_follows_retry_after_with_a_cap(small, tmp_path, monkeypatch):
    ds, parsed = small
    ds.reviews[:] = ds.reviews[:1]
    sleeps, n = [], {"calls": 0}
    monkeypatch.setattr(rj, "_wait", lambda s, stop: sleeps.append(s))

    def handler(request):
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(429, headers={"Retry-After": "60"}, json={})
        return httpx.Response(200, json=_completion(0.5))
    out = _judge(ds, parsed[:1], tmp_path, httpx.MockTransport(handler))
    assert out[0].text_polarity == 0.5 and n["calls"] == 3 and sleeps == [rj.MAX_WAIT_S, rj.MAX_WAIT_S]


def test_openai_retry_after_ms_header_is_followed(small, tmp_path, monkeypatch):
    ds, parsed = small
    ds.reviews[:] = ds.reviews[:1]
    sleeps, n = [], {"calls": 0}
    monkeypatch.setattr(rj, "_wait", lambda s, stop: sleeps.append(s))

    def handler(request):
        n["calls"] += 1
        if n["calls"] < 5:
            return httpx.Response(429, headers={"retry-after-ms": "350"}, json={})
        return httpx.Response(200, json=_completion(-0.2))
    out = _judge(ds, parsed[:1], tmp_path, httpx.MockTransport(handler))
    assert out[0].text_polarity == -0.2 and sleeps == [0.35] * 4


def test_partial_failure_keeps_received_judgments(small, tmp_path):
    ds, parsed = small
    first = ds.reviews[0].positive.text

    def handler(request):
        user = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json=_completion(0.3)) if user["좋은점"] == first else httpx.Response(400, json={})
    with pytest.raises(JudgeError):
        _judge(ds, parsed, tmp_path, httpx.MockTransport(handler), workers=1)
    assert list(json.loads((tmp_path / "c.json").read_text("utf-8")).values()) == [0.3]


def test_overall_deadline_stops_waiting(small, tmp_path):
    ds, parsed = small
    release = threading.Event()

    def slow(request):
        release.wait(5)
        return httpx.Response(200, json=_completion(0.0))
    try:
        with pytest.raises(JudgeError, match="초 안에 끝나지 않았다"):
            _judge(ds, parsed, tmp_path, httpx.MockTransport(slow), deadline_s=0.2)
    finally:
        release.set()


def test_unwritable_cache_does_not_break_judging(small, tmp_path, monkeypatch):
    ds, parsed = small
    import api.storage as storage
    monkeypatch.setattr(storage, "atomic_write", lambda *a, **k: (_ for _ in ()).throw(PermissionError("ro")))
    assert _judge(ds, parsed, tmp_path, _transport(lambda u: 0.1, []))[0].text_polarity == 0.1


def test_cache_keeps_only_the_current_data(small, tmp_path):
    ds, parsed = small
    (tmp_path / "c.json").write_text(json.dumps({"old-upload-hash": 0.1}), "utf-8")
    _judge(ds, parsed, tmp_path, _transport(lambda u: 0.7, []))
    saved = json.loads((tmp_path / "c.json").read_text("utf-8"))
    assert "old-upload-hash" not in saved and saved


# --- 데이터셋 만들기 ----------------------------------------------------------

def test_version_depends_on_model_and_judged_values(small):
    _, parsed = small
    a = [p.model_copy(update={"text_polarity": 0.5}) for p in parsed]
    b = [p.model_copy(update={"text_polarity": 0.25}) for p in parsed]
    assert judged_version("v", "m", a) != judged_version("v", "m", b)
    assert judged_version("v", "m", a) != judged_version("v", "m2", a)
    assert judged_version("v", "m", a) == judged_version("v", "m", list(a))


def test_bundles_are_judged_and_fixture_is_not(small, tmp_path, monkeypatch):
    ds, parsed = small
    seen = []
    monkeypatch.setattr(rj, "judge_reviews",
                        lambda ds, parsed, **kw: seen.append(1) or [p.model_copy(update={"text_polarity": -1.0})
                                                                    for p in parsed])
    up = build_active(ds, parsed, dataset_id="d", version="a" * 64, source="upload", synthetic=True)
    assert up.info.review_judge == "llm" and up.info.content_version == "a" * 64 != up.info.version
    assert up.info.judge_host == "llm.invalid" and up.info.judge_location == "unknown" and up.info.judge_model
    fx = build_active(ds, parsed, dataset_id="f", version="b" * 64, source="fixture", synthetic=True, judge=False)
    assert fx.info.review_judge == "fixture" and fx.info.version == "b" * 64 and len(seen) == 1


@pytest.mark.parametrize("exc", [JudgeError("LLM API 오류(HTTP 503)"), OSError("disk full")])
def test_judge_failure_falls_back_to_items(small, monkeypatch, exc):
    ds, parsed = small

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(rj, "judge_reviews", boom)
    out = build_active(ds, parsed, dataset_id="d", version="c" * 64, source="upload", synthetic=True)
    assert out.info.review_judge == "items" and out.info.version == "c" * 64
    assert "항목 점수" in out.info.judge_error


@pytest.mark.parametrize("url,location", [
    (None, "openai"), ("https://api.openai.com", "openai"), ("http://llm.corp.local:8000/v1", "onprem"), ("http://10.1.2.3:8000/v1", "onprem"),
    ("http://localhost:11434/v1", "onprem"), ("https://openrouter.ai/api/v1", "unknown"),
    ("https://my.openai.azure.com/v1", "unknown")])
def test_endpoint_location_is_never_assumed_internal(monkeypatch, url, location):
    if url is None:
        monkeypatch.delenv(rj.BASE_URL_ENV, raising=False)
    else:
        monkeypatch.setenv(rj.BASE_URL_ENV, url)
    ep = rj.endpoint()
    assert ep["location"] == location and ep["external"] is (location != "onprem")


def test_admin_can_declare_an_address_on_prem_and_choose_the_model(monkeypatch):
    monkeypatch.setenv(rj.BASE_URL_ENV, "https://llm.mycompany.com/v1")
    assert rj.endpoint()["location"] == "unknown"
    monkeypatch.setenv(rj.ONPREM_ENV, "1")
    monkeypatch.setenv(rj.MODEL_ENV, "qwen-internal")
    assert rj.endpoint()["location"] == "onprem" and rj.endpoint()["model"] == "qwen-internal"


def test_openai_key_is_not_sent_to_other_addresses(monkeypatch):
    monkeypatch.setenv(rj.KEY_ENV, "sk-openai")
    monkeypatch.delenv(rj.OTHER_KEY_ENV, raising=False)
    assert rj.api_key(rj.DEFAULT_BASE_URL) == "sk-openai"
    assert rj.api_key("http://llm.corp.local/v1") is None
    monkeypatch.setenv(rj.OTHER_KEY_ENV, "corp-key")
    assert rj.api_key("http://llm.corp.local/v1") == "corp-key"


def test_real_data_is_not_sent_outside_without_consent(small, monkeypatch):
    ds, parsed = small
    seen = []
    monkeypatch.setattr(rj, "judge_reviews", lambda ds, parsed, **kw: seen.append(1) or list(parsed))
    monkeypatch.delenv(rj.BASE_URL_ENV, raising=False)               # OpenAI
    out = build_active(ds, parsed, dataset_id="d", version="d" * 64, source="upload", synthetic=False)
    assert out.info.review_judge == "blocked" and seen == [] and rj.ALLOW_EXTERNAL_ENV in out.info.judge_error
    monkeypatch.setenv(rj.ALLOW_EXTERNAL_ENV, "1")
    assert build_active(ds, parsed, dataset_id="d", version="d" * 64, source="upload",
                        synthetic=False).info.review_judge == "llm"
    monkeypatch.delenv(rj.ALLOW_EXTERNAL_ENV)
    monkeypatch.setenv(rj.BASE_URL_ENV, "http://10.0.0.5:8000/v1")   # 사내 주소면 동의 없이도 보낸다
    assert build_active(ds, parsed, dataset_id="d", version="d" * 64, source="upload",
                        synthetic=None).info.review_judge == "llm"


def test_a_malformed_answer_is_asked_again(small, tmp_path):
    ds, parsed = small
    ds.reviews[:] = ds.reviews[:1]
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
        return httpx.Response(200, json=_completion(0.4))
    assert _judge(ds, parsed[:1], tmp_path, httpx.MockTransport(handler))[0].text_polarity == 0.4 and n["calls"] == 2


def test_http_error_code_is_reported_without_the_body(small, tmp_path):
    ds, parsed = small

    def handler(request):
        return httpx.Response(404, json={"error": {"code": "model_not_found", "message": "secret text"}})
    with pytest.raises(JudgeError) as exc:
        _judge(ds, parsed, tmp_path, httpx.MockTransport(handler))
    assert "model_not_found" in str(exc.value) and "secret" not in str(exc.value)


def test_fully_cached_data_needs_no_key(small, tmp_path, monkeypatch):
    ds, parsed = small
    judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", key=FAKE_KEY, url=rj.DEFAULT_BASE_URL,
                  model_name="m-test", transport=_transport(lambda u: 0.3, []))
    monkeypatch.delenv(rj.KEY_ENV, raising=False)
    out = judge_reviews(ds, parsed, cache_path=tmp_path / "c.json", url=rj.DEFAULT_BASE_URL, model_name="m-test")
    assert out[0].text_polarity == 0.3


# --- 설정 호환·다시 판정 ---------------------------------------------------------

def test_removed_judge_setting_is_ignored_in_files_and_requests(tmp_path, client):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"settings": {"time_limit": 200, "review_judge": "jev"}}), "utf-8")
    state = SettingsStore(path).current()
    assert state.load_error is None and state.settings.time_limit == 200
    cur = client.get("/api/settings").json()
    assert "review_judge" not in cur["settings"]
    res = client.put("/api/settings", json={"settings": {**cur["settings"], "review_judge": "jev"},
                                            "based_on": cur["updated_at"]})
    assert res.status_code == 200, res.text
    assert PlacementSettings(review_judge="rule").to_milp_params() == PlacementSettings().to_milp_params()


def test_rejudge_retries_the_llm_on_the_same_upload(client, tmp_path, monkeypatch):
    from core.ingest.synthetic import generate_bundle
    from tests.api.test_datasets import ZIP, _zip_dir
    calls = []

    def flaky(ds, parsed, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise JudgeError("LLM API 오류(HTTP 503)")
        return [p.model_copy(update={"text_polarity": 0.5}) for p in parsed]
    monkeypatch.setattr(rj, "judge_reviews", flaky)
    up = client.post("/api/datasets", content=_zip_dir(generate_bundle(tmp_path / "b", 12, 3, 5)), headers=ZIP).json()
    assert up["dataset"]["review_judge"] == "items" and "503" in up["dataset"]["judge_error"]
    res = client.post("/api/datasets/rejudge", json={})
    assert res.status_code == 200, res.text
    info = res.json()
    assert info["review_judge"] == "llm" and info["judge_error"] is None
    assert info["content_version"] == up["dataset"]["content_version"] and info["version"] != up["dataset"]["version"]
    assert client.get("/api/meta").json()["dataset_version"] == info["version"]
    assert client.post("/api/datasets/rejudge", content=b"", headers={"Content-Type": "text/plain"}).status_code == 415


def test_rejudge_on_fixture_rebuilds_without_calling(client, monkeypatch):
    before = client.get("/api/datasets/active").json()
    assert before["review_judge"] == "fixture"
    res = client.post("/api/datasets/rejudge", json={})
    assert res.status_code == 200 and res.json()["version"] == before["version"]


def test_reset_to_fixture_clears_the_judgment_cache(client):
    from api.datasets import judge_cache_path
    path = judge_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"x": 0.5}), "utf-8")
    assert client.post("/api/datasets/reset", json={}).status_code == 200 and not path.exists()


def test_tests_cannot_reach_a_real_llm():
    assert rj.base_url().startswith("http://llm.invalid")



# --- 서비스 경로(실제 판정 함수 + 가짜 응답) -------------------------------------------

def _service_judge(monkeypatch, handler):
    """서비스가 부르는 rj.judge_reviews를 '원래 함수 + MockTransport'로 바꾼다(conftest의 가짜 대신)."""
    t = httpx.MockTransport(handler)
    monkeypatch.setattr(rj, "judge_reviews", lambda ds, parsed, **kw: judge_reviews(
        ds, parsed, key=FAKE_KEY, url=URL, model_name="m-test", transport=t, workers=1, **kw))


def _upload(client, tmp_path):
    from core.ingest.synthetic import generate_bundle
    from tests.api.test_datasets import ZIP, _zip_dir
    return client.post("/api/datasets", content=_zip_dir(generate_bundle(tmp_path / "b", 12, 3, 5)), headers=ZIP).json()


def test_partial_judgments_survive_a_failure_and_rejudge_asks_only_the_rest(client, tmp_path, monkeypatch):
    calls = {"n": 0, "fail_after": 5}

    def handler(request):
        calls["n"] += 1
        if calls["fail_after"] is not None and calls["n"] > calls["fail_after"]:
            return httpx.Response(401, json={})
        return httpx.Response(200, json=_completion(0.25))
    _service_judge(monkeypatch, handler)
    up = _upload(client, tmp_path)
    assert up["dataset"]["review_judge"] == "items"
    from api.datasets import judge_cache_path
    assert len(json.loads(judge_cache_path(True).read_text("utf-8"))) == 5      # 받은 5건은 남는다(리뷰 M1)
    calls.update(n=0, fail_after=None)
    info = client.post("/api/datasets/rejudge", json={}).json()
    assert info["review_judge"] == "llm"
    unique = len(set(json.loads(judge_cache_path(True).read_text("utf-8"))))
    assert calls["n"] == unique - 5                                          # 남은 건만 불렀다


def test_switch_responses_carry_the_judge_endpoint(client, tmp_path):
    up = _upload(client, tmp_path)
    assert up["dataset"]["judge_endpoint"]["host"] == "llm.invalid"
    assert client.post("/api/datasets/reset", json={}).json()["judge_endpoint"]["location"] == "unknown"
    assert client.post("/api/datasets/rejudge", json={}).json()["judge_endpoint"]


def test_rejudge_prunes_edits_of_the_old_version(client, tmp_path, monkeypatch):
    calls = {"fail": True}

    def flaky(ds, parsed, **kw):
        if calls["fail"]:
            raise JudgeError("LLM API 오류(HTTP 503)")
        return [p.model_copy(update={"text_polarity": 0.5}) for p in parsed]
    monkeypatch.setattr(rj, "judge_reviews", flaky)
    _upload(client, tmp_path)
    pruned = []
    store = client.app.state.plan_edit_store
    monkeypatch.setattr(store, "prune", lambda v: pruned.append(v))
    calls["fail"] = False
    info = client.post("/api/datasets/rejudge", json={}).json()
    assert pruned == [info["version"]]


def test_rejudge_requires_admin_and_refuses_while_switching(client, monkeypatch):
    client.app.state.dataset_switching = True
    try:
        assert client.post("/api/datasets/rejudge", json={}).status_code == 409
    finally:
        client.app.state.dataset_switching = False
    monkeypatch.setenv("TEAMWEAVER_ADMIN_TOKEN", "t0ken")
    assert client.post("/api/datasets/rejudge", json={}).status_code == 401



def test_judge_address_is_separate_from_the_openai_sdk_variable():
    # openai SDK는 OPENAI_BASE_URL을 스스로 읽는다 -- 판정 주소를 그것으로 두면 브리핑 키가 사내 서버로 간다(리뷰 S-1).
    assert rj.BASE_URL_ENV != "OPENAI_BASE_URL"


def test_endpoint_reports_the_external_consent(monkeypatch):
    monkeypatch.delenv(rj.ALLOW_EXTERNAL_ENV, raising=False)
    assert rj.endpoint()["external_allowed"] is False
    monkeypatch.setenv(rj.ALLOW_EXTERNAL_ENV, "1")
    assert rj.endpoint()["external_allowed"] is True


def test_synthetic_judgments_are_kept_apart_and_never_trimmed(small, tmp_path, monkeypatch):
    ds, parsed = small
    seen = {}
    monkeypatch.setattr(rj, "judge_reviews", lambda ds, parsed, **kw: seen.update(kw) or list(parsed))
    from api.datasets import judge_cache_path
    build_active(ds, parsed, dataset_id="demo", version="e" * 64, source="demo-bundle", synthetic=True)
    assert seen["cache_path"] == judge_cache_path(True) != judge_cache_path(False) and seen["trim"] is False
    build_active(ds, parsed, dataset_id="up", version="f" * 64, source="upload", synthetic=None)
    # 실데이터 + 확인 안 된 주소 → 판정 안 함(blocked). 사내 주소면 실데이터 캐시로, 줄여서.
    monkeypatch.setenv(rj.BASE_URL_ENV, "http://10.0.0.9/v1")
    build_active(ds, parsed, dataset_id="up", version="f" * 64, source="upload", synthetic=None)
    assert seen["cache_path"] == judge_cache_path(False) and seen["trim"] is True


def test_untrimmed_cache_keeps_other_data(small, tmp_path):
    ds, parsed = small
    (tmp_path / "c.json").write_text(json.dumps({"other-demo-hash": 0.1}), "utf-8")
    _judge(ds, parsed, tmp_path, _transport(lambda u: 0.7, []), trim=False)
    assert "other-demo-hash" in json.loads((tmp_path / "c.json").read_text("utf-8"))
