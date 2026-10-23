"""발주 자료에서 필요 기술 읽기(2026-10-23, claude-a -- claude-b 요청 `docs/requests/2026-10-23-rfp-skill-reading.md`, 사용자 "자료를 넣으면 llm(openai api,
glm5.3-flash 각각 성능 확인 필요)을 통해서 분석해서 알아서 필요 기술에 대해 정의해서 등록해주고 사용자가 보고 … 수정 및 삭제", 결정 "a·b 나눠서",
설계 루트 `.omc/plan/2026-10-23-rfp-read.md`).

제안요청서(RFP)·과업지시서·사전규격·사업 개요의 글자(b의 `api.project_docs.extract_text`)를 **필요 기술 제안**으로 바꾼다. "상황을 말로"(`api/rag/request_parse`)와 같은 원칙:
AI는 사전 개념 id 안에서만 고르고, 근거 말(quote)이 원문에 있어야 받는다(위치는 서버가 다시 잰다). 사람 정보는 보내지 않는다(자료 글자만).
등록은 사람이 한다 -- 이 결과는 등록 양식의 **제안**이다(더하기·고치기·지우기).

핵심 난점: 발주 자료에는 요구가 아닌 기술 언급(현행 시스템 "Oracle로 운영 중", 연계 대상, 배경)이 섞여 있다. 그래서 요구(required)·우대(preferred)와
"자료에 나오지만 요구가 아님"(mentioned_not_required)을 나눠 돌려준다.

순서
1. 규칙(AI 없음): 사전 별칭 찾기 + 그 문장의 말로 판정(우대 말 → 우대, 경력·이상·투입 같은 요구 말 → 필수, 현행·기존·운영 중·연계만 있으면 요구 아님,
   아무 말 없으면 바로 위 목차·□ 머리 줄의 말(그 머리가 우대면 요구 말이 있어도 우대), 그것도 없으면 필수 -- 개조식 줄 머리·"우대:" 같은 머리말 줄은 문장을 끊는다),
   같은 문장에서 다음 기술 이름 전까지의 "N년·N개월"·"N명"을 경력·인원으로. 한 기술이 여러 번 나오면 필수 > 우대 > 요구 아님.
2. AI(json_object): 자료를 `MAX_AI_TEXT`자 이하 조각으로 나눠 **동시에** 묻는다(2026-10-23 claude-b 요청 `docs/requests/2026-10-23-rfp-chunked-read.md`,
   사용자 결정 "나눠 동시에 읽기" -- 앞 조각 요약을 다음 조각에 넣는 방식은 요약에 근거 말 원문 확인을 못 쓰고 실수가 번져 고르지 않았다).
   조각 경계는 목차 줄(Ⅰ.·1.·가.) → 빈 줄 → 줄바꿈, 둘 이상이면 조각마다 속한 제목 경로를 앞에 붙인다(한 조각 자료는 지시문이 예전과 같다).
3. 서버 검사: 사전 id, quote 위치(조각 원문에서 찾고 전체 위치로), 경력 0~240개월, 인원 1~10, 같은 id 한 번. 어긋난 칸만 버리고 `dropped`에 남긴다.
   조각을 합칠 때 같은 기술은 필수 > 우대 > 요구 아님, 경력·인원은 그 종류 근거 중 큰 값, 근거 말은 숫자가 있는 쪽.
4. AI가 없거나(키 없음·실데이터 외부 차단) 모두 실패하면 1번(method rule). 일부 조각만 실패하면 그 범위는 규칙 결과 + 경고.
"""
from __future__ import annotations

import bisect
import json
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from api.rag.request_parse import _locate, alias_hits, catalog
from core.ingest.skills import SkillDictionary

log = logging.getLogger(__name__)

