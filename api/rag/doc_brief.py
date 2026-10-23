"""발주 자료 → 등록 양식 정보 채우기 · 부서장용 자료 요약(2026-10-23, claude-b -- a 영역에 b의 새 파일, 사용자 결정 "이번엔 b가 전부").

사용자: "자료에서 메타 정보 따와서 원래 우리가 신규 사업 등록시에 입력했던 프로젝트 이름, 기간, 예산 등을 대신 작성해줄수있나? 물론 사용자가 보고 고칠수 있도록"
· "각각의 자료를 llm으로 잘 압축 요약한(이 서비스를 쓰는 부서장입장에서 필요로 하는 정보를 잘 요약해서 보여주기)".

필요 기술 읽기(`api/rag/rfp_read`, claude-a)와 같은 원칙:
- AI는 자료 글자만 받는다(사람 정보 없음). 결과는 **제안**이고 사람이 고른다·고친다.
- 칸·항목마다 근거 말(자료 그대로)을 서버가 원문에서 찾아야 받는다(`request_parse._locate`). 금액은 근거 말에서 다시 읽은 금액과 같아야,
  요약 항목의 숫자는 근거 말에 있는 숫자여야 받는다 -- 요약은 자유 글이라 지어낸 숫자가 가장 위험하다.
- 담당자 전화번호·이메일이 든 항목은 버린다(실제 공고문에 공무원 실명·번호가 있다).
- AI가 없거나(키 없음·실데이터 외부 차단) 실패하면 규칙(AI 없음).
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata

from api.rag.request_parse import _locate

log = logging.getLogger(__name__)

MAX_TEXT = 200_000           # b의 글자 뽑기 상한과 같다(api.project_docs.MAX_TEXT_CHARS)
MAX_AI_TEXT = 40_000         # AI에 보내는 앞부분(rfp_read와 같은 여유값)
PROMPT_VERSION = "doc-brief-2"   # 저장한 요약을 다시 쓸지 가르는 표지 -- 지시문·검사를 바꾸면 올린다(2: 폴백 리뷰 반영 검사)
SECTORS = ("대내", "대외금융", "대외공공")
BUDGET_MAX = 10 ** 13
QUOTE_MAX = 150              # 근거 말 상한 -- 길면 숫자 검사가 무력해진다(폴백 리뷰 S3: 공고 전체를 근거로 준 10억이 통과)
SECTIONS = (("overview", "사업 개요"), ("scope", "주요 과업"), ("schedule", "기간·일정"), ("budget", "예산·계약"),
            ("staffing", "인력·역량 요구"), ("evaluation", "평가 방식"), ("constraints", "유의 사항"), ("deliverables", "산출물"))
SECTION_TITLE = dict(SECTIONS)
ITEMS_PER_SECTION = 5
# 연락처·실명(폴백 리뷰 1차 S7 · 2차 S-1): 번호 꼴(0으로 시작하는 지역번호·하이픈 번호)·Tel·이메일은 늘 걸고, 이름은
# 공무원 직급(주무관·사무관·서기관) 곁 · "담당(자) : 이름" · 같은 글에 연락 단서(문의·☎·전화·이메일)가 있을 때의 "이름 + 직급"만 건다.
# 예전 판은 "PM은 부장 이상 경력자", "품질관리 담당자 1명 상주", "비상 연락처를 제출" 같은 인력 요구까지 지웠다.
_CONTACT = re.compile(
    r"(?<!\d)0\d{1,2}[\s.)-]+\d{3,4}[\s.-]+\d{4}(?!\d)|(?<![\d-])\d{2,4}-\d{3,4}-\d{4}(?!\d)|(?<![\d-])1[5-9]\d{2}-\d{4}(?!\d)"
    r"|☎|☏|\bTel\b|\bFax\b|전화\s*[:：]"
    r"|[\w.+-]+\s*(?:@|＠|\(at\)|\[at\])\s*[\w-]+(?:\.[\w-]+)+", re.I)
_CIVIL = re.compile(r"[가-힣]{2,4}\s*(?:주무관|사무관|서기관)|(?:주무관|사무관|서기관)\s+[가-힣]{2,4}(?![가-힣])|담당(?:자)?\s*[:：]\s*[가-힣]{2,4}(?![가-힣])")
_NAME_TITLE = re.compile(r"[가-힣]{2,4}\s+(?:과장|대리|차장|부장|팀장|실장)(?=[이은는의께에님,.)·\s]|$)|(?:과장|대리|차장|부장|팀장|실장)\s+[가-힣]{2,4}(?![가-힣])")
_CUE = re.compile(r"문의|☎|☏|전화|연락|이메일|e-?mail|@|＠", re.I)
_NOT_NAME = re.compile(r"(?:경력|기술|이상|이하|급|명|인|분야|개발|사업|품질|보안|정보|관리|발주|수행|각)")


def is_personal(t: str) -> bool:
    """글에 연락처나 (연락 단서와 함께 쓴) 사람 이름이 들어 있나."""
    t = t or ""
    if _CONTACT.search(t) or _CIVIL.search(t):
        return True
    if _CUE.search(t):
        for m in _NAME_TITLE.finditer(t):
            if not _NOT_NAME.search(m.group(0)):
                return True
    return False


# --- 금액 읽기(폴백 리뷰 M1: "1억 2천 5백만원"을 1억으로 읽었다) -------------------------------------------------------------
# 숫자와 자리말(억·천·백·십·만)이 이어진 덩어리를 통째로 읽는다. 원으로 끝나거나 억으로 끝나야 금액이다("3만 명"은 금액이 아니다).
# 읽다 애매하면(억 뒤 "2천"처럼 만 자리가 빠짐) 금액으로 보지 않는다 -- 틀린 값을 맞다고 하지 않게.
_NUM = r"\d[\d,]*(?:\.\d+)?"
_AMT_SPAN = re.compile(rf"(?<![\d.,]){_NUM}(?:\s*[억천백십만]+(?:\s*{_NUM})?)*(?:\s*원|(?<=억)(?!\s*\d))")
_SMALL_FULL = re.compile(r"(?:\d+(?:\.\d+)?[천백십]?|[천백십])+")
_SMALL_TOK = re.compile(r"(\d+(?:\.\d+)?)?([천백십])?")
_SCALE = {"천": 1000, "백": 100, "십": 10}
_VAT_IN = re.compile(r"부가(?:가치)?세\s*포함|VAT\s*포함", re.I)
_VAT_OUT = re.compile(r"부가(?:가치)?세\s*(?:별도|제외|미포함)|VAT\s*(?:별도|제외|미포함)", re.I)


def _small(s: str) -> float | None:
    """만 아래 자리: "3456" · "2천5백" · "천" → 수. 읽지 못하면 None."""
    if not s:
        return 0.0
    if not _SMALL_FULL.fullmatch(s):
        return None
    total = 0.0
    for n, u in _SMALL_TOK.findall(s):
        if n or u:
            total += (float(n) if n else 1.0) * _SCALE.get(u, 1)
    return total


def parse_amount(span: str) -> int | None:
    """금액 덩어리 하나 → 원. "100,000,000원" · "3,000만원" · "3천만 원" · "1억" · "1억 2천 5백만원" · "12억 3,456만 7,890원" · "5백만원"."""
    s = re.sub(r"[\s,원]", "", span or "")
    total = 0.0
    if s.count("억") > 1 or s.count("만") > 1:
        return None
    if "억" in s:
        head, s = s.split("억", 1)
        h = _small(head)
        if not head or h is None:
            return None
        total += h * 10 ** 8
        if s and "만" not in s and re.search(r"[천백십]", s):
            return None                                   # "1억 2천" -- 2천만인지 2천 원인지 모른다
    if "만" in s:
        head, s = s.split("만", 1)
        h = _small(head)
        if not head or h is None:
            return None
        total += h * 10 ** 4
    rest = _small(s)
    if rest is None:
        return None
    total += rest
    v = int(round(total))
    return v if 0 < v <= BUDGET_MAX else None


def amounts_won(s: str) -> list[int]:
    """글 안의 금액(원)들 -- 읽지 못한 덩어리는 빠진다."""
    out = []
    for m in _AMT_SPAN.finditer(s or ""):
        v = parse_amount(m.group(0))
        if v is not None:
            out.append(v)
    return out


def vat_of(s: str) -> bool | None:
    """근거 말의 부가세 표시. 포함·별도가 둘 다 있으면 모른다(None) -- AI의 주장은 쓰지 않는다(폴백 리뷰 NIT 11)."""
    inc, out = bool(_VAT_IN.search(s or "")), bool(_VAT_OUT.search(s or ""))
    return None if inc == out else inc


# --- 숫자 검사(요약 항목) -- 폴백 리뷰 S2: 숫자 집합만 보면 "5.2%" ← "2.5%", "2026년 4월 14일" ← "2026.04.28 14:00"이 통과했다 ----------------------
# 날짜는 (년, 월, 일) 묶음으로, 시각은 (시, 분)으로, 금액은 금액끼리, 나머지 숫자는 (값, 단위)로 맞춰 본다. 한글로 쓴 수("다섯 명", "삼천만 원")는 근거 말에 그대로 있어야 한다.
_D_FULL = re.compile(r"(?<!\d)(\d{4})\s*(?:[./-]|년)\s*(\d{1,2})\s*(?:[./-]|월)\s*(\d{1,2})(?!\d)\s*일?")
_D_YM = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월(?!\s*\d)")
# 숫자로만 쓴 연·월("2026-11", "2026. 5.", "2026.11") -- 일 없이 끝날 때(폴백 리뷰 2차 S-2: 시연 자료 "사업 기간 2026-11 ~ 2027-03")
_D_YMN = re.compile(r"(?<![\d.])(\d{4})\s*[./-]\s*(\d{1,2})(?!\d)\s*\.?(?!\s*\d)")
_D_MD = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*(?::\s*(\d{2})|시(?!간)(?:\s*(\d{1,2})\s*분)?)")       # "8시간"은 시각이 아니다
_UNITS = r"%|퍼센트|점|명|개월|개|일|주|년|월|시간|분|건|부|배|회|차|인|호|MB|GB|KB|쪽|장|자|위|억|만|천"
_TOKEN = re.compile(rf"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*({_UNITS})?")
# 한글로 쓴 수 -- 숫자 바로 뒤의 자리말("2천만 원"의 "천만")은 아니다
_UNIT_SAME = {"퍼센트": "%", "인": "명"}          # 같은 뜻의 단위("2명" = "2인")
_WORD_NUM = re.compile(r"(?<![\d가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무)\s*(?:명|개|곳|차례|번|가지|달|해)"
                       r"|(?<![\d가-힣])[일이삼사오육칠팔구십백천만억]{2,}\s*(?:원|명|개월|점|년)")


def _facts(s: str) -> dict:
    s = unicodedata.normalize("NFKC", s or "")
    out = {"full": set(), "ym": set(), "md": set(), "time": set(), "money": set(), "tok": set(), "years": set()}
    for rx, key in ((_D_FULL, "full"), (_D_YM, "ym"), (_D_YMN, "ym"), (_D_MD, "md")):
        def take(m, key=key):
            out[key].add(tuple(int(g) for g in m.groups()))
            return " "
        s = rx.sub(take, s)
    def take_time(m):
        out["time"].add((int(m.group(1)), int(m.group(2) or m.group(3) or 0)))
        return " "
    s = _TIME.sub(take_time, s)
    out["money"] = set(amounts_won(s))
    s = _AMT_SPAN.sub(" ", s)
    for v, u in _TOKEN.findall(s):
        v = v.replace(",", "")
        v = str(int(v)) if v.isdigit() else v
        out["tok"].add((v, _UNIT_SAME.get(u, u)))
        if len(v) == 4 and v.isdigit():
            out["years"].add(int(v))
    return out


def numbers_supported(claim: str, source: str) -> bool:
    """claim의 숫자(날짜·시각·금액·단위 붙은 수)가 모두 source에 있는가."""
    c, s = _facts(claim), _facts(source)
    for y, m, d in c["full"]:
        if (y, m, d) not in s["full"] and not ((m, d) in s["md"] and y in s["years"] | {f[0] for f in s["full"]} | {f[0] for f in s["ym"]}):
            return False
    for y, m in c["ym"]:
        if (y, m) not in s["ym"] and not any(f[:2] == (y, m) for f in s["full"]):
            return False
    for m, d in c["md"]:
        if (m, d) not in s["md"] and not any(f[1:] == (m, d) for f in s["full"]):
            return False
    for t in c["time"]:
        if t not in s["time"]:
            return False
    if not c["money"] <= s["money"]:
        return False
    src_months = {f[1] for f in s["full"]} | {f[1] for f in s["ym"]} | {f[0] for f in s["md"]}
    for v, u in c["tok"]:
        if u == "월" and v.isdigit() and int(v) in src_months:
            continue                                   # "2026년 5월 ~ 11월"의 11월 ← 근거 "2026. 11."
        if u and (v, u) not in s["tok"]:
            return False
        if not u and not any(sv == v for sv, _ in s["tok"]):
            return False
    src = re.sub(r"\s+", "", source or "")
    return all(re.sub(r"\s+", "", w) in src for w in _WORD_NUM.findall(claim or ""))


# --- 규칙(AI 없음) -------------------------------------------------------------------------------------------------------
_BULLET = r"(?:[❍○◦·∙•\-\*□■▪❑ㅇo※▸]\s*)?"
_NAME_LABELS = (r"입\s*찰\s*건\s*명", r"공\s*고\s*명", r"용\s*역\s*명", r"사\s*업\s*명", r"과\s*업\s*명", r"과\s*제\s*명")
# 예산 표지(폴백 리뷰 S6): 맨 "예산"은 쓰지 않고("예산의 범위 내에서"), 줄 머리 · 괄호 안 · 쌍점 앞에 있을 때만, 보증금·실적·자격·"이상" 줄은 빼고, 표지 뒤 첫 금액
_BUDGET_WORDS = r"사업\s*예산|소요\s*예산|총\s*사업비|사업비|사업\s*규모|추정\s*가격|기초\s*금액|용역비|사업\s*금액"
_ENUM = r"(?:[가-하]\.|\d{1,2}[.)]|\(\d{1,2}\)|[①-⑳])?\s*"     # "가." "1)" "(1)" "①" 같은 번호
_BUDGET_AT = re.compile(rf"^\s*{_BULLET}{_ENUM}\(?\s*(?:{_BUDGET_WORDS})(?![가-힣])\s*\)?|\(\s*(?:{_BUDGET_WORDS})\s*\)"
                        rf"|(?:{_BUDGET_WORDS}|예\s*산)\s*(?:\([^)\n]{{0,20}}\))?\s*[:：]")     # "예 산 : 금100,000,000원"은 쌍점이 있을 때만
_BUDGET_SKIP = re.compile(r"보증금|실적|자격|이상|미만|한도|초과")
_PERIOD_LABEL = re.compile(r"(계약\s*기간|과업\s*기간|용역\s*기간|수행\s*기간|사업\s*기간|연구\s*기간|과제\s*기간)")
_NOTICE = re.compile(r"(?:입찰\s*)?공고\s*번호\s*[:：]\s*(?P<v>[^\n]{3,40})|사전\s*규격\s*(?:등록\s*)?번호\s*[:：]\s*(?P<v2>[^\n]{3,40})")
# 마감 값은 날짜로 시작해야 받는다("… 18:00 까지)" 같은 꼬리를 잡지 않게)
_DEADLINE = re.compile(r"(?:제안서|입찰서)[^\n]{0,20}마감[^\n]{0,12}[:：]\s*(?P<v>\d{4}\s*[./년-]\s*\d{1,2}[^\n]{2,34})")
# 발주기관은 "이름 : 값" 꼴만(쌍점 없이 잡으면 "발주처의 조치에 대하여 …" 같은 문장을 잡았다)
_CLIENT = re.compile(r"(?m)^\s*" + _BULLET + r"(?:발\s*주\s*기\s*관|수\s*요\s*기\s*관|발\s*주\s*처|주\s*관\s*기\s*관)\s*[:：]\s*(?P<v>[^\n·:：]{2,40})")
# 고객 구분 낱말(폴백 리뷰 NIT 10): "통신 공사"·"전자정부"·"내부통제"를 잡지 않게 기관 이름 꼴만
_SECTOR_WORDS = (("대외공공", re.compile(r"[가-힣]{2,}(?:공단|공사|진흥원|연구원|재단|위원회)(?=[^가-힣]|[의은는이가에을를와과]|$)|(?<!전자)정부(?!\s*표준)|공공기관|지방자치단체|[가-힣]+(?:시청|도청|구청|군청)")),
                 ("대외금융", re.compile(r"은행|보험사|생명보험|손해보험|화재해상|증권|[가-힣A-Z]*카드(?:사)?(?=[^가-힣]|$)|캐피탈|금융사|금융지주")),
                 ("대내", re.compile(r"계열사|그룹사")))


def _field(text: str, value, start: int, end: int, source: str, **extra) -> dict:
    return {"value": value, "quote": text[start:end], "start": start, "end": end, "source": source, **extra}


def _name_rule(text: str) -> dict | None:
    for lab in _NAME_LABELS:
        # 표지 바로 뒤가 한글이면 문장이다("사업명을 정확히 기재한다" -- 폴백 리뷰 S6)
        rx = re.compile(rf"(?m)^\s*{_BULLET}{_ENUM}\(?\s*{lab}(?![가-힣])\s*\)?\s*[:：]?\s*(?P<v>[^\n]{{2,150}})")
        m = rx.search(text)
        if m:
            v = m.group("v").strip()
            if len(v) >= 2:
                return _field(text, v, m.start("v"), m.start("v") + len(v), "rule")
    return None


def _budget_rule(text: str) -> dict | None:
    lines = text.split("\n")
    pos = 0
    for i, line in enumerate(lines):
        m = _BUDGET_AT.search(line)
        if m and not _BUDGET_SKIP.search(line):
            seg, seg_start = line[m.end():], pos + m.end()
            hit = _AMT_SPAN.search(seg)
            if hit is None and not seg.strip() and i + 1 < len(lines) and not _BUDGET_SKIP.search(lines[i + 1]):
                seg, seg_start = lines[i + 1], pos + len(line) + 1        # 제목 줄("가. 사업 규모") 다음 줄의 금액
                hit = _AMT_SPAN.search(seg)
            v = parse_amount(hit.group(0)) if hit else None
            if v is not None:
                return _field(text, v, seg_start + hit.start(), seg_start + hit.end(), "rule", vat_included=vat_of(line + " " + seg))
        pos += len(line) + 1
    return None


def _period_rule(text: str) -> tuple[dict | None, dict | None]:
    lines = text.split("\n")
    pos = 0
    for i, line in enumerate(lines):
        m = _PERIOD_LABEL.search(line)
        if m:
            seg = line[m.end():].lstrip(" )）:：")
            span_start = pos + len(line) - len(seg)
            if not re.search(r"\d", seg) and i + 1 < len(lines):          # 제목 줄 다음 줄에 기간("1. 수행 기간" ↵ "o 2026년 5월 …")
                seg, span_start = lines[i + 1], pos + len(line) + 1
            if re.search(r"\d", seg):
                seg = seg.strip()
                period = _field(text, seg, span_start, span_start + len(seg), "rule") if seg else None
                # 서로 다른 "N개월"이 하나일 때만("120개월"을 20으로 읽지 않는다 · "6개월(하자보수 12개월)"처럼 여럿이면 모른다 -- 폴백 리뷰 S1·2차 S-3)
                ns = sorted({int(x) for x in re.findall(r"(?<!\d)(\d{1,2})\s*개월", seg) if 1 <= int(x) <= 60})
                months = _field(text, ns[0], span_start, span_start + len(seg), "rule") if len(ns) == 1 else None
                return period, months
        pos += len(line) + 1
    return None, None


def _simple(rx: re.Pattern, text: str, *groups: str) -> dict | None:
    m = rx.search(text)
    if not m:
        return None
    g = next((g for g in groups if m.group(g)), None)
    if g is None:
        return None
    v = m.group(g).strip()
    return _field(text, v, m.start(g), m.start(g) + len(v), "rule")


def _sector_rule(text: str, name: dict | None, client: dict | None) -> dict | None:
    for f in (client, name):
        if not f:
            continue
        for sector, rx in _SECTOR_WORDS:
            m = rx.search(f["value"])
            if m:
                s = f["start"] + m.start()
                return _field(text, sector, s, f["start"] + m.end(), "rule")
    return None


def rule_meta(text: str) -> dict:
    """규칙만: 칸 → {value, quote, start, end, source} 또는 None."""
    name = _name_rule(text)
    client = _simple(_CLIENT, text, "v")
    period, months = _period_rule(text)
    return {"name": name, "client": client, "sector": _sector_rule(text, name, client), "duration_months": months,
            "period": period, "budget": _budget_rule(text), "notice_no": _simple(_NOTICE, text, "v", "v2"),
            "deadline": _simple(_DEADLINE, text, "v")}


META_FIELDS = ("name", "client", "sector", "duration_months", "period", "budget", "notice_no", "deadline")

SYSTEM_META = ("너는 SI 회사가 받은 발주 자료(입찰공고문·제안요청서·사전규격·과업지시서·사업 개요)에서 신규 사업 등록 양식에 쓸 사실을 옮기는 도우미다. "
               "자료에 있는 것만 옮기고 짐작하지 않는다. 사람을 평가하지 않는다. JSON 하나만 답한다.")

TASK_META = """발주 자료:
<<<{text}>>>

