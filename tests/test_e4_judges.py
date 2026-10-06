"""실험 E4 판정기 비교 스크립트의 오프라인 시험(API를 부르지 않는다): 치명 오류 판별, 부분 결과·이어 실행, 지문, 통계."""
import json

import httpx
import pytest

import experiments.jev.e4_judges as e4
from core.domain.models import PeerReview, ReviewSection


def _reviews(n=4):
    return [PeerReview(reviewer_id="a", reviewee_id=f"b{i}", positive=ReviewSection(items=["x"], text=f"좋음{i}"),
                       negative=ReviewSection(items=["y"], text=f"아쉬움{i}")) for i in range(n)]


class _Err(Exception):
    def __init__(self, status=None, body=None, msg="x"):
        super().__init__(msg)
        self.status_code, self.body = status, body


def test_fatal_errors_are_classified_by_structure():
    assert e4._is_fatal(_Err(429, {"error": {"code": "1113"}}))           # Z.ai 잔액 부족
    assert e4._is_fatal(_Err(401))
    assert not e4._is_fatal(_Err(429, {"error": {"code": "1302"}}, msg="column 1113"))   # 문자열에 1113이 섞여도 일시 오류
    resp = httpx.Response(403, request=httpx.Request("POST", "http://x"))
    assert e4._is_fatal(httpx.HTTPStatusError("forbidden", request=resp.request, response=resp))


@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(e4, "RESULTS", tmp_path)
    monkeypatch.setattr(e4, "load_env", lambda: None)
    monkeypatch.setenv("ZAI_API_KEY", "test")
    return tmp_path


def test_partial_results_are_kept_and_reused_without_calling(results, monkeypatch):
    calls = []

    def fake_one(client, model, r, extra=None):
        calls.append(r.reviewee_id)
        if len(calls) > 2:
            raise e4.FatalApiError("Insufficient balance")
        return {"pol": 0.5, "lat": 1.0, "in": 10, "out": 20}
    monkeypatch.setattr(e4, "llm_one", fake_one)
    monkeypatch.setattr(e4, "WORKERS", 1)
    rec = e4.run("glm", "demo", _reviews())
    assert rec["partial"] and rec["indices"] == [0, 1] and "Insufficient" in rec["stop_reason"]
    assert (results / "e4_glm_demo.partial.json").exists()
    calls.clear()
    again = e4.run("glm", "demo", _reviews())               # 기본: 이어서 부르지 않고 부분 결과 그대로
    assert calls == [] and again["indices"] == [0, 1]
    monkeypatch.setattr(e4, "llm_one", lambda *a, **k: calls.append(1) or {"pol": 0.1, "lat": 1.0, "in": 1, "out": 1})
    done = e4.run("glm", "demo", _reviews(), resume=True)    # 이어서: 남은 2건만
    assert len(calls) == 2 and not done.get("partial") and len(done["pol"]) == 4
    assert not (results / "e4_glm_demo.partial.json").exists()


def test_records_of_other_texts_are_refused(results, monkeypatch):
    monkeypatch.setattr(e4, "llm_one", lambda *a, **k: {"pol": 0.0, "lat": 1.0, "in": 1, "out": 1})
    e4.run("glm", "probe", _reviews())
    changed = _reviews()
    changed[0] = changed[0].model_copy(update={"positive": ReviewSection(items=["x"], text="바뀐 문장")})
    with pytest.raises(ValueError, match="다른 리뷰 글"):
        e4.run("glm", "probe", changed)


def test_mcnemar_and_wilson_match_known_values():
    assert e4._mcnemar_exact(7, 1) == pytest.approx(0.0703125)
    assert e4._mcnemar_exact(0, 0) == 1.0
    lo, hi = e4._wilson(76, 83)
    assert 0.83 < lo < 0.84 and 0.95 < hi < 0.96
