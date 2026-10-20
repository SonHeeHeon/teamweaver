"""시험 세션은 실제 설명 AI 채팅 호출을 막는다(tests/conftest._no_real_ai_calls, 2026-10-20)."""
import pytest


def test_real_openai_chat_call_is_blocked_in_tests():
    openai = pytest.importorskip("openai")
    c = openai.OpenAI(api_key="sk-test-not-real", base_url="http://127.0.0.1:9/v1")
    with pytest.raises(RuntimeError, match="실제 AI 호출을 막았다"):
        c.chat.completions.create(model="x", messages=[{"role": "user", "content": "hi"}])