자료에서 아래 칸을 찾아라. 각 칸은 {{"value": 값, "quote": 근거가 되는 자료의 말 그대로(글자 그대로 복사, 80자 안쪽)}} 또는 null(자료에 없으면).
- name: 사업명(입찰건명·공고명·용역명·사업명·과업명). 자료에 적힌 그대로.
- client: 발주기관 이름(자료에 적힌 그대로).
- sector: 고객 구분 — "대외공공"(정부·지자체·공공기관·공단·공사·연구원), "대외금융"(은행·보험·증권·카드 등 금융사), "대내"(같은 그룹 계열사·사내) 중 하나. quote는 그렇게 본 근거(기관 이름 등).
- duration_months: 사업(계약) 기간의 개월 수(정수). 자료에 "N개월"이 있으면 그 수. 시작·끝 날짜만 있으면 시작 달과 끝 달을 모두 넣어 센다(2026년 5월 ~ 11월 = 7).
  시작이 "계약체결일"이면 제출 마감·심사 일정 다음 달을 시작으로 본다. quote는 기간이 적힌 말.
- period: 계약·사업 기간 원문(예: "계약체결일로부터 ~ 2026년 11월 13일까지").
- budget: {{"value": 총 사업비(원 단위 정수, 월 단가가 아니라 총액), "vat_included": true|false|null, "quote": 금액이 적힌 말}}.
- notice_no: 입찰공고 번호 또는 사전규격 번호(자료에 적힌 그대로).
- deadline: 제안서(입찰서) 제출 마감 일시(자료에 적힌 그대로).
담당자 이름·전화번호·이메일은 옮기지 않는다.
답 형식: {{"name": …, "client": …, "sector": …, "duration_months": …, "period": …, "budget": …, "notice_no": …, "deadline": …}}"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", s or ""))


