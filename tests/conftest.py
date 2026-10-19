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
