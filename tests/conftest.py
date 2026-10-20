"""저장소 전체 시험 공용(2026-10-18, claude-b): 야간 배치 예약 실행기(api/nightly.py)를 끈다.

서버(lifespan)는 기본으로 예약 실행기를 켠다 -- 시험이 자정을 넘겨 돌면 시험 서버마다 밤 배치가 시작될 수 있다.
모듈·세션 단위 실서버 픽스처도 함수 픽스처보다 먼저 뜨므로 세션 시작에 환경 변수로 끈다. 수동 실행 시험은 그대로 된다."""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _nightly_scheduler_off():
    saved = os.environ.get("TEAMWEAVER_NIGHTLY")
    os.environ["TEAMWEAVER_NIGHTLY"] = "off"
    yield
    if saved is None:
        os.environ.pop("TEAMWEAVER_NIGHTLY", None)
    else:
        os.environ["TEAMWEAVER_NIGHTLY"] = saved


@pytest.fixture(scope="session", autouse=True)
def _no_real_ai_calls():
    """시험은 실제 설명 AI를 부르지 않는다(2026-10-20, claude-b 확인 권장 처리).

    lifespan의 load_env가 .env의 실제 키를 환경에 올리면 그 뒤로는(세션 끝까지 남는다) 가짜로 바꿔 끼우지 않았거나 바꿔 끼우기가
    빗나간 의존성이 진짜 OpenAI 클라이언트를 만든다. 그 클라이언트의 채팅 호출을 여기서 막는다 -- 가짜(MagicMock)는 이 길을 지나지 않는다.
    10-20 진단(호출을 막고 기록하는 플러그인으로 전체 실행): 실제 호출 0건 -- 지금은 새는 곳이 없고, 앞으로 생기면 외부로 나가지 않게."""
    try:
        from openai.resources.chat import completions as cc
    except Exception:                                   # noqa: BLE001 -- openai가 없으면 막을 것도 없다
        yield
        return
    real = cc.Completions.create

    def blocked(self, *args, **kwargs):
        raise RuntimeError("시험 중 실제 AI 호출을 막았다 -- tests/api의 ai_client 픽스처나 dependency_overrides로 가짜를 넣어라")
    cc.Completions.create = blocked
    yield
    cc.Completions.create = real