MAX_TEXT = 200_000           # b의 extract_text(_limited)가 주는 최대 글자 수와 같다(2026-10-23 6만 → 20만, b 요청서 1)
MAX_AI_TEXT = 40_000         # AI에 보내는 조각 하나(사전 목록과 합쳐 모델 문맥 안)
MAX_CHUNKS = 8               # 20만 자를 경계에서 자르면 5~7조각 -- 모두 동시에 보낸다(화면 대기 상한은 한 번 호출 기준이라 그대로 맞는다)
_CUT_FROM = 0.75             # 조각 끝 경계는 조각 길이의 75~100% 사이에서 찾는다(너무 잘게 쪼개지 않게)
_OVERLAP = 300               # 경계를 못 찾아 글자에서 자를 때만 겹친다(문장이 둘로 갈리지 않게 -- 같은 기술은 합치기에서 하나로)
MONTHS_MAX, HEADCOUNT_MAX = 240, 10
_PREF = re.compile(r"우대|가점|가산점|선호|있으면\s*좋|플러스")
_REQ = re.compile(r"경력|이상|필수|투입|요건|자격|능숙|가능자|가능한|보유|경험|수행\s*인력|기술\s*인력|숙련")
_NOT = re.compile(r"현행|현재\s*(?:운영|사용)|기존\s*(?:시스템|환경|운영)|운영\s*중|사용\s*중|구축되어|도입되어|연계\s*대상|연계한다|연동\s*대상|인터페이스\s*대상")
# 경력 표현일 때만 -- "사업 기간 6개월"·"2024년" 같은 숫자를 경력으로 읽지 않게
_YEARS = re.compile(r"(\d{1,2})\s*년\s*(?:이상|차)|경력\s*(\d{1,2})\s*년")
_MONTHS = re.compile(r"(\d{1,3})\s*개월\s*이상|경력\s*(\d{1,3})\s*개월")
_HEAD = re.compile(r"(\d{1,2})\s*(?:명|인)(?![가-힣])")
# 줄바꿈 하나는 문장 끝이 아니다(PDF 줄 넘김) -- 다만 다음 줄이 개조식 머리(□ ◦ - · 1. 가. ① …)나 "우대:" 같은 머리말로 시작하면 끝이다
# (2026-10-23 b 관찰 3: 한글·워드 문단은 줄바꿈 하나로 이어져, 다음 문단의 "우대"가 앞 문단 기술을 우대로 바꿨다)
_SENT_END = re.compile(r"(?:다|요|음|함|됨|것)\.\s|[.!?。]\s*\n|\n\s*\n|\.\s+(?=[가-힣A-Z0-9])"
                       r"|\n(?=[ \t]*(?:[□■○◦❍●•▪◆◇※ㆍ·\-–]|\d{1,2}[.)][ \t]|[가-하][.)][ \t]|\(\d{1,2}\)|[①-⑳]"
                       r"|[가-힣A-Za-z][가-힣A-Za-z ]{0,11}[:：]))")


# 목차 줄(조각 경계·제목 경로) -- 큰 목차부터. 80자보다 긴 줄은 본문으로 본다
_HEADINGS = (re.compile(r"(?:제\s*\d{1,2}\s*[장편부절]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4}(?:\s*[.)]|\s)|[IVX]{1,4}\s*[.)])\s*\S"),
             re.compile(r"\d{1,2}\s*[.)]\s*[^\d\s]"),
             re.compile(r"[가-하]\s*[.)]\s*\S"))
_LEAD = re.compile(r"[□■]\s*\S")                # 공공 문서의 큰 항목 머리(개조식 줄은 이 머리의 뜻을 이어받는다)


def _sentences(text: str) -> tuple[list[int], list[int]]:
    """문장 끝 표지의 (시작들, 끝들) -- 기술 이름마다 처음부터 다시 훑지 않게 한 번만(20만 자 자료)."""
    ms = list(_SENT_END.finditer(text))
    return [m.start() for m in ms], [m.end() for m in ms]


def _sentence(bounds: tuple[list[int], list[int]], n: int, start: int, end: int) -> tuple[int, int]:
    """[start, end)를 품은 문장의 범위(앞 표지 끝 ~ 뒤 표지 시작 + 1)."""
    starts, ends = bounds
    i = bisect.bisect_right(ends, start) - 1
    j = bisect.bisect_left(starts, end)
    return (ends[i] if i >= 0 else 0), (starts[j] + 1 if j < len(starts) else n)


def _numbers(seg: str) -> tuple[int | None, int | None]:
    """한 기술 뒤 구간의 경력(개월)·인원."""
    months = None
    m = _YEARS.search(seg)
    if m:
        months = int(m.group(1) or m.group(2)) * 12
    else:
        m = _MONTHS.search(seg)
        if m:
            months = int(m.group(1) or m.group(2))
    h = _HEAD.search(seg)
    head = int(h.group(1)) if h else None
    if months is not None and not 0 <= months <= MONTHS_MAX:
        months = None
    if head is not None and not 1 <= head <= HEADCOUNT_MAX:
        head = None
    return months, head


