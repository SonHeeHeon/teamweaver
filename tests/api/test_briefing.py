import json
from unittest.mock import MagicMock

import pytest

from api.rag.briefing import generate_briefing
from api.rag.fallback import rule_based_briefing

_CTX = {
    "p000": {"skills": [{"key": "Java", "value": "4"}],
            "coworks": [{"key": "p005", "value": "6"}],
            "evidence": [{"key": "p010", "value": "0.5"}]},
    "p001": {"skills": [{"key": "Java", "value": "2"}], "coworks": [], "evidence": []},
}


def test_rule_based_briefing_is_deterministic_and_llm_free():
    a = rule_based_briefing(_CTX, "p000", "p001")
    b = rule_based_briefing(_CTX, "p000", "p001")
    assert a == b
    assert set(a.keys()) == {"rationale", "risks", "alternatives", "evidence"}
    assert a["evidence"] == []          # 예전 문맥(극성 숫자)에는 인용할 문장이 없다
    assert isinstance(a["rationale"], str) and len(a["rationale"]) > 0


def test_generate_briefing_parses_structured_output():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "rationale": "p000이 Java 레벨이 더 높다",
            "risks": ["협업 이력이 짧다"],
            "alternatives": ["p002도 후보"],
        })))])
    out = generate_briefing(fake_client, "gpt-5-mini", _CTX, "p000", "p001")
    assert out["rationale"].startswith("p000")
    assert out["risks"] == ["협업 이력이 짧다"]


def test_generate_briefing_raises_on_malformed_json():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="not json"))])
    with pytest.raises(ValueError):
        generate_briefing(fake_client, "gpt-5-mini", _CTX, "p000", "p001")


