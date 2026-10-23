"""발주 자료 필요 기술 읽기(api/rag/rfp_read, 2026-10-23 claude-a -- claude-b 요청 "발주 자료에서 필요 기술 읽기(AI)").
AI는 가짜 client로만 부른다(시험 전체에서 실제 호출은 막혀 있다)."""
import json
from types import SimpleNamespace

import pytest

from api.rag.request_parse import alias_hits
from api.rag.rfp_read import MAX_AI_TEXT, MAX_TEXT, read_requirements, rule_read
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


def test_ai_failure_and_too_long_text(sd):
    def boom(**kw):
        raise TimeoutError("slow")
    bad = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    out = read_requirements(DOC, sd, client=bad, model="m")
    assert out["method"] == "rule" and out["fallback_reason"] == "llm_error:TimeoutError" and out["skills"]
    out = read_requirements(DOC + "가" * MAX_TEXT, sd)
    assert any(f"앞 {MAX_TEXT:,}자만" in w for w in out["warnings"])
    assert read_requirements("   ", sd)["warnings"]


def _cid(sd, label):
    return next(c for c, v in sd.concepts.items() if v["label"] == label)


def _filler(n: int) -> str:
    line = "본 사업은 발주기관의 업무 효율을 높이기 위한 것으로 세부 내용은 협의하여 정한다\n"
    return line * (n // len(line) + 1)


def _router(rules: dict, calls: list):
    """조각 글에 들어 있는 표지 말로 답을 모으는 가짜 AI(동시에 불린다). rules: 표지 말 → 답(skills) 또는 예외."""
    def create(**kw):
        msg = kw["messages"][1]["content"]
        calls.append(msg)
        payload: dict = {"skills": []}
        for mark, ans in rules.items():
            if mark in msg:
                if isinstance(ans, Exception):
                    raise ans
                payload["skills"] = payload["skills"] + ans.get("skills", [])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_long_document_is_read_in_chunks_with_heading_paths(sd):
    """4만 자 뒤의 요구도 AI가 읽는다 -- 조각마다 속한 제목을 붙여 동시에 묻고, 근거 위치는 전체 글 기준(2026-10-23 b 요청 1)."""
    spark, java = _cid(sd, "Spark"), _cid(sd, "Java")
    head = "Ⅰ. 사업 개요\n" + _filler(30_000) + "Ⅱ. 제안 요청 사항\n1. 투입 인력\n"
    text = head + "가. 개발 인력\n- Java 개발 경력 5년 이상 2명\n" + _filler(35_000) + "나. 분석 인력\n- Spark 경력 3년 이상 1명\n"
    assert text.index("Spark 경력") > MAX_AI_TEXT
    calls: list = []
    client = _router({"Java 개발 경력": {"skills": [{"id": java, "quote": "Java 개발 경력 5년 이상 2명", "kind": "required", "months": 60, "headcount": 2}]},
                      "Spark 경력": {"skills": [{"id": spark, "quote": "Spark 경력 3년 이상 1명", "kind": "required", "months": 36, "headcount": 1}]}},
                     calls)
    out = read_requirements(text, sd, client=client, model="m", price={"input_per_1m": 1.0, "output_per_1m": 1.0})
    got = _by(out)
    assert out["method"] == "ai" and set(got) == {"Java", "Spark"}
    for s in out["skills"]:
        assert text[s["start"]:s["end"]] == s["quote"]
    assert len(out["chunks"]) == len(calls) >= 2 and all(c["method"] == "ai" for c in out["chunks"])
    assert out["chunks"][0]["start"] == 0 and out["chunks"][-1]["end"] == len(text)
    assert all(len(m) < MAX_AI_TEXT + 20_000 for m in calls)
    later = next(m for m in calls if "Spark 경력" in m)
    assert "이 조각이 속한 제목: Ⅱ. 제안 요청 사항 > 1. 투입 인력" in later
    assert out["usage"]["in"] == 100 * len(calls) and not any("앞" in w for w in out["warnings"])


def test_single_chunk_prompt_is_unchanged(sd):
    calls: list = []
    read_requirements(DOC, sd, client=_router({}, calls), model="m")
    assert len(calls) == 1 and calls[0].startswith("발주 자료:")
    assert read_requirements("", sd, client=_router({}, calls), model="m")["method"] == "ai"     # 빈 글도 조각 하나(조각 0개로 멈추지 않게)


def test_chunks_merge_and_a_failed_chunk_falls_back_to_rule(sd):
    """같은 기술은 필수 > 우대, 경력·인원은 숫자가 있는 근거에서. 한 조각이 실패하면 그 범위만 규칙 결과 + 경고."""
    etl, kafka = _cid(sd, "ETL"), _cid(sd, "Kafka")
    text = ("1. 과업 범위\n- 일 배치 ETL 파이프라인 구축\n- Kafka 연계 검토\n" + _filler(36_000)
            + "2. 투입 인력\n- ETL 경력 4년 이상 2명\n- Kafka 운영 경력 2년 이상 1명\n" + _filler(42_000)
            + "3. 기타\n- Airflow 경력 2년 이상 1명\n")
    rules = {"일 배치 ETL": {"skills": [{"id": etl, "quote": "일 배치 ETL 파이프라인 구축", "kind": "required"},
                                       {"id": kafka, "quote": "Kafka 연계 검토", "kind": "preferred"}]},
             "ETL 경력 4년": {"skills": [{"id": etl, "quote": "ETL 경력 4년 이상 2명", "kind": "required", "months": 48, "headcount": 2},
                                        {"id": kafka, "quote": "Kafka 운영 경력 2년 이상 1명", "kind": "required", "months": 24, "headcount": 1}]},
             "Airflow 경력": TimeoutError("slow")}
    out = read_requirements(text, sd, client=_router(rules, []), model="m")
    got = _by(out)
    assert got["ETL"]["months"] == 48 and got["ETL"]["headcount"] == 2 and got["ETL"]["quote"] == "ETL 경력 4년 이상 2명"
    assert got["Kafka"]["kind"] == "required" and got["Kafka"]["months"] == 24
    assert got["Airflow"]["months"] == 24                                                  # 실패한 조각은 규칙으로
    assert out["method"] == "ai" and [c["method"] for c in out["chunks"]].count("rule") == 1
    assert any("AI 읽기에 실패" in w for w in out["warnings"])
    every = read_requirements(text, sd, client=_router({"": TimeoutError("down")}, []), model="m")
    assert every["method"] == "rule" and every["fallback_reason"] == "llm_error:TimeoutError"


def test_rule_sentence_ends_at_bullet_and_label_lines(sd):
    """한글·워드 문단은 줄바꿈 하나로 이어진다 -- 다음 줄의 "우대"가 앞 줄 기술을 우대로 바꾸지 않게(b 관찰 3)."""
    got = _by(rule_read("수행 인력: ETL 경력 5년 이상 2명\n우대: Python 개발 경험", sd))
    assert got["ETL"]["kind"] == "required" and got["Python"]["kind"] == "preferred"
    got = _by(rule_read("□ 과업 범위\n ◦ Airflow 기반 스케줄링 구축\n ◦ Kafka 실시간 적재 경험자 우대", sd))
    assert got["Airflow"]["kind"] == "required" and got["Kafka"]["kind"] == "preferred"
    got = _by(rule_read("데이터 플랫폼은 Spark로\n처리하며 경험자를 우대한다.", sd))                   # PDF 줄 넘김은 문장 끝이 아니다
    assert got["Spark"]["kind"] == "preferred"


def test_domain_words_found_only_by_rule_are_not_listed_with_ai(sd):
    """AI가 고르지 않은 규칙 언급 중 업무 도메인 낱말("공공기관"→공공 업무)은 "요구 아님" 목록을 어지럽혀 뺀다(b 관찰 2). 규칙만이면 그대로."""
    text = "공공기관 정책 현안 카드를 만든다. 분석에는 Python을 쓰고, 현행 시스템은 Oracle로 운영 중이다."
    py = _cid(sd, "Python")
    out = read_requirements(text, sd, client=_client({"skills": [{"id": py, "quote": "Python", "kind": "required"}]}), model="m")
    names = {m["name"] for m in out["mentioned_not_required"]}
    assert "Oracle Database" in names and not names & {"공공 업무", "카드 업무"}
    rule_names = {s["name"] for s in read_requirements(text, sd)["skills"]}
    assert {"공공 업무", "카드 업무"} <= rule_names


def test_bullets_without_their_own_words_follow_the_heading(sd):
    """줄마다 문장을 끊으면 "1. 현행 시스템 구성" 아래 장비 목록이 필수로 바뀌었다(2026-10-23 측정에서 발견) -- 제 말이 없는 줄은 바로 위 머리 줄을 따른다."""
    text = ("1. 현행 시스템 구성\n - DBMS: Tibero 6(보조)\n - WAS: JEUS 8\n2. 과업 범위\n - 통계 화면을 Vue로 신규 구축\n"
            "□ 현행 연계 현황\n ◦ 대외 연계는 IBM MQ로 처리하고 있다\n□ 우대 사항\n ◦ Kafka 구축 경험\n")
    out = rule_read(text, sd)
    assert {m["name"] for m in out["mentioned_not_required"]} >= {"Tibero", "JEUS", "IBM MQ"}
    got = _by(out)
    assert got["Vue"]["kind"] == "required" and got["Kafka"]["kind"] == "preferred"
