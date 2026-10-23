"""발주 자료 → 등록 양식 정보 채우기 · 부서장용 자료 요약(2026-10-23, `api/rag/doc_brief.py`).

AI는 가짜만 쓴다(시험에서 실제 AI 호출은 막혀 있다). 근거 말 원문 확인 · 금액 다시 읽기 · 요약 항목 숫자 검사 · 연락처 거르기 · 규칙(AI 없음)."""
import json
from types import SimpleNamespace

from api.rag import doc_brief as db

NOTICE = """(용역) 입찰 공고
❑ 입찰공고번호 : 가상공고 제2026 - 7호
❑ 공 고 명 : 가상 공공기관 문서 데이터 구조화 사업
❍ 입찰건명 : 가상 공공기관 문서 데이터 구조화 사업
❍ 계약기간 : 계약체결일로부터 ~ 2027년 5월 31일까지
❍ 사업예산(부가가치세 포함) : 120,000,000원
❍ 가격입찰서 및 제안서 제출 마감일시 : 2026/10/28(수) 10:00
❍ 발주기관 : 가상안전공단
- 재무관리부 홍길동 과장 (☎ 052-000-0000)
❍ 공동계약 : 불가
제안서 기술평가(70점)와 가격평가(30점)를 종합평가한다.
사업 수행 인력은 보안 서약서를 제출하고 상주 근무한다."""


class FakeAI:
    def __init__(self, payload):
        self.payload, self.calls = payload, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.payload, Exception):
            raise self.payload
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


def test_amounts_and_number_checks():
    assert db.amounts_won("사업예산 : 100,000,000원(VAT 포함)") == [100_000_000]
    assert db.amounts_won("3,000만원 이내") == [30_000_000] and db.amounts_won("3천만 원") == [30_000_000]
    assert db.amounts_won("1억 원") == [100_000_000] and db.amounts_won("1억 5천만원") == [150_000_000]
    assert db.amounts_won("95,000,000원(부가세 포함)") == [95_000_000]
    assert db.vat_of("(부가가치세 포함)") is True and db.vat_of("VAT 별도") is False and db.vat_of("1억 원") is None
    # 요약 항목 숫자 검사: 금액은 금액끼리, 날짜·점수는 정수끼리
    assert db.numbers_supported("사업예산은 1억 원이다", "사업예산 : 100,000,000원")
    assert db.numbers_supported("마감은 2026년 4월 28일이다", "마감일시 : 2026/04/28(화) 10:00")
    assert db.numbers_supported("기술 70점·가격 30점", "기술평가(70점)와 가격평가(30점)")
    assert not db.numbers_supported("사업예산은 2억 원이다", "사업예산 : 100,000,000원")
    assert not db.numbers_supported("기간은 7개월이다", "계약체결일로부터 ~ 2026년 11월 13일까지")      # 계산한 숫자는 받지 않는다
    assert db.numbers_supported("보안 서약서를 낸다", "보안 서약서를 제출")


def test_rule_meta_reads_a_bid_notice():
    m = db.rule_meta(NOTICE)
    assert m["name"]["value"] == "가상 공공기관 문서 데이터 구조화 사업"
    assert m["notice_no"]["value"] == "가상공고 제2026 - 7호"
    assert m["period"]["value"] == "계약체결일로부터 ~ 2027년 5월 31일까지" and m["duration_months"] is None
    assert m["budget"]["value"] == 120_000_000 and m["budget"]["vat_included"] is True
    assert m["deadline"]["value"] == "2026/10/28(수) 10:00"
    assert m["client"]["value"] == "가상안전공단" and m["sector"]["value"] == "대외공공"
    for f in m.values():                                                       # 근거 위치가 원문과 맞다
        if f:
            assert NOTICE[f["start"]:f["end"]] == f["quote"] and f["source"] == "rule"
    small = db.rule_meta("가. 사업 규모\no 3,000만원 이내(VAT 포함)\n1. 수행 기간\no 2026년 5월 20일 ~ 2026년 10월 20일 (계약 체결일부터 5개월)")
    assert small["budget"]["value"] == 30_000_000 and small["budget"]["vat_included"] is True     # 제목 다음 줄 금액
    assert small["duration_months"]["value"] == 5
    assert db.rule_meta("발주처의 조치에 대하여 이의를 제기할 수 없다")["client"] is None          # 쌍점 없는 문장은 발주기관이 아니다


