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


def test_e5_conclusion_is_read_from_the_first_sentence():
    import experiments.jev.e5_briefing as e5
    assert e5._conclusion("교체를 보류한다. 권고할 이유도 있으나 위반이 생긴다.") == "보류"
    assert e5._conclusion("조건부로 권고한다. 다만…") == "조건부"
    assert e5._conclusion("교체를 권고한다.") == "권고"
    assert e5._conclusion("판단하기 어렵다.") == "불명"


def test_e5_glm_cost_counts_cached_input_and_reasoning_as_output():
    import experiments.jev.e5_briefing as e5
    rows = [{"in_tokens": 1000, "cached": 400, "out_tokens": 500, "reasoning": 300}]
    assert e5._cost("glm_low", rows) == pytest.approx((600 * 1.40 + 400 * 0.26 + 500 * 4.40) / 1e6)


def test_e4_sets_keep_the_faithful_texts_and_sample_the_demo():
    s = e4.load_sets()
    assert len(s["faithful"]) == 300 and len(s["demo_s300"]) == e4.N_DEMO_SAMPLE
    assert set(e4.SET_JUDGES["demo_s300"]) == {"llm_low", "glm_low", "llm", "jev"}


@pytest.mark.parametrize("judge,effort", [("glm_low", "low"), ("llm_low", "low"), ("glm", "max"), ("llm", None)])
def test_each_judge_sends_its_reasoning_effort(results, monkeypatch, judge, effort):
    # 사용자 지시 "전부 low": low 판정기는 실제로 reasoning_effort=low를 보낸다
    seen = []
    monkeypatch.setattr(e4, "llm_one", lambda client, model, r, extra=None: seen.append(extra) or
                        {"pol": 0.0, "lat": 1.0, "in": 1, "out": 1})
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    e4.run(judge, "probe", _reviews(2))
    assert all((x or {}).get("reasoning_effort") == effort for x in seen) and len(seen) == 2


def test_e5_summary_splits_forced_and_free_agreement_and_checks_case_keys(monkeypatch, tmp_path):
    import experiments.jev.e5_briefing as e5
    cases = [{"key": f"k{i}", "score_change": ({"new_violations": ["v"]} if i < 2 else {})} for i in range(4)]
    ok = lambda c: {"ok": True, "conclusion": c, "first": f"교체를 {c}한다.", "citations": 1, "quotes": 1, "chars": 10,
                              "lat": 1.0, "in_tokens": 1, "out_tokens": 1, "cached": 0, "reasoning": 0}
    rows = {"llm": {"0": ok("보류"), "1": {**ok(""), "ok": False, "code": "recommends_infeasible"}, "2": ok("권고"), "3": ok("권고")},
            "glm_low": {"0": ok("보류"), "1": ok("보류"), "2": ok("보류"), "3": ok("권고")}}
    for m in rows:
        for i, r in rows[m].items():
            r["key"] = f"k{i}"
    monkeypatch.setattr(e5, "build_cases", lambda: (type("A", (), {"evidence": None})(), cases))
    monkeypatch.setattr(e5, "run", lambda m, cs, ev: {"rows": rows[m], "model": m, "effort": "low", "wall_s": 1.0})
    monkeypatch.setattr(e5, "RESULTS", tmp_path)
    s = e5.summarize()
    assert s["n_forced"] == 1 and s["conclusion_agree_forced"] == 1.0          # 둘 다 성공한 강제 사례 1건
    assert s["n_free"] == 2 and s["conclusion_agree_free"] == 0.5
    assert s["hold_paired"] == {"n": 2, "llm_only": 0, "glm_low_only": 1, "p": 1.0}
    rows["llm"]["3"]["key"] = "other"
    with pytest.raises(ValueError, match="다른 교체 사례"):
        e5.summarize()


def test_e5_post_guard_codes_match_the_briefing_module():
    # 보류 가드 뒤(인용 검증)에서 나는 코드 = briefing.py의 BriefingRejected 코드 − 가드 앞 코드(리뷰 2라운드)
    import re
    from pathlib import Path
    import experiments.jev.e5_briefing as e5
    src = Path("api/rag/briefing.py").read_text("utf-8")
    codes = set(re.findall(r'BriefingRejected\("([a-z_]+)"', src))
    assert e5._POST_GUARD == codes - {"missing_index", "recommends_infeasible"}


@pytest.mark.parametrize("sentence,tag", [("교체를 권고하지 않습니다.", "보류"), ("결론은 교체 비권고입니다.", "보류"),
                                          ("교체는 권고 안 함.", "보류"), ("교체를 권고드리기 어렵습니다.", "보류"),
                                          ("교체를 권고합니다.", "권고")])
def test_e5_negated_recommendations_are_holds(sentence, tag):
    import experiments.jev.e5_briefing as e5
    assert e5._conclusion(sentence) == tag
