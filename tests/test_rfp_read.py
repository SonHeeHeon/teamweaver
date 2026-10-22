"""발주 자료 필요 기술 읽기(api/rag/rfp_read, 2026-10-23 claude-a -- claude-b 요청 "발주 자료에서 필요 기술 읽기(AI)").
AI는 가짜 client로만 부른다(시험 전체에서 실제 호출은 막혀 있다)."""
import json
from types import SimpleNamespace

import pytest

from api.rag.request_parse import alias_hits
from api.rag.rfp_read import MAX_AI_TEXT, read_requirements, rule_read
from core.ingest.skills import load_dictionary

DOC = """제안요청서
1. 현황: 현행 시스템은 오라클 11g와 웹로직(WebLogic) 위에서 운영 중이다.
2. 과업: 응용 시스템은 전자정부 표준프레임워크를 적용하여 재개발하고, 데이터베이스는 PostgreSQL로 전환한다.
3. 투입 인력: Java 기반 개발 경력 5년 이상 2명, PostgreSQL 경력 3년 이상 1명.
4. 우대: Kafka 실시간 처리 경험자 우대."""


@pytest.fixture(scope="module")
def sd():
    return load_dictionary()


def _client(payload: dict, calls: list | None = None):
    def create(**kw):
        if calls is not None:
            calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))],
                               usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _by(out):
    return {s["name"]: s for s in out["skills"]}


def test_alias_boundaries_use_the_original_text(sd):
    """띄어쓰기를 지운 글로 경계를 보면 "Python 5년"·"JEUS·WebtoB"를 버리고 "산출물은 행정"에서 "은행"을 잡았다(2026-10-23 고침)."""
    names = lambda t: [h["name"] for h in alias_hits(sd, t)]   # noqa: E731
    assert "Python" in names("인력: Python 5년 이상 2명")
    assert {"JEUS", "WebtoB"} <= set(names("JEUS·WebtoB 운영 경력"))
    assert "은행 업무" not in names("개발 산출물은 행정안전부 가이드를 따른다")
    assert "은행 업무" in names("은행 업무 경험 3년")
    assert "Go" not in names("Google 검색")
    assert "Kubernetes" in names("사내 쿠버네티스 위로 옮긴다")


def test_rule_reads_requirements_preferred_and_current_system(sd):
    out = rule_read(DOC, sd)
    got = _by(out)
    assert got["Java"]["kind"] == "required" and got["Java"]["months"] == 60 and got["Java"]["headcount"] == 2
    assert got["PostgreSQL"]["months"] == 36 and got["PostgreSQL"]["headcount"] == 1      # 여러 번 나오면 경력이 있는 쪽
    assert got["Kafka"]["kind"] == "preferred"
    assert {m["name"] for m in out["mentioned_not_required"]} == {"Oracle Database", "WebLogic"}
    assert "사업 기간" not in DOC and all(s["months"] in (None, 36, 60) for s in out["skills"])


def test_rule_does_not_read_period_numbers_as_experience(sd):
    out = rule_read("사업명: 공공 데이터 개방 (사업 기간 6개월, 47개월 운영) — Spark 경력 2년 이상 1명", sd)
    got = _by(out)
    assert got["공공 업무"]["months"] is None and got["Spark"]["months"] == 24


def test_no_client_falls_back_to_rule(sd):
    out = read_requirements(DOC, sd)
    assert out["method"] == "rule" and out["fallback_reason"] == "no_client" and out["skills"]


def test_ai_answer_is_checked_and_keeps_rule_mentions(sd):
    pg = next(c for c, v in sd.concepts.items() if v["label"] == "PostgreSQL")
    java = next(c for c, v in sd.concepts.items() if v["label"] == "Java")
    kafka = next(c for c, v in sd.concepts.items() if v["label"] == "Kafka")
    ora = next(c for c, v in sd.concepts.items() if v["label"] == "Oracle Database")
    payload = {"skills": [
        {"id": java, "quote": "Java 기반 개발 경력 5년 이상 2명", "kind": "required", "months": 60, "headcount": 2},
        {"id": pg, "quote": "PostgreSQL 경력 3년 이상 1명", "kind": "required", "months": 36, "headcount": 1},
        {"id": kafka, "quote": "Kafka 실시간 처리 경험자 우대", "kind": "preferred", "months": None, "headcount": None},
        {"id": "no_such_skill", "quote": "Java", "kind": "required"},                         # 사전 밖
        {"id": ora, "quote": "Oracle 19c 필수", "kind": "required"},                         # 근거 말이 자료에 없다
        {"id": java, "quote": "Java", "kind": "required"},                                    # 같은 id 두 번
        {"id": pg, "quote": "PostgreSQL", "kind": "required", "months": 999, "headcount": 0}],
        "not_required": [{"id": ora, "quote": "현행 시스템은 오라클 11g"}], "unmapped_terms": ["지도 API"]}
    calls: list = []
    out = read_requirements(DOC, sd, client=_client(payload, calls), model="m", price={"input_per_1m": 0.1, "output_per_1m": 0.5})
    assert out["method"] == "ai" and out["model"] == "m"
    got = _by(out)
    assert set(got) == {"Java", "PostgreSQL", "Kafka"}
    assert got["Java"]["months"] == 60 and got["Kafka"]["kind"] == "preferred"
    assert DOC[got["Java"]["start"]:got["Java"]["end"]] == got["Java"]["quote"]          # 위치는 서버가 다시 잰다
    whys = {d["why"] for d in out["dropped"]}
    assert {"사전에 없는 id", "근거 말이 자료에 없다"} <= whys
    names = {m["name"] for m in out["mentioned_not_required"]}
    assert "Oracle Database" in names and "WebLogic" in names                             # AI가 고르지 않은 규칙 언급도 남긴다
    assert "Java" not in names and out["unmapped_terms"] == ["지도 API"]
    assert out["usage"]["cost_usd"] == pytest.approx((1000 * 0.1 + 200 * 0.5) / 1e6)
    sent = calls[0]["messages"][1]["content"]
    assert "Java 기반 개발 경력" in sent and calls[0]["response_format"] == {"type": "json_object"}


def test_ai_failure_and_long_text(sd):
    def boom(**kw):
        raise TimeoutError("slow")
    bad = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    out = read_requirements(DOC, sd, client=bad, model="m")
    assert out["method"] == "rule" and out["fallback_reason"] == "llm_error:TimeoutError" and out["skills"]
    calls: list = []
    long_doc = DOC + "\n" + ("가" * MAX_AI_TEXT)
    out = read_requirements(long_doc, sd, client=_client({"skills": []}, calls), model="m")
    assert any("앞 40,000자" in w for w in out["warnings"])
    assert len(calls[0]["messages"][1]["content"]) < MAX_AI_TEXT + 20_000
    assert read_requirements("   ", sd)["warnings"]