_TEXT_MIN = {"name": 4, "client": 2, "period": 4, "notice_no": 4, "deadline": 6}
_PERIOD_CUE = re.compile(r"기간|계약(?:체결)?일|착수일|부터|까지|~|∼")
_NAME_CUE = re.compile(r"입\s*찰\s*건\s*명|공\s*고\s*명|용\s*역\s*명|사\s*업\s*명|과\s*업\s*명|과\s*제\s*명|건\s*명")


def _check_meta(key: str, item, text: str, sectors: tuple[str, ...]) -> tuple[dict | None, str | None]:
    """AI 칸 하나를 검사 → (칸, None) 또는 (None, 버린 이유). 근거 말은 원문에 있고 150자 안, 글 칸 값은 근거 말 안의 원문 그대로(폴백 리뷰 S5)."""
    if item is None:
        return None, None
    if not isinstance(item, dict):
        return None, "모양이 다르다"
    value, quote = item.get("value"), str(item.get("quote") or "").strip()
    if key in _TEXT_MIN:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 150:
            return None, "값이 글이 아니다"
        value = value.strip()
        if len(value) < _TEXT_MIN[key] or (key in ("notice_no", "deadline") and not re.search(r"\d", value)):
            return None, "값이 너무 짧다"
        quote = quote or value
    if not quote:
        return None, "근거 말이 없다"
    if len(quote) > QUOTE_MAX:
        return None, "근거 말이 너무 길다"
    span = _locate(text, quote)
    if span is None:
        return None, "근거 말이 자료에 없다"
    q = text[span[0]:span[1]]
    if key in _TEXT_MIN:
        inner = _locate(q, value)
        if inner is None:
            return None, "값이 근거 말에 없다"
        # 값은 원문 표기 그대로, 줄바꿈·연속 빈칸만 한 칸으로(제목이 두 줄인 실제 제안요청서: "…기반\nAI 대전환…" -- 양식 칸에 줄바꿈이 들어가지 않게)
        extra = {}
        if key == "name" and not _NAME_CUE.search(q):
            extra["unlabeled"] = True          # 근거 말에 사업명 표지가 없다 -- 화면이 미리 체크하지 않는다(폴백 리뷰 2차 NIT 1: 잘린 이름·아무 구절)
        return _field(text, re.sub(r"\s+", " ", q[inner[0]:inner[1]]).strip(), *span, "ai", **extra), None
    if key == "sector":
        if value not in sectors:
            return None, f"고객 구분이 {'·'.join(sectors)}가 아니다"
        return _field(text, value, *span, "ai"), None
    if key == "duration_months":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 60:
            return None, "기간 개월이 1~60이 아니다"
        ns = sorted({int(x) for x in re.findall(r"(?<!\d)(\d{1,2})\s*개월", q)})
        if ns and value not in ns:
            return None, f"근거 말의 개월({', '.join(map(str, ns))})과 다르다"
        # 서버가 "확인했다"고 하는 것은 근거 말에 서로 다른 N개월이 하나뿐이고 기간을 뜻하는 말이 함께 있을 때만이다(폴백 리뷰 2차 S-3:
        # "PM은 경력 8개월 이상"의 8, "착수보고 1개월 이내, 총 8개월"의 1이 확인됨으로 미리 체크됐다). 나머지는 AI가 센 값 -- 화면이 미리 체크하지 않는다.
        confirmed = len(ns) == 1 and bool(_PERIOD_CUE.search(q))
        return _field(text, value, *span, "ai", computed=not confirmed), None
    if key == "budget":
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= BUDGET_MAX:
            return None, "금액이 정수가 아니다"
        if value not in amounts_won(q):
            return None, "근거 말의 금액과 다르다"
        return _field(text, value, *span, "ai", vat_included=vat_of(q)), None     # 부가세는 근거 말로만(AI 주장은 쓰지 않음)
    return None, "모르는 칸"