def test_ai_meta_keeps_only_fields_found_in_the_text():
    ai = FakeAI({
        "name": {"value": "가상 공공기관 문서 데이터 구조화 사업", "quote": "입찰건명 : 가상 공공기관 문서 데이터 구조화 사업"},
        "client": {"value": "가상안전공단", "quote": "발주기관 : 가상안전공단"},
        "sector": {"value": "대외공공", "quote": "가상안전공단"},
        "duration_months": {"value": 7, "quote": "계약체결일로부터 ~ 2027년 5월 31일까지"},
        "period": {"value": "계약체결일로부터 ~ 2027년 5월 31일까지", "quote": "계약체결일로부터 ~ 2027년 5월 31일까지"},
        "budget": {"value": 120_000_000, "vat_included": False, "quote": "사업예산(부가가치세 포함) : 120,000,000원"},
        "notice_no": {"value": "가상공고 제2026 - 7호", "quote": "입찰공고번호 : 가상공고 제2026 - 7호"},
        "deadline": {"value": "2026/10/28(수) 10:00", "quote": "제출 마감일시 : 2026/10/28(수) 10:00"},
    })
    out = db.read_project_meta(NOTICE, client=ai, model="m")
    f = out["fields"]
    assert out["method"] == "ai" and out["dropped"] == [] and ai.calls
    assert f["duration_months"]["value"] == 7 and f["duration_months"]["source"] == "ai"
    assert f["budget"]["value"] == 120_000_000 and f["budget"]["vat_included"] is True     # 부가세는 근거 말이 이긴다
    assert "홍길동" not in json.dumps(ai.calls[0]["messages"][0], ensure_ascii=False)          # 시스템 지시문에 사람 정보 없음


def test_ai_meta_drops_made_up_or_inconsistent_fields_and_falls_back_to_rules():
    ai = FakeAI({
        "name": {"value": "자료에 없는 사업명", "quote": "입찰건명"},
        "sector": {"value": "공공", "quote": "가상안전공단"},
        "duration_months": {"value": 9, "quote": "6개월"},
        "budget": {"value": 200_000_000, "quote": "사업예산(부가가치세 포함) : 120,000,000원"},
        "deadline": "2026-10-28",
        "client": None,
    })
    out = db.read_project_meta(NOTICE, client=ai, model="m")
    why = {d["field"]: d["why"] for d in out["dropped"]}
    assert why["name"] == "값이 근거 말에 없다" and "고객 구분" in why["sector"] and why["budget"] == "근거 말의 금액과 다르다"
    assert why["duration_months"] == "근거 말이 자료에 없다" and why["deadline"] == "모양이 다르다"
    f = out["fields"]
    assert f["name"]["source"] == "rule" and f["name"]["value"] == "가상 공공기관 문서 데이터 구조화 사업"   # 규칙 값이 남는다
    assert f["budget"]["value"] == 120_000_000 and f["sector"]["source"] == "rule"
    assert f["notice_no"]["source"] == "rule"                                                   # AI가 안 준 칸도 규칙 값


def test_ai_meta_failure_or_no_client_uses_rules():
    out = db.read_project_meta(NOTICE, client=FakeAI(RuntimeError("down")), model="m")
    assert out["method"] == "rule" and out["fallback_reason"] == "llm_error:RuntimeError"
    out = db.read_project_meta(NOTICE, client=FakeAI("[]"), model="m")
    assert out["method"] == "rule" and out["fallback_reason"] == "llm_error:TypeError"
    out = db.read_project_meta(NOTICE)
    assert out["fallback_reason"] == "no_client" and out["fields"]["budget"]["value"] == 120_000_000