def _kind(sentence: str) -> str | None:
    """문장 말로 본 종류(우대 > 요구 말 > 현행·연계 말) -- 판정할 말이 없으면 None."""
    if _PREF.search(sentence):
        return "preferred"
    if _REQ.search(sentence):
        return "required"
    if _NOT.search(sentence):
        return "not_required"
    return None


def _leads(text: str) -> tuple[list[int], list[str]]:
    """문맥 줄(목차 줄·□ 머리 줄)의 시작 위치와 글. 개조식 줄이 제 말로 판정되지 않으면 바로 위 문맥 줄의 뜻을 따른다
    ("1. 현행 시스템 구성" 아래 "- DBMS: Tibero"는 요구가 아니다 -- 줄마다 문장을 끊으면서 잃은 문맥, 2026-10-23)."""
    pos_, txt, pos = [], [], 0
    for line in text.split("\n"):
        s = line.strip()
        if 0 < len(s) <= 80 and (_LEAD.match(s) or any(rx.match(s) for rx in _HEADINGS)):
            pos_.append(pos)
            txt.append(s)
        pos += len(line) + 1
    return pos_, txt


_RANK = {"required": 0, "preferred": 1, "not_required": 2}


def rule_read(text: str, sd: SkillDictionary) -> dict:
    """규칙만: {skills, mentioned_not_required} -- AI가 없을 때와 측정의 기준선."""
    hits = alias_hits(sd, text)
    bounds = _sentences(text)
    lead_pos, lead_txt = _leads(text)
    best: dict[str, dict] = {}
    for i, h in enumerate(hits):
        s, e = _sentence(bounds, len(text), h["start"], h["end"])
        kind = _kind(text[s:e])
        if kind in (None, "required"):                   # "□ 우대 사항" 아래 "◦ Kafka 구축 경험"의 "경험"은 요구 말이지만 우대다
            k = bisect.bisect_right(lead_pos, s) - 1
            lead = _kind(lead_txt[k]) if k >= 0 else None
            kind = "preferred" if lead == "preferred" else (kind or lead or "required")
        nxt = hits[i + 1]["start"] if i + 1 < len(hits) and hits[i + 1]["start"] < e else e
        months, head = _numbers(text[h["end"]:min(nxt, h["end"] + 60)])
        row = {"concept_id": h["id"], "name": h["name"], "months": months, "headcount": head, "quote": h["quote"],
               "start": h["start"], "end": h["end"], "kind": kind}
        old = best.get(h["id"])
        if old is None or _RANK[kind] < _RANK[old["kind"]] or (
                _RANK[kind] == _RANK[old["kind"]] and old["months"] is None and months is not None):
            best[h["id"]] = row
    rows = sorted(best.values(), key=lambda r: r["start"])
    return {"skills": [r for r in rows if r["kind"] != "not_required"],
            "mentioned_not_required": [{k: r[k] for k in ("concept_id", "name", "quote", "start", "end")}
                                       for r in rows if r["kind"] == "not_required"]}


SYSTEM = ("너는 SI 회사가 받은 발주 자료(제안요청서·과업지시서·사전규격·사업 개요)에서 수행 인력에게 필요한 기술을 뽑는 도우미다. "
          "사람을 고르거나 평가하지 않는다. 기술은 주어진 사전 목록의 id만 쓴다. JSON 하나만 답한다.")