def read_project_meta(text: str, *, client=None, model: str | None = None, reasoning_effort: str | None = None,
                      price: dict | None = None, sectors: tuple[str, ...] = SECTORS) -> dict:
    """발주 자료 글자 → 등록 양식 칸 제안 {fields{칸: {value, quote, start, end, source: ai|rule, (vat_included)} | None},
    method: ai|rule, model, usage, warnings, fallback_reason, dropped[{field, why}]}. 위치는 넘겨받은 text 기준."""
    text = unicodedata.normalize("NFC", str(text or ""))[:MAX_TEXT]
    rule = rule_meta(text)
    warnings: list[str] = []
    if len(text.strip()) < 30:
        warnings.append("자료에서 읽은 글자가 거의 없다 -- 스캔 이미지 PDF면 글자로 저장한 파일을 올린다.")
    out = {"fields": rule, "method": "rule", "model": None, "warnings": warnings, "usage": None, "fallback_reason": None, "dropped": []}
    if client is None or not model:
        out["fallback_reason"] = "no_client"
        return out
    ai_text = text[:MAX_AI_TEXT]
    if len(text) > MAX_AI_TEXT:
        warnings.append(f"자료가 길어 앞 {MAX_AI_TEXT:,}자만 AI가 읽었다.")
    data, usage, err = _call(client, model, reasoning_effort, price, SYSTEM_META, TASK_META.format(text=ai_text))
    if err:
        out["fallback_reason"] = err
        return out
    fields: dict = {}
    dropped = []
    for key in META_FIELDS:
        got, why = _check_meta(key, data.get(key), ai_text, sectors)
        if why:
            dropped.append({"field": key, "why": why})
        fields[key] = got or rule.get(key)            # AI 칸이 검사를 못 넘으면 규칙 값(있으면)
    out.update(fields=fields, method="ai", model=model, usage=usage, dropped=dropped)
    return out


