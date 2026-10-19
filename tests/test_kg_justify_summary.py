"""인사팀 소명 요약(2026-10-20 사용자 "너무 길어서 읽을수가 없어 … 출처에서 나온 사실만 … 결과를 검증"): 본문과 같은 자리표시 문법·검사 +
3문장 이하·근거 2개 이상·불리한 사실 인용. 걸리면 규칙 요약."""
import dataclasses
import json
from types import SimpleNamespace

from core.kg.justify import render, rule_summary_raw, verify_summary
from tests.test_kg_justify import _small, inp  # noqa: F401 -- 같은 사실 묶음·시연 사업


def _with_adverse():
    s = _small()
    facts = [dataclasses.replace(f, adverse=True) if f.id == "F5" else f for f in s.facts]
    return dataclasses.replace(s, facts=facts)


def test_short_summary_passes_and_fills_facts_from_the_source():
    s = _small()
    raw = "요구 기술은 {F2}입니다. 후보 비교 결과는 {F9}입니다."
    assert verify_summary(raw, s)["ok"]
    text = render(raw, s)
    assert "{" not in text and "ETL 요구 2명" in text


def test_summary_rules():
    s = _with_adverse()
    assert [v["rule"] for v in verify_summary("요구 기술은 {F2}입니다. 후보 비교 결과는 {F9}입니다.", s)["violations"]] == ["Y3"]
    assert verify_summary("요구 기술은 {F2}입니다. 후보 비교 결과는 {F9}입니다. 다만 P1은 {F5}입니다.", s)["ok"]
    four = "요구 기술은 {F2}입니다. 후보 비교 결과는 {F9}입니다. 규칙 준수 현황은 {F10}입니다. 다만 P1은 {F5}입니다."
    assert "Y1" in {v["rule"] for v in verify_summary(four, s)["violations"]}
    assert "Y2" in {v["rule"] for v in verify_summary("다만 P1은 {F5}입니다.", s)["violations"]}
    five = "요구 기술은 {F2}입니다. 후보 비교 결과는 {F9}입니다. P1은 {F3}, {F4}이며 {F5}입니다."
    assert "Y4" in {v["rule"] for v in verify_summary(five, s)["violations"]}
    assert "S1" in {v["rule"] for v in verify_summary("요구 기술은 {F2}입니다. 다만 P1은 {F99}, {F5}입니다.", s)["violations"]}
    assert "S2" in {v["rule"] for v in verify_summary("요구 기술은 {F2}로 아주 훌륭합니다. 다만 P1은 {F5}입니다.", s)["violations"]}


def test_rule_summary_is_valid_and_keeps_the_adverse_fact():
    s = _with_adverse()
    raw = rule_summary_raw(s)
    assert raw and verify_summary(raw, s)["ok"] and "{F5}" in raw
    assert rule_summary_raw(_small()) == "요구 기술은 {F2}입니다. 후보 비교 결과는 {F9}입니다."


def test_rule_summary_on_a_demo_project(inp):   # noqa: F811
    from core.kg.justify import template_text
    raw = rule_summary_raw(inp)
    assert raw is not None and len([s for s in raw.split(".") if "{" in s]) <= 3
    assert len(render(raw, inp)) * 3 < len(template_text(inp.facts))      # 본문보다 훨씬 짧다


def _client(text):
    msg = SimpleNamespace(content=json.dumps({"text": text}))
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: resp)))


def test_generate_summary_uses_ai_only_when_it_passes_the_check():
    from api.rag.justification import generate_summary
    s = _with_adverse()
    ok = generate_summary(_client("요구 기술은 {F2}입니다. 다만 P1은 {F5}입니다."), "gpt-6-luna", s)
    assert ok["summary_method"] == "graphrag" and "Spark 경력 8개월" in ok["summary"]
    bad = generate_summary(_client("이 팀은 최고입니다. 요구 기술은 {F2}입니다."), "gpt-6-luna", s)
    assert bad["summary_method"] == "rule" and bad["summary_fallback_reason"].startswith("verify:")
    assert generate_summary(None, "gpt-6-luna", s)["summary_method"] == "rule"