def _sum_payload(**over):
    base = {"doc_type": "입찰공고문", "relevance": "high", "one_line": "가상안전공단 문서 구조화 사업 공고(1억 2천만 원)",
            "sections": {
                "budget": [{"text": "사업예산은 부가세 포함 1억 2천만 원이다.", "quote": "사업예산(부가가치세 포함) : 120,000,000원"},
                           {"text": "사업예산은 2억 원이다.", "quote": "사업예산(부가가치세 포함) : 120,000,000원"},     # 숫자 지어냄
                           {"text": "공동계약은 불가하다.", "quote": "공동계약 : 불가"}],
                "schedule": [{"text": "제출 마감은 2026년 10월 28일 10시다.", "quote": "제출 마감일시 : 2026/10/28(수) 10:00"},
                             {"text": "기간은 7개월이다.", "quote": "계약체결일로부터 ~ 2027년 5월 31일까지"},          # 계산한 숫자
                             {"text": "착수보고는 1주 안이다.", "quote": "착수보고서를 7일 안에 낸다"}],                  # 자료에 없는 말
                "evaluation": [{"text": "기술 70점·가격 30점으로 평가한다.", "quote": "기술평가(70점)와 가격평가(30점)"}],
                "staffing": [{"text": "문의는 홍길동 과장 052-000-0000", "quote": "재무관리부 홍길동 과장 (☎ 052-000-0000)"},   # 연락처
                             {"text": "수행 인력은 보안 서약서를 내고 상주한다.", "quote": "보안 서약서를 제출하고 상주 근무한다"}],
                "constraints": [{"text": "수행 인력은 상주 근무한다.", "quote": "보안 서약서를 제출하고 상주 근무한다"}],    # 같은 근거 말
                "nonsense": [{"text": "x", "quote": "공동계약 : 불가"}],
            }}
    base.update(over)
    return base


def test_summary_keeps_only_checked_items():
    out = db.summarize_document(NOTICE, "입찰공고문", client=FakeAI(_sum_payload()), model="m")
    by = {s["key"]: [i["text"] for i in s["items"]] for s in out["sections"]}
    assert [s["key"] for s in out["sections"]] == [k for k, _ in db.SECTIONS]                   # 정해진 칸만, 순서대로
    assert by["budget"] == ["사업예산은 부가세 포함 1억 2천만 원이다.", "공동계약은 불가하다."]
    assert by["schedule"] == ["제출 마감은 2026년 10월 28일 10시다."]
    assert by["staffing"] == ["수행 인력은 보안 서약서를 내고 상주한다."] and by["constraints"] == []
    why = sorted(d["why"] for d in out["dropped"])
    assert why == sorted(["근거 말에 없는 숫자", "근거 말에 없는 숫자", "근거 말이 자료에 없다", "이름·연락처"])
    assert out["one_line"] == "가상안전공단 문서 구조화 사업 공고(1억 2천만 원)" and out["relevance"] == "high"
    for s in out["sections"]:
        for i in s["items"]:
            assert NOTICE[i["start"]:i["end"]] == i["quote"]
    assert "홍길동" not in json.dumps(out["sections"], ensure_ascii=False)


def test_summary_one_line_with_a_made_up_number_is_replaced_and_low_relevance_is_kept():
    out = db.summarize_document(NOTICE, "기타", client=FakeAI(_sum_payload(one_line="3억 원 사업", relevance="low")), model="m")
    assert out["one_line"] == "제출 마감은 2026년 10월 28일 10시다." and out["relevance"] == "low"   # 칸 순서상 첫 항목(기간·일정이 예산 앞)
    assert {"section": "one_line", "why": "길거나 항목 근거에 없는 숫자·이름·연락처"} in out["dropped"]


def test_summary_caps_items_and_falls_back_to_rules():
    many = [{"text": f"과업 {i}", "quote": "공동계약 : 불가"} for i in range(30)]
    out = db.summarize_document(NOTICE, client=FakeAI(_sum_payload(sections={"scope": many})), model="m")
    assert sum(len(s["items"]) for s in out["sections"]) <= 1                                  # 같은 근거 말은 하나만
    out = db.summarize_document(NOTICE, "입찰공고문")
    assert out["method"] == "rule" and out["fallback_reason"] == "no_client"
    texts = [i["text"] for s in out["sections"] for i in s["items"]]
    assert texts and not any("052-" in t for t in texts)                                       # 규칙도 연락처 줄은 뺀다
    assert out["one_line"] == "가상 공공기관 문서 데이터 구조화 사업"


# --- 폴백 리뷰(2026-10-23) 반영 ----------------------------------------------------------------------------------------------------