# --- 부서장용 요약 -----------------------------------------------------------------------------------------------------
SYSTEM_SUM = ("너는 SI 회사 부서장이 발주 자료를 빨리 파악하도록 요약하는 도우미다. 부서장은 이 사업에 인력을 낼지, 어떤 사람을 얼마 동안 낼지 판단한다. "
              "자료에 있는 사실만 쓰고 짐작·평가·권고는 하지 않는다. JSON 하나만 답한다.")

TASK_SUM = """자료 종류(올린 사람이 고름): {kind}
자료:
<<<{text}>>>

규칙
- doc_type: 이 자료가 무엇인지 짧게(예: "입찰공고문", "제안요청서", "과업지시서", "표준 계약 조건", "청렴계약 서약").
- relevance: "high"(사업 내용·기간·예산·인력·평가처럼 부서장 판단에 쓰는 내용이 있다) 또는 "low"(표준 계약 조건·서약처럼 어느 사업에나 같은 내용).
- one_line: 이 자료를 부서장에게 한 문장(60자 안쪽)으로. 자료에 없는 숫자를 쓰지 않는다.
- sections: 칸 id별 항목 목록(해당 없는 칸은 빈 목록). 항목 = {{"text": 부서장이 읽을 짧은 문장(80자 안쪽), "quote": 근거가 되는 자료의 말 그대로(글자 그대로 복사, 100자 안쪽)}}.
  overview(사업 목적·발주기관), scope(주요 과업 — 실제로 만들거나 분석할 것), schedule(기간·제출 마감·설명회·보고 일정), budget(사업비·계약 방식·공동수급·하도급),
  staffing(투입 인력·전공·경력·자격·PM 요건·인력 교체 제한), evaluation(평가 방식·배점, 특히 인력 관련 배점), constraints(보안·상주·개인정보·지식재산·지체 등 부서장이 알아야 할 제약),
  deliverables(산출물·보고서). 칸마다 중요한 순서로 최대 5개.
- 항목 text의 숫자는 그 항목 quote에 있는 숫자만 쓴다(계산한 숫자를 쓰지 않는다).
- 담당자 이름·전화번호·이메일·주소는 쓰지 않는다.
답 형식: {{"doc_type": "", "relevance": "high", "one_line": "", "sections": {{"overview": [], "scope": [], "schedule": [], "budget": [], "staffing": [], "evaluation": [], "constraints": [], "deliverables": []}}}}"""