TASK = """발주 자료:
<<<{text}>>>

사전(기술 id | 대표 이름 | 다른 이름):
{catalog}

규칙
- skills: 이 사업을 수행할 인력에게 필요한 기술. 각 항목
  {{"id": 사전 id, "quote": 근거가 되는 자료의 말 그대로(글자 그대로 복사, 40자 안쪽), "kind": "required" 또는 "preferred",
    "months": 요구 경력 개월 수(“3년 이상” = 36, 없으면 null), "headcount": 그 기술이 필요한 인원(없으면 null)}}.
  required = 필수 요건·투입 인력 요건·과업 수행에 쓰는 기술. preferred = 우대·가점·있으면 좋은 기술.
  과업 범위에 적힌 일(예: "Kubernetes 운영 환경 전환")에 쓰는 기술은 required다.
- not_required: 자료에 나오지만 요구가 아닌 기술 — 현행·기존 시스템 설명("현재 Oracle로 운영 중"), 연계·연동 대상, 배경·현황 설명, 발주처가 이미 가진 것.
  각 항목 {{"id": 사전 id, "quote": 자료의 말 그대로}}. 같은 기술이 다른 곳에서 요구로 쓰였으면 skills에만 넣는다.
- 사전에 맞는 id가 없는 기술 요구는 unmapped_terms에 그 말을 적는다. 같은 id는 한 번만.
- 나이·성별 같은 사람 특성은 요구로 쓰지 않는다.
답 형식: {{"skills": [...], "not_required": [...], "unmapped_terms": []}}"""


_PART = "이 글은 긴 발주 자료를 나눈 {n}조각 중 {i}번째다(글자 {s:,}~{e:,}). {path}근거 말(quote)은 아래 조각 글에서 그대로 복사한다.\n\n"


def _int_in(v, lo: int, hi: int) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _headings(text: str) -> list[tuple[int, int, str]]:
    """(줄 시작 위치, 목차 깊이 0~2, 제목 앞 50자)."""
    out, pos = [], 0
    for line in text.split("\n"):
        s = line.strip()
        if 0 < len(s) <= 80:
            for lv, rx in enumerate(_HEADINGS):
                if rx.match(s):
                    out.append((pos, lv, s[:50]))
                    break
        pos += len(line) + 1
    return out