def test_mixed_korean_amounts_are_read_whole_or_not_at_all():
    """M1: "1억 2천 5백만원"을 1억으로 읽어 맞는 AI 값을 버리고 틀린 규칙 값을 넣었다 -- 통째로 읽고, 애매하면 금액으로 보지 않는다."""
    assert db.amounts_won("사업예산 : 1억 2천 5백만원") == [125_000_000]
    assert db.amounts_won("1억 2백만원") == [102_000_000] and db.amounts_won("12억 3,456만 7,890원") == [1_234_567_890]
    assert db.amounts_won("1,234만 5,678원") == [12_345_678] and db.amounts_won("5백만원") == [5_000_000]
    assert db.amounts_won("1억 2천") == [] and db.amounts_won("3만 명") == [] and db.amounts_won("기술 70점") == []
    ai = FakeAI({"budget": {"value": 125_000_000, "quote": "사업예산 : 1억 2천 5백만원(부가세 포함)"}})
    out = db.read_project_meta("사업예산 : 1억 2천 5백만원(부가세 포함)\n" + NOTICE, client=ai, model="m")
    assert out["fields"]["budget"]["value"] == 125_000_000 and out["fields"]["budget"]["source"] == "ai"


def test_number_check_compares_dates_times_units_and_words():
    """S2: 숫자 집합만 보면 통과하던 것들."""
    ok = db.numbers_supported
    assert not ok("요율은 5.2%다", "요율 2.5%")
    assert not ok("마감은 2026년 4월 14일이다", "마감일시 2026.04.28 14:00")
    assert not ok("2027년 5월 31일까지", "2026년 5월 31일 ~ 2027년 1월")
    assert not ok("PM 70명이 필요하다", "기술평가 70점")
    assert not ok("3천 명을 투입한다", "3명을 투입한다")
    assert not ok("다섯 명을 투입한다", "인력을 투입한다") and ok("다섯 명을 투입한다", "다섯 명을 투입한다")
    assert not ok("삼천만 원 규모다", "사업 규모는 크다")
    assert ok("마감은 4월 28일 10시다", "제출 마감일시 : 2026/04/28(화) 10:00")
    assert ok("계약은 2026년 11월 13일까지다", "계약체결일로부터 ~ 2026년 11월 13일까지")
    assert not ok("마감은 4월 28일 11시다", "제출 마감일시 : 2026/04/28(화) 10:00")
    assert ok("1억 2천만 원이다", "120,000,000원") and ok("기술 70점·가격 30점", "기술평가(70점)와 가격평가(30점)")


def test_duration_is_confirmed_only_with_one_n_months_and_a_period_word():
    """1차 S1 · 2차 S-3: 근거 말에 서로 다른 N개월이 하나이고 기간을 뜻하는 말이 있을 때만 확인됨(computed False) -- 나머지는 AI가 센 값."""
    def dur(text, value, quote):
        return db.read_project_meta(text, client=FakeAI({"duration_months": {"value": value, "quote": quote}}), model="m")["fields"]["duration_months"]
    f = dur(NOTICE, 7, "계약체결일로부터 ~ 2027년 5월 31일까지")
    assert f["value"] == 7 and f["computed"] is True                                          # 날짜로 셈
    t = "1. 수행 기간: 계약 체결일부터 5개월\n" + NOTICE
    assert dur(t, 5, "수행 기간: 계약 체결일부터 5개월")["computed"] is False
    t = "1. 수행 기간: 착수보고 1개월 이내, 총 8개월\n수행 인력: PM은 경력 8개월 이상\n" + NOTICE
    assert dur(t, 8, "착수보고 1개월 이내, 총 8개월")["computed"] is True                        # N개월이 둘
    assert dur(t, 8, "PM은 경력 8개월 이상")["computed"] is True                                  # 기간을 뜻하는 말이 없다
    out = db.read_project_meta(t, client=FakeAI({"duration_months": {"value": 3, "quote": "착수보고 1개월 이내, 총 8개월"}}), model="m")
    assert out["dropped"] == [{"field": "duration_months", "why": "근거 말의 개월(1, 8)과 다르다"}]
    assert db.rule_meta(t)["duration_months"] is None                                          # 규칙도 하나일 때만
    assert db.rule_meta("과업기간: 120개월 이내")["duration_months"] is None
    assert db.rule_meta("계약기간: 계약일로부터 6개월(무상 하자보수 12개월)")["duration_months"] is None
    assert db.rule_meta("1. 수행 기간\no 2026년 5월 20일 ~ 2026년 10월 20일 (계약 체결일부터 5개월)")["duration_months"]["value"] == 5