_SECTION_WORDS = {
    "overview": r"목적|배경|필요성|개\s*요",
    "scope": r"과업\s*내용|주요\s*과업|과업\s*\d|구축|개발",
    "schedule": r"기간|마감|일시|설명회|착수|중간\s*보고|최종\s*보고",
    "budget": r"예산|사업비|공동\s*수급|하도급|계약\s*방법|낙찰자\s*결정",
    "staffing": r"투입\s*인력|참여\s*인력|수행\s*인력|경력|자격|PM|기술자|수행원",
    "evaluation": r"평가|배점",
    "constraints": r"보안|비밀|개인정보|상주|지체|저작권|지식재산|교체",
    "deliverables": r"산출물|결과\s*보고서|최종\s*보고서|제출물",
}


def rule_summary(text: str, kind: str = "") -> dict:
    """규칙만: 칸별 열쇠말이 든 줄을 그대로(앞에서부터 3줄). 근거 = 그 줄."""
    lines, pos, spans = text.split("\n"), 0, []
    for line in lines:
        spans.append((pos, line))
        pos += len(line) + 1
    sections = []
    used: set[int] = set()
    for key, title in SECTIONS:
        rx = re.compile(_SECTION_WORDS[key])
        items = []
        for s, line in spans:
            t = line.strip()
            if s in used or not (8 <= len(t) <= 160) or not rx.search(t) or is_personal(t):
                continue
            used.add(s)
            start = s + line.index(t)
            items.append({"text": t, "quote": t, "start": start, "end": start + len(t)})
            if len(items) >= 3:
                break
        sections.append({"key": key, "title": title, "items": items})
    name = _name_rule(text)
    return {"doc_type": kind or "자료", "relevance": "high", "one_line": name["value"] if name else "",
            "sections": sections}