def _chunks(text: str, heads: list[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """MAX_AI_TEXT 이하 조각(시작, 끝) -- 끝은 조각 뒤 25% 안의 마지막 목차 줄 → 빈 줄 → 줄바꿈, 없으면 글자에서 자르고 조금 겹친다."""
    n, out, s = len(text), [], 0
    starts = [h[0] for h in heads]
    while s < n and len(out) < MAX_CHUNKS:
        hard = s + MAX_AI_TEXT
        if hard >= n:
            out.append((s, n))
            break
        lo = s + int(MAX_AI_TEXT * _CUT_FROM)
        k = bisect.bisect_right(starts, hard) - 1
        if k >= 0 and starts[k] >= lo:
            cut = starts[k]
        elif (b := text.rfind("\n\n", lo, hard)) >= 0:
            cut = b + 1
        elif (b := text.rfind("\n", lo, hard)) >= 0:
            cut = b + 1
        else:
            out.append((s, hard))
            s = hard - _OVERLAP
            continue
        out.append((s, cut))
        s = cut
    return out


def _path(heads: list[tuple[int, int, str]], at: int) -> str:
    """at 앞의 목차 줄로 만든 제목 경로(큰 목차가 나오면 그 아래 작은 목차는 지운다)."""
    cur: dict[int, str] = {}
    for pos, lv, title in heads:
        if pos >= at:
            break
        cur = {k: v for k, v in cur.items() if k < lv}
        cur[lv] = title
    return " > ".join(cur[k] for k in sorted(cur))


def _ask(client, model: str, effort: str | None, prompt: str) -> tuple[dict, int, int, float]:
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model, response_format={"type": "json_object"}, **({"reasoning_effort": effort} if effort else {}),
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    data = json.loads(resp.choices[0].message.content or "{}")
    if not isinstance(data, dict):
        raise TypeError("JSON 객체가 아니다")
    u = getattr(resp, "usage", None)
    return data, getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0, round(time.monotonic() - t0, 2)


def _checked(data: dict, sd: SkillDictionary, piece: str, off: int, dropped: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """한 조각의 AI 답 → (skills, not_required, unmapped) -- 사전 id·근거 말 위치(조각 원문에서 찾아 전체 위치로)·범위 검사."""
    skills: list[dict] = []
    nots: list[dict] = []
    seen: set[str] = set()

    def place(item) -> tuple[str, tuple[int, int]] | None:
        if not isinstance(item, dict):
            return None
        cid, quote = str(item.get("id") or ""), str(item.get("quote") or "")
        if cid not in sd.concepts:
            dropped.append({"id": cid, "why": "사전에 없는 id"})
            return None
        span = _locate(piece, quote)
        if span is None:
            dropped.append({"id": cid, "why": "근거 말이 자료에 없다"})
            return None
        return cid, span
    for item in data.get("skills") or []:
        got = place(item)
        if got is None or got[0] in seen:
            continue
        cid, (s, e) = got
        seen.add(cid)
        months, head = item.get("months"), item.get("headcount")
        if months is not None and _int_in(months, 0, MONTHS_MAX) is None:
            dropped.append({"id": cid, "why": f"경력 개월 범위 밖({months!r})"})
        if head is not None and _int_in(head, 1, HEADCOUNT_MAX) is None:
            dropped.append({"id": cid, "why": f"인원 범위 밖({head!r})"})
        skills.append({"concept_id": cid, "name": sd.concepts[cid]["label"], "months": _int_in(months, 0, MONTHS_MAX),
                       "headcount": _int_in(head, 1, HEADCOUNT_MAX), "quote": piece[s:e], "start": off + s, "end": off + e,
                       "kind": "preferred" if item.get("kind") == "preferred" else "required"})
    for item in data.get("not_required") or []:
        got = place(item)
        if got is None or got[0] in seen:
            continue
        cid, (s, e) = got
        seen.add(cid)
        nots.append({"concept_id": cid, "name": sd.concepts[cid]["label"], "quote": piece[s:e], "start": off + s, "end": off + e})
    unmapped = [str(x)[:60] for x in (data.get("unmapped_terms") or []) if isinstance(x, str) and x.strip()]
    return skills, nots, unmapped


def _merge_skills(parts: list[list[dict]]) -> list[dict]:
    """조각들의 skills를 기술마다 하나로: 필수 > 우대, 경력·인원은 그 종류 근거 중 큰 값, 근거 말은 숫자가 있는 쪽(없으면 앞쪽)."""
    by: dict[str, list[dict]] = {}
    for rows in parts:
        for r in rows:
            by.setdefault(r["concept_id"], []).append(r)
    out = []
    for rows in by.values():
        kind = "required" if any(r["kind"] == "required" for r in rows) else "preferred"
        same = sorted((r for r in rows if r["kind"] == kind), key=lambda r: r["start"])
        months = max((r["months"] for r in same if r["months"] is not None), default=None)
        head = max((r["headcount"] for r in same if r["headcount"] is not None), default=None)
        rep = next((r for r in same if r["months"] is not None or r["headcount"] is not None), same[0])
        out.append({**rep, "kind": kind, "months": months, "headcount": head})
    return sorted(out, key=lambda r: r["start"])


def read_requirements(text: str, sd: SkillDictionary, *, client=None, model: str | None = None,
                      reasoning_effort: str | None = None, price: dict | None = None) -> dict:
    """발주 자료 글자 → {skills[{concept_id, name, months, headcount, quote, start, end, kind: required|preferred}],
    mentioned_not_required[{concept_id, name, quote, start, end}], unmapped_terms, method: ai|rule, model, warnings, usage, fallback_reason, dropped,
    chunks[{start, end, method: ai|rule, latency_s}]}.
    위치(start·end)는 넘겨받은 text 기준이다. AI 결과가 있으면 skills는 AI 것이고, 규칙으로만 찾은 언급은 mentioned_not_required에 남긴다
    (화면이 보여 줄 수 있게 -- 다만 업무 도메인 낱말("공공기관"→공공 업무, "현안 카드"→카드 업무)은 잡음이라 뺀다, 2026-10-23 b 관찰 2)."""
    raw = unicodedata.normalize("NFC", str(text or ""))
    text = raw[:MAX_TEXT]
    rule = rule_read(text, sd)
    warnings = []
    if len(text.strip()) < 30:
        warnings.append("자료에서 읽은 글자가 거의 없다 -- 스캔 이미지 PDF면 글자로 저장한 파일을 올린다.")
    if len(raw) > MAX_TEXT:
        warnings.append(f"자료가 길어 앞 {MAX_TEXT:,}자만 읽었다 -- 그 뒤의 요구는 찾지 않았다.")
    out = {"skills": rule["skills"], "mentioned_not_required": rule["mentioned_not_required"], "unmapped_terms": [],
           "method": "rule", "model": None, "warnings": warnings, "usage": None, "fallback_reason": None, "dropped": [],
           "chunks": [{"start": 0, "end": len(text), "method": "rule", "latency_s": None}]}
    if client is None or not model:
        out["fallback_reason"] = "no_client"
        return out
    heads = _headings(text)
    spans = _chunks(text, heads) or [(0, len(text))]      # 빈 글도 한 조각(예전처럼 AI에 한 번 묻는다)
    if spans and spans[-1][1] < len(text):
        warnings.append(f"자료가 길어 앞 {spans[-1][1]:,}자까지만 AI가 읽었다 -- 뒷부분의 요구는 규칙으로 찾은 언급만 남는다.")
    cat = catalog(sd)

    def prompt(i: int, s: int, e: int) -> str:
        task = TASK.format(text=text[s:e], catalog=cat)
        if len(spans) == 1:
            return task
        p = _path(heads, s + 1)                            # 조각이 목차 줄로 시작하면 그 줄까지(같은 깊이의 앞 목차는 지워진다)
        return _PART.format(n=len(spans), i=i + 1, s=s, e=e, path=f"이 조각이 속한 제목: {p}. " if p else "") + task

    def one(i: int):
        try:
            return _ask(client, model, reasoning_effort, prompt(i, *spans[i]))
        except Exception as exc:                                # noqa: BLE001 -- 네트워크·API·JSON 오류는 그 조각만 규칙 값으로
            log.warning("발주 자료 AI 읽기 실패(조각 %d/%d): %s", i + 1, len(spans), exc)
            return exc
    t0 = time.monotonic()
    if len(spans) == 1:
        got = [one(0)]
    else:
        with ThreadPoolExecutor(max_workers=len(spans)) as pool:
            got = list(pool.map(one, range(len(spans))))
    wall = round(time.monotonic() - t0, 2)
    if all(isinstance(g, Exception) for g in got):
        out["fallback_reason"] = f"llm_error:{type(got[0]).__name__}"
        return out
    dropped: list[dict] = []
    skill_parts: list[list[dict]] = []
    not_parts: list[dict] = []
    unmapped: list[str] = []
    chunks = []
    pin = pout = 0
    for i, ((s, e), g) in enumerate(zip(spans, got)):
        if isinstance(g, Exception):
            skill_parts.append([r for r in rule["skills"] if s <= r["start"] < e])
            not_parts += [r for r in rule["mentioned_not_required"] if s <= r["start"] < e]
            warnings.append(f"{i + 1}번째 조각(글자 {s:,}~{e:,})은 AI 읽기에 실패해({type(g).__name__}) 규칙으로 읽었다.")
            chunks.append({"start": s, "end": e, "method": "rule", "latency_s": None})
            continue
        data, a, b, lat = g
        pin, pout = pin + a, pout + b
        sk, nt, um = _checked(data, sd, text[s:e], s, dropped)
        skill_parts.append(sk)
        not_parts += nt
        unmapped += [u for u in um if u not in unmapped]
        chunks.append({"start": s, "end": e, "method": "ai", "latency_s": lat})
    skills = _merge_skills(skill_parts)
    chosen = {r["concept_id"] for r in skills}
    mentioned: dict[str, dict] = {}
    for r in sorted(not_parts, key=lambda r: r["start"]):
        if r["concept_id"] not in chosen:
            mentioned.setdefault(r["concept_id"], {k: r[k] for k in ("concept_id", "name", "quote", "start", "end")})
    # 규칙이 찾았지만 AI가 요구로 고르지 않은 언급도 "요구 아님"으로 보인다(사람이 다시 볼 수 있게) -- 업무 도메인 낱말은 빼고
    for r in rule["skills"] + rule["mentioned_not_required"]:
        cid = r["concept_id"]
        if cid not in chosen and sd.concepts.get(cid, {}).get("category") != "Domain":
            mentioned.setdefault(cid, {k: r[k] for k in ("concept_id", "name", "quote", "start", "end")})
    pr = price or {}
    out.update(skills=skills, mentioned_not_required=sorted(mentioned.values(), key=lambda r: r["start"]),
               unmapped_terms=unmapped[:20], method="ai", model=model, dropped=dropped, chunks=chunks,
               usage={"latency_s": wall, "in": pin, "out": pout, "model": model,
                      "cost_usd": round((pin * pr.get("input_per_1m", 0) + pout * pr.get("output_per_1m", 0)) / 1e6, 6)})
    return out