def test_long_quotes_short_values_and_values_outside_their_quote_are_dropped():
    """S3·S5."""
    ai = FakeAI({"budget": {"value": 120_000_000, "quote": NOTICE},
                 "name": {"value": "문서  데이터 구조화", "quote": "입찰건명 : 가상 공공기관 문서 데이터 구조화 사업"},
                 "client": {"value": "가상안전공단", "quote": "입찰건명 : 가상 공공기관 문서 데이터 구조화 사업"},
                 "notice_no": {"value": "제2026 - 7호", "quote": "입찰공고번호 : 가상공고 제2026 - 7호"}})
    out = db.read_project_meta(NOTICE, client=ai, model="m")
    why = {d["field"]: d["why"] for d in out["dropped"]}
    assert why == {"budget": "근거 말이 너무 길다", "client": "값이 근거 말에 없다"}
    assert out["fields"]["name"]["value"] == "문서 데이터 구조화"                                     # 근거 안 -- 원문 표기로(띄어쓰기도 원문)
    assert out["fields"]["notice_no"]["value"] == "제2026 - 7호"
    ai = FakeAI({"name": {"value": "가상", "quote": "입찰건명 : 가상 공공기관 문서 데이터 구조화 사업"}})
    assert {d["field"]: d["why"] for d in db.read_project_meta(NOTICE, client=ai, model="m")["dropped"]} == {"name": "값이 너무 짧다"}
    long_item = {"text": "사업예산은 1억 2천만 원이다.", "quote": NOTICE}
    out = db.summarize_document(NOTICE, client=FakeAI(_sum_payload(sections={"budget": [long_item]})), model="m")
    assert out["dropped"][0]["why"] == "근거 말이 없거나 길다"


def test_one_line_numbers_must_come_from_surviving_items():
    """S4: 자료 어딘가의 숫자(자격 기준 10억)로 만든 머리말을 막는다."""
    text = NOTICE + "\n입찰참가자격: 최근 3년간 10억원 이상 실적"
    payload = _sum_payload(one_line="사업비 10억 원 규모 공고", sections={"evaluation": [
        {"text": "기술 70점·가격 30점으로 평가한다.", "quote": "기술평가(70점)와 가격평가(30점)"}]})
    out = db.summarize_document(text, client=FakeAI(payload), model="m")
    assert out["one_line"] == "기술 70점·가격 30점으로 평가한다."


def test_rules_skip_qualification_and_guarantee_lines_and_sentences():
    """S6."""
    t = "입찰참가자격 : 사업 규모 10억원 이상 실적 보유\n❍ 사업예산(부가가치세 포함) : 120,000,000원"
    assert db.rule_meta(t)["budget"]["value"] == 120_000_000
    t = "계약은 예산의 범위 내에서 집행한다\n입찰보증금 5,000,000원"
    assert db.rule_meta(t)["budget"] is None
    assert db.rule_meta("사업명을 정확히 기재한다")["name"] is None
    assert db.rule_meta("◦ (소요예산) 95,000,000원(부가세 포함)")["budget"]["value"] == 95_000_000
    assert db.rule_meta("가. 사업 규모\no 3,000만원 이내(VAT 포함)")["budget"]["value"] == 30_000_000


def test_personal_filter_catches_contacts_but_not_staffing_requirements():
    """1차 S7 · 2차 S-1: 연락처·공무원 실명은 걸고, 인력 요구 문장("PM은 부장 이상", "품질관리 담당자 1명")은 걸지 않는다."""
    for t in ("문의 052.000.0000", "문의 052 703 0745", "Tel 1588-0000", "1588-0800", "hong (at) example.go.kr", "hong＠example.go.kr",
              "담당 주무관 홍길동", "담당자 : 홍길동", "문의는 홍길동 과장에게", "재무관리부 이성종 과장 (☎ 052-703-0558)"):
        assert db.is_personal(t), t
    for t in ("기술평가 70점과 가격평가 30점", "사업예산 100,000,000원", "제출 마감 2026/04/28 10:00", "정보통신정책연구원",
              "내용을 과장되게 쓰지 않는다", "PM은 부장 이상 경력자로 한다", "과장 이상 경력자 2명", "사업관리 팀장은 정보관리기술사 보유",
              "수행사 PM 및 각 분야 팀장은 상주한다", "개인정보 담당자 지정", "품질관리 담당자 1명 상주", "비상 연락처를 제출한다",
              "최근 3년(2025 2026 2027) 실적", "인원 100 200 3000", "고객사 담당자 면담으로 파악했다", "규격 담당자에게 의견을 낸다"):
        assert not db.is_personal(t), t


