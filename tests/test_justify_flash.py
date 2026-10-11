"""E7c-F(소명 글 사내 열) 측정 스크립트: 가짜 클라이언트로 끝까지 -- 짝 비교·무결성·비용 상한·서비스 상한 동기화."""
import json
from types import SimpleNamespace

import pytest

from rehearsal import justify_flash as jf
from rehearsal import justify_scale_e7c as e7c


class _Fake:
    """사용자 지시문(사실 목록)으로 사업을 알아보고 정해 둔 글을 돌려준다."""

    def __init__(self, texts: dict[str, str], *, out_tokens: int = 2000):
        self.calls = 0

        def create(**kw):
            self.calls += 1
            text = texts.get(kw["messages"][-1]["content"], "")
            assert kw.get("reasoning_effort") == "high" and kw["model"] == "glm-5.3-flash"
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"text": text}, ensure_ascii=False)))],
                                   usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=out_tokens))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


@pytest.fixture(scope="module")
def src():
    s = jf.load_source()
    jf.prime_ids(s)
    return s


def _texts(src, *, broken: bool = False) -> dict[str, str]:
    from api.rag.justification import prompt
    out = {}
    for c in jf.planned(src):
        inp = e7c._inp(src, c)
        out[prompt(inp)] = "이 사업은 정말 훌륭한 {F1}입니다." if broken else c["llm_text"]
    return out


def test_service_timeout_matches_the_api():
    from api.routes.justification import AI_TIMEOUT_S
    assert jf.SERVICE_TIMEOUT_S == AI_TIMEOUT_S


def test_planned_is_the_holdout_first_run(src):
    rows = jf.planned(src)
    assert len(rows) == 75 and {c["set"] for c in rows} == {"B"} and {c["run"] for c in rows} == {0}
    assert all(c["bundle"].startswith("holdout-") for c in rows)


def test_mcnemar_exact():
    assert jf.mcnemar_exact(0, 0) == 1.0
    assert jf.mcnemar_exact(0, 5) == pytest.approx(2 / 32)
    assert jf.mcnemar_exact(1, 9) == pytest.approx(2 * 11 / 1024)
    assert jf.mcnemar_exact(9, 1) == jf.mcnemar_exact(1, 9)


def test_replaying_the_luna_texts_passes_integrity_and_pairs_equal(src):
    res = jf.run(_Fake(_texts(src)), limit=5, workers=2)
    s = res["summary"]
    assert res["planned_calls"] == 5 and len(res["calls"]) == 5
    assert s["accepted"] == 5 and s["F1"] == "통과" and s["F1_failed"] == 0 and s["F2"] == "달성"
    assert s["paired"] == {"luna_only": 0, "flash_only": 0, "p": 1.0}
    assert s["cost_per_text"] == pytest.approx((1000 * 0.15 + 2000 * 0.5) / 1e6)
    assert all(r["wall_s"] >= 0 and r["usage"]["reasoning_effort"] == "high" for r in res["calls"])
    assert "E7c-F" in jf.render(res)


def test_integrity_failures_are_counted(src, monkeypatch):
    monkeypatch.setattr(e7c, "integrity_problems", lambda c, inp, ids: ["주입한 결함"])
    s = jf.run(_Fake(_texts(src)), limit=3, workers=1)["summary"]
    assert s["F1"] == "실패" and s["F1_failed"] == 3


def test_rejected_texts_fall_back_and_count_against_flash(src):
    s = jf.run(_Fake(_texts(src, broken=True)), limit=4, workers=2)["summary"]
    assert s["accepted"] == 0 and s["F1"] == "해당 없음" and s["F2"] == "미달"
    assert s["paired"]["luna_only"] == 4 and s["rules"].get("S2", 0) >= 1
    assert s["fallback_reasons"] == {"verify": 4}


def test_cost_cap_stops_and_marks_incomplete(src):
    fake = _Fake(_texts(src), out_tokens=3_000_000)          # 한 편 약 $1.5
    res = jf.run(fake, limit=6, workers=1, cost_cap=1.0)
    assert res["stopped_by_cost_cap"] and len(res["calls"]) == 1 and fake.calls == 1
    assert res["summary"]["F2"] == "미완" and res["summary"]["planned"] == 6