def test_generate_briefing_raises_on_wrong_shape():
    """JSON 자체는 파싱되지만 스키마와 다른 형태(risks가 list가 아니라 str)면
    BriefingOut(pydantic) 검증이 ValidationError를 내고, generate_briefing이
    이를 ValueError로 감싸 재발생시켜야 한다 -- 예전 코드는 이런 입력을 그대로
    클라이언트에 흘려보냈다."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({
            "rationale": "x", "risks": "not a list", "alternatives": [],
        })))])
    with pytest.raises(ValueError):
        generate_briefing(fake_client, "gpt-5-mini", _CTX, "p000", "p001")


# ---- K5: 출처·직접 인용 ------------------------------------------------------------------

from api.rag.evidence import Evidence, EvidenceIndex, EvidenceSource  # noqa: E402

_POS = "장애 대응 회의에서도 먼저 나서 문제를 정리해 주셨습니다."
_NEG = "회귀 테스트 과정에서 문서 업데이트가 지연되었습니다."
_SID_POS, _SID_NEG = "rv:p097>p001#1:pos", "rv:p097>p001#1:neg"


def _index(reveal=True):
    sources = {
        _SID_POS: EvidenceSource(_SID_POS, "p097", "p001", 1, "positive", _POS if reveal else ""),
        _SID_NEG: EvidenceSource(_SID_NEG, "p097", "p001", 1, "negative", _NEG if reveal else ""),
    }
    if reveal:
        evs = [Evidence(_SID_POS, "p097", "quote", _POS, 0.2), Evidence(_SID_NEG, "p097", "quote", _NEG, 0.2)]
    else:
        evs = [Evidence(_SID_POS, "p097", "label", "좋은 점: 적극성·소통", 0.2),
               Evidence(_SID_NEG, "p097", "label", "아쉬운 점: 문서화", 0.2)]
    return EvidenceIndex(sources, {"p001": evs}, reveal)


def _ctx(idx):
    from dataclasses import asdict
    return {"p000": {"skills": [{"key": "Java", "value": "4"}], "coworks": [], "evidence": []},
            "p001": {"skills": [{"key": "Java", "value": "2"}], "coworks": [],
                     "evidence": [asdict(e) for e in idx.for_person("p001")]}}


def _client(payload):
    c = MagicMock()
    c.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(payload, ensure_ascii=False)))])
    return c


def _llm(**over):
    base = {"rationale": f"p001은 장애 대응에 적극적이다 [{_SID_POS}].",
            "risks": [f"문서화 지연 지적이 있다 [{_SID_NEG}]."],
            "alternatives": ["p002 검토"],
            "citations": [{"source_id": _SID_POS, "quote": _POS}, {"source_id": _SID_NEG, "quote": _NEG}]}
    base.update(over)
    return base


def test_verified_citations_become_quote_evidence():
    idx = _index()
    out = generate_briefing(_client(_llm()), "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert out["evidence"] == [{"source_id": _SID_POS, "reviewer_id": "p097", "kind": "quote", "text": _POS},
                               {"source_id": _SID_NEG, "reviewer_id": "p097", "kind": "quote", "text": _NEG}]
    assert out["rationale"].endswith(f"[{_SID_POS}].")


@pytest.mark.parametrize("bad", [
    {"citations": [{"source_id": "rv:p999>p001#1:pos", "quote": _POS}]},                 # 없는 출처
    {"citations": [{"source_id": _SID_POS, "quote": "장애 대응 회의에서도 먼저 나서 문제를 정리했습니다."}]},  # 바꿔 쓴 인용
    {"citations": [{"source_id": _SID_NEG, "quote": _POS}]},                             # 다른 출처의 문장
    {"rationale": "p001은 적극적이다 [rv:p097>p001#9:pos]."},                            # 본문의 없는 출처 표시
    {"risks": ["동료가 “책임감이 매우 강하다”고 평가했다."]},                       # 본문의 지어낸 인용
    {"citations": "not a list"},
])
def test_any_unverifiable_citation_rejects_the_whole_briefing(bad):
    idx = _index()
    with pytest.raises(ValueError):
        generate_briefing(_client(_llm(**bad)), "m", _ctx(idx), "p000", "p001", evidence=idx)


def test_inline_quote_that_is_verbatim_is_allowed():
    idx = _index()
    payload = _llm(rationale=f"동료는 “{_POS}”라고 평가했다 [{_SID_POS}].")
    out = generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert out["evidence"][0]["text"] == _POS


def test_hidden_mode_forbids_any_quote_and_sends_no_text():
    idx = _index(reveal=False)
    client = _client(_llm(citations=[]))
    out = generate_briefing(client, "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert out["evidence"] == []
    sent = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert _POS not in sent and _NEG not in sent
    with pytest.raises(ValueError):
        generate_briefing(_client(_llm()), "m", _ctx(idx), "p000", "p001", evidence=idx)


def test_without_an_index_citations_are_ignored_like_before():
    out = generate_briefing(_client(_llm()), "m", _CTX, "p000", "p001")
    assert out["evidence"] == []


def test_rule_based_briefing_quotes_real_sentences_with_sources():
    idx = _index()
    out = rule_based_briefing(_ctx(idx), "p000", "p001")
    assert out["evidence"] == [
        {"source_id": _SID_POS, "reviewer_id": "p097", "kind": "quote", "text": _POS},
        {"source_id": _SID_NEG, "reviewer_id": "p097", "kind": "quote", "text": _NEG}]
    assert any("리뷰 근거 2건(직접 인용 2건)" in r for r in out["risks"] + [out["rationale"]])
    assert out == rule_based_briefing(_ctx(idx), "p000", "p001")


def test_rule_based_briefing_in_hidden_mode_shows_labels_only():
    idx = _index(reveal=False)
    out = rule_based_briefing(_ctx(idx), "p000", "p001")
    assert [e["kind"] for e in out["evidence"]] == ["label", "label"]
    assert _POS not in repr(out)


def test_rule_based_briefing_never_labels_a_summary_as_a_quote():
    idx = _index()
    ctx = _ctx(idx)
    ctx["p001"]["evidence"][0]["kind"] = "summary"
    out = rule_based_briefing(ctx, "p000", "p001")
    assert [e["kind"] for e in out["evidence"]] == ["summary", "quote"]


def test_single_quotes_and_apostrophes_are_not_treated_as_quotes():
    idx = _index()
    payload = _llm(risks=["‘소통’ 역량을 확인할 필요가 있다.", "the team's review cycle is short."])
    out = generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert len(out["risks"]) == 2


@pytest.mark.parametrize("risk", [
    "동료가 「책임감이 매우 강하다」고 평가했다.",
    "동료가 『책임감이 매우 강하다』고 평가했다.",
    "동료가 ＂책임감이 매우 강하다＂고 평가했다.",
    "동료가 ‘책임감이 매우 강하다’고 평가했다.",
    "동료가 \"책임감이 매우 강하다고 평가했다.",            # 닫히지 않은 따옴표
])
def test_other_quote_styles_and_unclosed_quotes_are_checked(risk):
    idx = _index()
    with pytest.raises(ValueError):
        generate_briefing(_client(_llm(risks=[risk])), "m", _ctx(idx), "p000", "p001", evidence=idx)


@pytest.mark.parametrize("risk", ["동료가 \"무책임\"하다고 했다.", "‘무책임’ 지적이 있다.", "동료가 「일을 미룬다」고 했다."])
def test_hidden_mode_rejects_any_quote_even_short_ones(risk):
    idx = _index(reveal=False)
    with pytest.raises(ValueError):
        generate_briefing(_client(_llm(citations=[], rationale="p001 검토 [rv:p097>p001#1:pos].",
                                       risks=[risk])), "m", _ctx(idx), "p000", "p001", evidence=idx)


def test_bundled_markers_are_each_checked():
    idx = _index()
    payload = _llm(rationale=f"근거가 있다 [{_SID_POS}, rv:zz>yy#9:neg].")
    with pytest.raises(ValueError):
        generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)


def test_reveal_mode_markers_must_point_at_a_verified_citation():
    idx = _index()
    payload = _llm(rationale=f"p001은 회의 정리를 못한다 [{_SID_NEG}].",
                   risks=["없음"], citations=[{"source_id": _SID_POS, "quote": _POS}])
    with pytest.raises(ValueError):
        generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)


def test_sourced_context_without_the_index_is_refused():
    """연결 실수(문맥에는 색인 근거, generate_briefing에는 색인 없음)로 검증이 꺼지지 않게."""
    idx = _index()
    with pytest.raises(ValueError):
        generate_briefing(_client(_llm()), "m", _ctx(idx), "p000", "p001")


def test_output_that_follows_the_prompt_passes():
    """2차 리뷰 MUST: 프롬프트 지시(quote 근거에만 [id], 표시마다 citations)대로 쓴 출력은 통과해야 한다."""
    idx = _index()
    payload = {"rationale": f"p001은 장애 대응 회의에서 문제를 정리했다는 평가가 있다 [{_SID_POS}].",
               "risks": [f"문서 업데이트 지연 지적이 있다 [{_SID_NEG}]."],
               "alternatives": ["p002 검토"],
               "citations": [{"source_id": _SID_POS, "quote": _POS}, {"source_id": _SID_NEG, "quote": _NEG}]}
    out = generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert [e["source_id"] for e in out["evidence"]] == [_SID_POS, _SID_NEG]
    from api.rag.briefing import _SYSTEM_SOURCED
    assert "kind가 quote가 아닌 근거에는 [source_id]를 붙이지 마라" in _SYSTEM_SOURCED


def test_ascii_single_quotes_around_korean_are_quotes():
    idx = _index(reveal=False)
    with pytest.raises(ValueError):
        generate_briefing(_client(_llm(citations=[], rationale="검토.", risks=["'마감을 항상 어긴다'는 평이다."])),
                          "m", _ctx(idx), "p000", "p001", evidence=idx)


def test_rejections_carry_a_reason_code():
    from api.rag.briefing import BriefingRejected
    idx = _index()
    bad = _llm(citations=[{"source_id": _SID_POS, "quote": "장애 대응을 잘했다는 문장입니다."}])
    with pytest.raises(ValueError) as info:
        generate_briefing(_client(bad), "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert isinstance(info.value.__cause__, BriefingRejected) and info.value.__cause__.code == "quote_not_verbatim"


# ---- 2026-10-05 튜닝: 숨김 모드 라벨 허용, 프로젝트·점수 변화, 생각 깊이 -----------------------

def test_hidden_mode_allows_quoting_a_label_item():
    idx = _index(reveal=False)
    payload = _llm(citations=[], rationale="p001은 ‘소통’ 라벨이 있다 [rv:p097>p001#1:pos].", risks=["“문서화” 보완 필요."])
    out = generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert out["evidence"] == []


def test_hidden_mode_ignores_citations_that_only_echo_labels():
    idx = _index(reveal=False)
    payload = _llm(rationale="검토 [rv:p097>p001#1:pos].", risks=["없음"],
                   citations=[{"source_id": _SID_POS, "quote": "좋은 점: 적극성·소통"},
                              {"source_id": _SID_NEG, "quote": "문서화"}])
    out = generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)
    assert out["evidence"] == []
    bad = _llm(rationale="검토.", risks=["없음"], citations=[{"source_id": _SID_POS, "quote": "회의를 잘 이끌었다"}])
    with pytest.raises(ValueError):
        generate_briefing(_client(bad), "m", _ctx(idx), "p000", "p001", evidence=idx)


def test_score_change_and_effort_reach_the_request():
    idx = _index()
    client = _client(_llm())
    generate_briefing(client, "m", _ctx(idx), "p000", "p001", evidence=idx,
                      score_change={"total": -0.12, "skill": -0.2}, reasoning_effort="low")
    kw = client.chat.completions.create.call_args.kwargs
    assert kw["reasoning_effort"] == "low"
    assert json.loads(kw["messages"][1]["content"])["score_change"] == {"total": -0.12, "skill": -0.2}


def test_effort_comes_from_the_model_entry_and_is_omitted_otherwise():
    from core.config import load_pricing
    model = load_pricing()["briefing_model"]
    client = _client(_llm())
    generate_briefing(client, model, _CTX, "p000", "p001")
    assert client.chat.completions.create.call_args.kwargs.get("reasoning_effort") == \
        load_pricing()["models"][model].get("reasoning_effort")
    client = _client(_llm())
    generate_briefing(client, "some-non-reasoning-model", _CTX, "p000", "p001")
    assert "reasoning_effort" not in client.chat.completions.create.call_args.kwargs


def test_hidden_mode_allows_quoting_the_whole_label_text():
    idx = _index(reveal=False)
    payload = _llm(citations=[], rationale="라벨은 \u201c좋은 점: 적극성·소통\u201d이다 [rv:p097>p001#1:pos].", risks=["없음"])
    assert generate_briefing(_client(payload), "m", _ctx(idx), "p000", "p001", evidence=idx)["evidence"] == []


def test_prompts_carry_the_length_limits_and_project_rule():
    from api.rag.briefing import _SYSTEM, _SYSTEM_HIDDEN, _SYSTEM_SOURCED
    for p in (_SYSTEM, _SYSTEM_SOURCED, _SYSTEM_HIDDEN):
        assert "risks는 최대 3개" in p and "alternatives는 최대 2개" in p and "project가 있으면" in p
    assert "citations는 항상 빈 배열" in _SYSTEM_HIDDEN
