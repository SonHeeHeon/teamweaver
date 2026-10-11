"""설명 글 LLM 전송 정책(api.deps.llm_client_for): 실데이터는 사내 주소이거나 외부 전송을 허용했을 때만 보낸다.
Codex 사후 리뷰 MUST(2026-10-11): 교체 설명이 허용 설정 없이 실데이터 문맥(ID·기술·협업·라벨·사업명)을 OpenAI로 보냈다."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api import review_judge as rj
from api.deps import get_openai_client_or_none, llm_client_for
from api.main import app


def _ds(synthetic):
    return SimpleNamespace(info=SimpleNamespace(synthetic=synthetic))


def _client(url):
    return SimpleNamespace(base_url=url)


@pytest.mark.parametrize("synthetic, url, allow, expected", [
    (True, "https://api.openai.com/v1/", False, None),          # 가상 데이터는 어디든
    (False, "https://api.openai.com/v1/", False, "external_blocked"),
    (None, "https://api.openai.com/v1/", False, "external_blocked"),   # 가상 여부를 모르면 실데이터로 본다
    (False, "https://api.openai.com/v1/", True, None),           # 관리자가 외부 전송 허용
    (False, "http://10.1.2.3:8000/v1", False, None),             # 사내(사설 주소)
    (False, "https://llm.example.com/v1", False, "external_blocked"),  # 사내로 확인되지 않은 주소
])
def test_llm_client_for_policy(monkeypatch, synthetic, url, allow, expected):
    monkeypatch.delenv(rj.ALLOW_EXTERNAL_ENV, raising=False)
    monkeypatch.delenv(rj.ONPREM_ENV, raising=False)
    if allow:
        monkeypatch.setenv(rj.ALLOW_EXTERNAL_ENV, "1")
    c = _client(url)
    got, reason = llm_client_for(_ds(synthetic), c)
    assert reason == expected and (got is c) == (expected is None)


def test_llm_client_for_without_client():
    assert llm_client_for(_ds(True), None) == (None, "no_client")


def test_whatif_does_not_send_real_data_to_openai(client, monkeypatch):
    fake = MagicMock()
    fake.base_url = "https://api.openai.com/v1/"
    fake.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(
        {"rationale": "x", "risks": [], "alternatives": []})))])
    app.dependency_overrides[get_openai_client_or_none] = lambda: fake
    monkeypatch.delenv(rj.ALLOW_EXTERNAL_ENV, raising=False)
    import dataclasses
    monkeypatch.setattr(app.state.dataset, "info", dataclasses.replace(app.state.dataset.info, synthetic=False))  # 실데이터처럼
    try:
        meta = client.get("/api/meta").json()
        project, people = meta["projects"][0], meta["people"]
        body = {"entries": [{"person_id": people[0]["id"], "project_id": project["id"], "alloc": 1.0}],
                "swap": {"out_person_id": people[0]["id"], "in_person_id": people[1]["id"], "project_id": project["id"]},
                "weights": {}}
        out = client.post("/api/whatif", json=body).json()
        assert out["fallback_used"] is True and out["fallback_reason"] == "external_blocked"
        fake.chat.completions.create.assert_not_called()
        monkeypatch.setenv(rj.ALLOW_EXTERNAL_ENV, "1")                    # 허용하면 보낸다
        out = client.post("/api/whatif", json=body).json()
        assert out["fallback_used"] is False and out["fallback_reason"] is None
    finally:
        app.dependency_overrides.pop(get_openai_client_or_none, None)


def test_llm_client_for_honors_explicit_onprem_but_not_for_openai(monkeypatch):
    monkeypatch.delenv(rj.ALLOW_EXTERNAL_ENV, raising=False)
    monkeypatch.setenv(rj.ONPREM_ENV, "1")             # 관리자가 사내로 지정(공인 이름의 사내 서버)
    assert llm_client_for(_ds(False), _client("https://llm.example.com/v1"))[1] is None
    assert llm_client_for(_ds(False), _client("https://api.openai.com/v1/"))[1] == "external_blocked"
