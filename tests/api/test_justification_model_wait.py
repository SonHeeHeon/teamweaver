"""소명 AI 대기 상한이 모델을 따른다(사용자 결정 2026-10-11 (가): 사내 AI로 바꿔 적용하면 더 오래 기다린다). 서비스 기본(외부 luna)은 40초 그대로."""
from types import SimpleNamespace

import pytest

import api.routes.justification as jr
from api.routes.report import _justify_allowance
from api.schemas import ReportRequest


def _req(n):
    return ReportRequest(plan_label="A", objective=1, fulfillment=1, optimization_ratio=1,
                         entries=[{"person_id": f"p{i}", "project_id": f"J{i}", "alloc": 1.0} for i in range(n)])


def _pricing_with(model):
    from core.config import load_pricing
    base = load_pricing()
    return lambda: {**base, "briefing_model": model}


def test_external_model_keeps_forty_seconds():
    assert jr.ai_model() == "gpt-6-luna"
    assert jr.ai_timeout_s() == jr.AI_TIMEOUT_S == 40.0
    assert _justify_allowance(_req(6)) == 40.0 + 30


@pytest.mark.parametrize("model,timeout", [("glm-5.3-flash", 300.0), ("gpt-6-luna", 40.0), ("모르는-모델", 40.0)])
def test_timeout_client_and_pdf_allowance_follow_the_model(monkeypatch, model, timeout):
    import core.config
    monkeypatch.setattr(core.config, "load_pricing", _pricing_with(model))
    assert jr.ai_timeout_s() == timeout
    seen = {}
    fake = SimpleNamespace(with_options=lambda **kw: seen.update(kw) or "wrapped")
    assert jr._client_with_timeout(fake) == "wrapped" and seen == {"timeout": timeout, "max_retries": 0}
    assert _justify_allowance(_req(30)) == 5 * timeout + 30
