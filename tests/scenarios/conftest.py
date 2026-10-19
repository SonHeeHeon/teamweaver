"""3가지 핵심 상황 시험 공용: tests/nightly의 격리(홈 폴더·외부 AI 차단·예약 끔)를 그대로 쓰고, 연말형 시연 묶음으로 서버를 띄운다."""
from pathlib import Path

import pytest

from tests.nightly.conftest import _isolated, _session_private_dirs, fake_solvers, fast_checks  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
YEAREND = ROOT / "demo" / "org-n100-yearend"


@pytest.fixture
def yearend(monkeypatch):
    monkeypatch.setenv("TEAMWEAVER_DEMO_DIR", str(YEAREND.parent))
    monkeypatch.setenv("TEAMWEAVER_DEMO_BUNDLE", str(YEAREND))
    return YEAREND
