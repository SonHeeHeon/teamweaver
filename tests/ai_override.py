"""시험에서 설명 AI 클라이언트를 바꿔 끼우는 공용 도구(2026-10-20, claude-a -- claude-b 확인 권장 처리).

`app.dependency_overrides[api.deps.get_openai_client_or_none]`처럼 이름을 열쇠로 쓰면, 다른 시험이 그 이름을 바꿔 끼웠거나
모듈을 다시 불러온 뒤에는 열쇠가 라우트가 쥔 함수와 달라 override가 조용히 빠진다 -- 그때 시험은 진짜 클라이언트로 돈다.
여기서는 라우트가 실제로 쥔 함수를 찾아 모두 바꾼다. 함수 시험은 `ai_client` 픽스처(tests/conftest.py)로,
모듈 단위 픽스처·도우미는 `override_ai`를 직접 쓴다."""
from contextlib import contextmanager

_MISSING = object()


def ai_dependencies() -> list:
    """라우트가 쥔 설명 AI 의존성 함수들(이름으로 찾는다)."""
    from api.main import app
    found: list = []

    def walk(dep):
        for d in dep.dependencies:
            if getattr(d.call, "__name__", "") == "get_openai_client_or_none" and d.call not in found:
                found.append(d.call)
            walk(d)

    def visit(routes):
        for r in routes:
            if getattr(r, "dependant", None) is not None:
                walk(r.dependant)
            if getattr(r, "original_router", None) is not None:      # FastAPI 0.14x: include_router가 감싼 라우터
                visit(r.original_router.routes)
    visit(app.routes)
    return found


@contextmanager
def override_ai(value):
    """`with override_ai(fake) as used:` -- 설명 AI 클라이언트를 `value`(가짜·None)로 바꾼 채 실행한다.
    `used`는 라우트가 그 값을 받아 간 횟수만큼 쌓인다(가짜가 정말 쓰였는지 확인용). 끝나면 직전 override로 되돌린다
    -- 바깥 픽스처가 걸어 둔 None 위에 가짜를 겹쳐 써도 된다."""
    from api.main import app
    keys = ai_dependencies()
    assert keys, "설명 AI 의존성을 쓰는 라우트를 찾지 못했다"
    used: list = []

    def provide():
        used.append(1)
        return value
    saved = {k: app.dependency_overrides.get(k, _MISSING) for k in keys}
    for k in keys:
        app.dependency_overrides[k] = provide
    try:
        yield used
    finally:
        for k, prev in saved.items():
            if prev is _MISSING:
                app.dependency_overrides.pop(k, None)
            else:
                app.dependency_overrides[k] = prev