def _clean_item(item, ai_text: str) -> tuple[dict | None, str | None]:
    if not isinstance(item, dict):
        return None, "모양이 다르다"
    t, q = str(item.get("text") or "").strip(), str(item.get("quote") or "").strip()
    if not t or len(t) > 160:
        return None, "글이 없거나 길다"
    if not q or len(q) > QUOTE_MAX:
        return None, "근거 말이 없거나 길다"
    span = _locate(ai_text, q)
    if span is None:
        return None, "근거 말이 자료에 없다"
    quote = ai_text[span[0]:span[1]]
    if is_personal(t) or is_personal(quote):
        return None, "이름·연락처"
    if not numbers_supported(t, quote):
        return None, "근거 말에 없는 숫자"
    return {"text": t, "quote": quote, "start": span[0], "end": span[1]}, None


def summarize_document(text: str, kind: str = "", *, client=None, model: str | None = None, reasoning_effort: str | None = None,
                       price: dict | None = None) -> dict:
    """자료 글자 → 부서장용 요약 {doc_type, relevance: high|low, one_line, sections[{key, title, items[{text, quote, start, end}]}],
    method: ai|rule, model, usage, warnings, fallback_reason, dropped[{section, why}]}. 위치는 넘겨받은 text 기준."""
    text = unicodedata.normalize("NFC", str(text or ""))[:MAX_TEXT]
    warnings: list[str] = []
    if len(text.strip()) < 30:
        warnings.append("자료에서 읽은 글자가 거의 없다 -- 스캔 이미지 PDF면 글자로 저장한 파일을 올린다.")
    out = {**rule_summary(text, kind), "method": "rule", "model": None, "warnings": warnings, "usage": None,
           "fallback_reason": None, "dropped": []}
    if client is None or not model:
        out["fallback_reason"] = "no_client"
        return out
    ai_text = text[:MAX_AI_TEXT]
    if len(text) > MAX_AI_TEXT:
        warnings.append(f"자료가 길어 앞 {MAX_AI_TEXT:,}자만 AI가 읽었다 -- 뒷부분은 요약에 없다.")
    data, usage, err = _call(client, model, reasoning_effort, price, SYSTEM_SUM, TASK_SUM.format(kind=kind or "모름", text=ai_text))
    if err:
        out["fallback_reason"] = err
        return out
    dropped: list[dict] = []
    raw = data.get("sections") if isinstance(data.get("sections"), dict) else {}
    sections = []
    seen: set[str] = set()                  # 같은 근거 말은 한 칸에만(첫 칸) -- 실측: "산출물은 공단에 귀속"이 유의 사항·산출물에 두 번
    for key, title in SECTIONS:
        items = []
        for item in (raw.get(key) or [])[: ITEMS_PER_SECTION * 2] if isinstance(raw.get(key), list) else []:
            got, why = _clean_item(item, ai_text)
            if why:
                dropped.append({"section": key, "why": why})
                continue
            if got["quote"] in seen:
                continue
            seen.add(got["quote"])
            items.append(got)
            if len(items) >= ITEMS_PER_SECTION:
                break
        sections.append({"key": key, "title": title, "items": items})
    one = str(data.get("one_line") or "").strip()
    # 한 줄 요약의 숫자는 살아남은 항목들의 근거 말에 있는 것만(자료 전체와 대조하면 "10억 원 규모"(자격 기준)가 굵은 머리말로 통과했다 -- 폴백 리뷰 S4)
    quotes = "\n".join(i["quote"] for sec in sections for i in sec["items"])
    if len(one) > 120 or is_personal(one) or not numbers_supported(one, quotes):
        dropped.append({"section": "one_line", "why": "길거나 항목 근거에 없는 숫자·이름·연락처"})
        one = next((s["items"][0]["text"] for s in sections if s["items"]), "")
    doc_type = str(data.get("doc_type") or "").strip()[:30] or (kind or "자료")
    relevance = data.get("relevance") if data.get("relevance") in ("high", "low") else "high"
    if relevance == "high" and not any(s["items"] for s in sections):
        warnings.append("근거를 확인한 요약 항목이 없다.")
    out.update(doc_type=doc_type, relevance=relevance, one_line=one, sections=sections, method="ai", model=model,
               usage=usage, dropped=dropped)
    return out


def _call(client, model, reasoning_effort, price, system: str, user: str) -> tuple[dict, dict | None, str | None]:
    """AI 한 번(json_object) → (답, 사용량, 실패 이유)."""
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        data = json.loads(resp.choices[0].message.content or "{}")
        if not isinstance(data, dict):
            raise TypeError("JSON 객체가 아니다")
    except Exception as exc:                                    # noqa: BLE001 -- 네트워크·API·JSON 오류는 규칙 값으로
        log.warning("발주 자료 AI 읽기 실패: %s", exc)
        return {}, None, f"llm_error:{type(exc).__name__}"
    u = getattr(resp, "usage", None)
    pin, pout = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
    pr = price or {}
    usage = {"latency_s": round(time.monotonic() - t0, 2), "in": pin, "out": pout, "model": model,
             "cost_usd": round((pin * pr.get("input_per_1m", 0) + pout * pr.get("output_per_1m", 0)) / 1e6, 6)}
    return data, usage, None
