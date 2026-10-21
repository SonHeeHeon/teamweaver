"""시험 세션은 실제 설명 AI 채팅 호출을 막는다(tests/conftest._no_real_ai_calls, 2026-10-20).
가짜 AI는 라우트가 쥔 함수에 끼운다(tests/ai_override.py) -- 이름을 열쇠로 쓰는 옛 방식은 다시 들어오지 않게 막는다."""
from pathlib import Path

import pytest


def test_real_openai_chat_call_is_blocked_in_tests():
    openai = pytest.importorskip("openai")
    c = openai.OpenAI(api_key="sk-test-not-real", base_url="http://127.0.0.1:9/v1")
    with pytest.raises(RuntimeError, match="실제 AI 호출을 막았다"):
        c.chat.completions.create(model="x", messages=[{"role": "user", "content": "hi"}])


def test_override_ai_swaps_the_route_function_and_restores_the_previous_override():
    import api.deps as deps
    from api.main import app
    from tests.ai_override import ai_dependencies, override_ai
    keys = ai_dependencies()
    assert keys == [deps.get_openai_client_or_none]          # 모든 라우트가 같은 함수 하나를 쥔다
    outer = lambda: "바깥"                                    # noqa: E731 -- 바깥 픽스처가 걸어 둔 override
    app.dependency_overrides[keys[0]] = outer
    try:
        with override_ai("가짜") as used:
            assert app.dependency_overrides[keys[0]]() == "가짜" and used == [1]
        assert app.dependency_overrides[keys[0]] is outer   # 겹쳐 써도 바깥 것으로 되돌아간다
    finally:
        app.dependency_overrides.pop(keys[0], None)
    with override_ai(None):
        pass
    assert keys[0] not in app.dependency_overrides            # 없던 것은 지운다


def test_tests_do_not_key_ai_overrides_by_name():
    """`app.dependency_overrides[...get_openai_client_or_none]`는 다른 시험이 그 이름을 바꿔 끼우면 빗나간다 -- ai_client를 쓴다."""
    root = Path(__file__).parent
    here = Path(__file__).resolve()
    bad = [f"{p.relative_to(root)}:{i}" for p in sorted(root.rglob("*.py")) if p.resolve() != here
           for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if "dependency_overrides[" in line and "get_openai_client_or_none" in line and not line.lstrip().startswith(("#", "`"))]
    assert not bad, f"이름을 열쇠로 쓴 설명 AI override: {bad} -- ai_client 픽스처나 tests.ai_override.override_ai를 쓴다"
