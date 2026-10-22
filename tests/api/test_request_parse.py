"""긴급 투입 "상황을 말로"(2026-10-22): 상황 글 → 요구(기술·인원·등급). AI는 사전 id 안에서만, 근거 말이 글에 있어야 받고,
사람 특성 말은 요구로 쓰지 않는다. AI가 없으면 사전 별칭 찾기 + 숫자·등급 규칙."""
import json
from types import SimpleNamespace

import pytest

from api.rag.request_parse import alias_hits, parse_request, protected_terms, rule_fields
from core.ingest.skills import load_dictionary


class FakeAI:
    """chat.completions.create가 정해 둔 JSON을 돌려주는 가짜(호출 내용은 calls에)."""
    def __init__(self, answer, fail=False):
        self.calls, self.answer, self.fail = [], answer, fail
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise TimeoutError("느림")
        msg = SimpleNamespace(content=json.dumps(self.answer, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))

    def with_options(self, **kw):
        return self


@pytest.fixture(scope="module")
def sd():
    return load_dictionary()


TEXT = "운영 이관 중 장애 — k8s랑 배치 데이터 적재 아는 사람 두 명, 30대 남자면 좋겠음. 고급 이상"


def test_without_ai_alias_names_and_rules_fill_the_form(sd):
    r = parse_request(TEXT, sd)
    assert r["method"] == "rule" and r["fallback_reason"] == "no_client"
    assert [(s["name"], s["quote"], s["source"]) for s in r["skills"]] == [("Kubernetes", "k8s", "alias")]
    assert (r["headcount"], r["grade"], r["grade_at_least"]) == (2, "고급", True)
    assert r["warnings"] and "30대" in r["warnings"][0] and "남자" in r["warnings"][0]
    assert TEXT[r["skills"][0]["start"]:r["skills"][0]["end"]] == "k8s"


def test_alias_hits_respect_word_edges_and_take_longest_names(sd):
    assert alias_hits(sd, "Google 검색 화면") == []                         # "Go"가 Google 안에 있어도 아니다
    names = [h["name"] for h in alias_hits(sd, "AI 에이전트 프레임워크 경험")]
    assert names == ["AI 에이전트 프레임워크"]                              # 긴 이름 먼저, 겹치지 않게
    assert protected_terms("3대 서버 장애, 3세대 구조") == []                 # IT의 장애·대수는 사람 특성이 아니다
    assert rule_fields("특급 한 분") == {"headcount": 1, "grade": "특급", "grade_at_least": False}


def test_ai_answer_is_checked_against_the_dictionary_and_the_text(sd):
    ai = FakeAI({"skills": [
        {"id": "kubernetes", "quote": "k8s", "kind": "explicit"},             # 별칭 찾기와 같은 id -- 한 번만
        {"id": "etl", "quote": "배치 데이터 적재", "kind": "implied"},         # 미루어 본 기술(근거 말이 글에 있다)
        {"id": "no_such_skill", "quote": "k8s", "kind": "explicit"},           # 사전 밖 -- 버림
        {"id": "spark", "quote": "스파크", "kind": "explicit"},                # 글에 없는 근거 -- 버림
        {"id": "java", "quote": "30대 남자", "kind": "implied"}],              # 사람 특성 말이 근거 -- 버림
        "headcount": 9, "grade": "고급", "grade_at_least": True, "unmapped_terms": ["운영 이관"], "question": None})
    r = parse_request(TEXT, sd, client=ai, model="m", price={"input_per_1m": 1.0, "output_per_1m": 2.0})
    assert r["method"] == "ai" and len(ai.calls) == 1
    got = [(s["id"], s["kind"], s["source"]) for s in r["skills"]]
    assert got == [("kubernetes", "explicit", "alias"), ("etl", "implied", "ai")]
    assert TEXT[r["skills"][1]["start"]:r["skills"][1]["end"]] == "배치 데이터 적재"
    assert {d["id"] for d in r["dropped"]} == {"no_such_skill", "spark", "java"}
    assert r["headcount"] == 2                                               # AI의 9명은 범위 밖 -- 글의 "두 명"
    assert r["unmapped_terms"] == ["운영 이관"] and r["usage"]["cost_usd"] == pytest.approx(0.00014)
    prompt = ai.calls[0]["messages"][1]["content"]
    assert "kubernetes | Kubernetes" in prompt and "사람을 고르거나" in ai.calls[0]["messages"][0]["content"]


def test_ai_failure_falls_back_to_rules(sd):
    r = parse_request(TEXT, sd, client=FakeAI({}, fail=True), model="m")
    assert r["method"] == "rule" and r["fallback_reason"] == "llm_error:TimeoutError" and r["skills"][0]["id"] == "kubernetes"


def test_route_uses_the_ai_client_and_never_sends_people(client, ai_client):
    v = client.get("/api/meta").json()["dataset_version"]
    ai = FakeAI({"skills": [{"id": "etl", "quote": "배치 데이터 적재", "kind": "implied"}], "headcount": 2, "grade": None,
                 "grade_at_least": False, "unmapped_terms": [], "question": None})
    with ai_client(ai) as used:
        r = client.post("/api/emergency/parse", json={"dataset_version": v, "text": TEXT})
    assert r.status_code == 200, r.text
    body = r.json()
    assert used and body["method"] == "ai" and body["ai_available"] is True
    assert {s["id"] for s in body["skills"]} == {"kubernetes", "etl"}
    sent = json.dumps(ai.calls[0]["messages"], ensure_ascii=False)
    people = client.get("/api/meta").json()["people"]
    assert not any(p["id"] in sent or (p.get("name") and p["name"] in sent) for p in people)   # 사람 정보는 AI에 가지 않는다
    with ai_client(None):
        r = client.post("/api/emergency/parse", json={"dataset_version": v, "text": TEXT}).json()
    assert r["method"] == "rule" and r["fallback_reason"] == "no_client" and r["ai_available"] is False
    assert client.post("/api/emergency/parse", json={"dataset_version": v, "text": "x" * 301}).status_code == 422
