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
    assert set(a.keys()) == {"rationale", "risks", "alternatives"}
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