def test_numeric_year_month_units_and_hours():
    """2차 S-2: "2026-11"·"2026. 5."·"2026.11"도 연·월, "2명" = "2인", "8시간"은 시각이 아니다."""
    ok = db.numbers_supported
    assert ok("2026년 11월부터 2027년 3월까지", "2026-11 ~ 2027-03") and ok("2026년 5월 ~ 11월", "2026. 5. ~ 2026. 11.")
    assert ok("2026년 11월 시작", "2026.11 착수") and ok("고급 2명", "고급 2인")
    assert not ok("12월 시작", "2026. 5. ~ 2026. 11.") and not ok("8시에 시작", "교육 8시간")


def test_name_without_a_label_in_the_quote_is_unlabeled():
    """2차 NIT 1: 근거 말에 사업명 표지가 없으면 표시(화면이 미리 체크하지 않는다)."""
    text = "“비정형 데이터 기반\nAI 대전환 4대 메가트렌드 진단”\n" + NOTICE
    ai = FakeAI({"name": {"value": "비정형 데이터 기반 AI 대전환 4대 메가트렌드 진단", "quote": "비정형 데이터 기반\nAI 대전환 4대 메가트렌드 진단"}})
    assert db.read_project_meta(text, client=ai, model="m")["fields"]["name"]["unlabeled"] is True
    ai = FakeAI({"name": {"value": "가상 공공기관 문서 데이터 구조화 사업", "quote": "입찰건명 : 가상 공공기관 문서 데이터 구조화 사업"}})
    assert "unlabeled" not in db.read_project_meta(NOTICE, client=ai, model="m")["fields"]["name"]
    assert db.rule_meta("가. 사업명 : 가상 사업 하나")["name"]["value"] == "가상 사업 하나"            # 번호 붙은 표지(2차 NIT 3)
    assert db.rule_meta("예 산 : 금100,000,000원")["budget"]["value"] == 100_000_000


def test_vat_and_sector_words():
    """NIT 11·10."""
    assert db.vat_of("기초금액(부가세 포함) · 낙찰 금액은 부가세 별도") is None
    q = "사업예산 : 120,000,000원"
    f = db.read_project_meta(q + "\n" + NOTICE, client=FakeAI({"budget": {"value": 120_000_000, "vat_included": True, "quote": q}}), model="m")
    assert f["fields"]["budget"]["vat_included"] is None                                         # AI 주장만으로는 정하지 않는다
    sector = lambda name: db.rule_meta(f"사업명 : {name}")["sector"]                              # noqa: E731
    assert sector("통신 공사 감리 사업") is None and sector("전자정부 표준프레임워크 전환") is None and sector("내부통제 시스템 구축") is None
    assert sector("한국전력공사 데이터 플랫폼")["value"] == "대외공공" and sector("대형 시중은행 A 데이터")["value"] == "대외금융"
    assert sector("한국전력공사의 데이터 플랫폼")["value"] == "대외공공" and sector("현대카드 차세대")["value"] == "대외금융"
    assert sector("고용보험 전산망 개선") is None                                                  # 보험사가 아니다(2차 NIT 4)


def test_text_values_keep_the_original_wording_on_one_line():
    text = "“비정형 데이터 기반\nAI 대전환 4대 메가트렌드 진단”\n제안요청서"
    ai = FakeAI({"name": {"value": "비정형 데이터 기반 AI 대전환 4대 메가트렌드 진단", "quote": "비정형 데이터 기반\nAI 대전환 4대 메가트렌드 진단"}})
    assert db.read_project_meta(text, client=ai, model="m")["fields"]["name"]["value"] == "비정형 데이터 기반 AI 대전환 4대 메가트렌드 진단"
