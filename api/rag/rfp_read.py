"""발주 자료에서 필요 기술 읽기(2026-10-23, claude-a -- claude-b 요청 `docs/requests/2026-10-23-rfp-skill-reading.md`, 사용자 "자료를 넣으면 llm(openai api,
glm5.3-flash 각각 성능 확인 필요)을 통해서 분석해서 알아서 필요 기술에 대해 정의해서 등록해주고 사용자가 보고 … 수정 및 삭제", 결정 "a·b 나눠서",
설계 루트 `.omc/plan/2026-10-23-rfp-read.md`).

제안요청서(RFP)·과업지시서·사전규격·사업 개요의 글자(b의 `api.project_docs.extract_text`)를 **필요 기술 제안**으로 바꾼다. "상황을 말로"(`api/rag/request_parse`)와 같은 원칙:
AI는 사전 개념 id 안에서만 고르고, 근거 말(quote)이 원문에 있어야 받는다(위치는 서버가 다시 잰다). 사람 정보는 보내지 않는다(자료 글자만).
등록은 사람이 한다 -- 이 결과는 등록 양식의 **제안**이다(더하기·고치기·지우기).

핵심 난점: 발주 자료에는 요구가 아닌 기술 언급(현행 시스템 "Oracle로 운영 중", 연계 대상, 배경)이 섞여 있다. 그래서 요구(required)·우대(preferred)와
"자료에 나오지만 요구가 아님"(mentioned_not_required)을 나눠 돌려준다.

순서
1. 규칙(AI 없음): 사전 별칭 찾기 + 그 문장의 말로 판정(우대 말 → 우대, 경력·이상·투입 같은 요구 말 → 필수, 현행·기존·운영 중·연계만 있으면 요구 아님, 아무 말 없으면 필수),
   같은 문장에서 다음 기술 이름 전까지의 "N년·N개월"·"N명"을 경력·인원으로. 한 기술이 여러 번 나오면 필수 > 우대 > 요구 아님.
2. AI 한 번(json_object): 자료 앞 `MAX_AI_TEXT`자 + 사전 목록 → skills(required|preferred, 경력·인원)·not_required·unmapped_terms.
3. 서버 검사: 사전 id, quote 위치, 경력 0~240개월, 인원 1~10, 같은 id 한 번. 어긋난 칸만 버리고 `dropped`에 남긴다.
4. AI가 없거나(키 없음·실데이터 외부 차단) 실패하면 1번(method rule).
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata

from api.rag.request_parse import _locate, alias_hits, catalog
from core.ingest.skills import SkillDictionary

log = logging.getLogger(__name__)

MAX_TEXT = 60_000            # b의 extract_text가 주는 글자 수와 같다
MAX_AI_TEXT = 40_000         # AI에 보내는 앞부분(사전 목록과 합쳐 모델 문맥 안 -- 넘으면 경고)
MONTHS_MAX, HEADCOUNT_MAX = 240, 10
_PREF = re.compile(r"우대|가점|가산점|선호|있으면\s*좋|플러스")
_REQ = re.compile(r"경력|이상|필수|투입|요건|자격|능숙|가능자|가능한|보유|경험|수행\s*인력|기술\s*인력|숙련")
_NOT = re.compile(r"현행|현재\s*(?:운영|사용)|기존\s*(?:시스템|환경|운영)|운영\s*중|사용\s*중|구축되어|도입되어|연계\s*대상|연계한다|연동\s*대상|인터페이스\s*대상")
# 경력 표현일 때만 -- "사업 기간 6개월"·"2024년" 같은 숫자를 경력으로 읽지 않게
_YEARS = re.compile(r"(\d{1,2})\s*년\s*(?:이상|차)|경력\s*(\d{1,2})\s*년")
_MONTHS = re.compile(r"(\d{1,3})\s*개월\s*이상|경력\s*(\d{1,3})\s*개월")
_HEAD = re.compile(r"(\d{1,2})\s*(?:명|인)(?![가-힣])")
_SENT_END = re.compile(r"(?:다|요|음|함|됨|것)\.\s|[.!?。]\s*\n|\n\s*\n|\.\s+(?=[가-힣A-Z0-9])")


def _sentence(text: str, start: int, end: int) -> tuple[int, int]:
    """[start, end)를 품은 문장의 범위(문장 끝 표지 사이). PDF 줄바꿈은 문장 끝이 아니다."""
    s = 0
    for m in _SENT_END.finditer(text, 0, start):
        s = m.end()
    m = _SENT_END.search(text, end)
    return s, (m.start() + 1 if m else len(text))


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


def _classify(sentence: str) -> str:
    if _PREF.search(sentence):
        return "preferred"
    if _REQ.search(sentence):
        return "required"
    if _NOT.search(sentence):
        return "not_required"
    return "required"


_RANK = {"required": 0, "preferred": 1, "not_required": 2}


def rule_read(text: str, sd: SkillDictionary) -> dict:
    """규칙만: {skills, mentioned_not_required} -- AI가 없을 때와 측정의 기준선."""
    hits = alias_hits(sd, text)
    best: dict[str, dict] = {}
    for i, h in enumerate(hits):
        s, e = _sentence(text, h["start"], h["end"])
        kind = _classify(text[s:e])
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


def _int_in(v, lo: int, hi: int) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def read_requirements(text: str, sd: SkillDictionary, *, client=None, model: str | None = None,
                      reasoning_effort: str | None = None, price: dict | None = None) -> dict:
    """발주 자료 글자 → {skills[{concept_id, name, months, headcount, quote, start, end, kind: required|preferred}],
    mentioned_not_required[{concept_id, name, quote, start, end}], unmapped_terms, method: ai|rule, model, warnings, usage, fallback_reason, dropped}.
    위치(start·end)는 넘겨받은 text 기준이다. AI 결과가 있으면 skills는 AI 것이고, 규칙으로만 찾은 언급은 mentioned_not_required에 남긴다(화면이 보여 줄 수 있게)."""
    text = unicodedata.normalize("NFC", str(text or ""))[:MAX_TEXT]
    rule = rule_read(text, sd)
    warnings = []
    if len(text.strip()) < 30:
        warnings.append("자료에서 읽은 글자가 거의 없다 -- 스캔 이미지 PDF면 글자로 저장한 파일을 올린다.")
    out = {"skills": rule["skills"], "mentioned_not_required": rule["mentioned_not_required"], "unmapped_terms": [],
           "method": "rule", "model": None, "warnings": warnings, "usage": None, "fallback_reason": None, "dropped": []}
    if client is None or not model:
        out["fallback_reason"] = "no_client"
        return out
    ai_text = text[:MAX_AI_TEXT]
    if len(text) > MAX_AI_TEXT:
        warnings.append(f"자료가 길어 앞 {MAX_AI_TEXT:,}자만 AI가 읽었다 -- 뒷부분의 요구는 규칙으로 찾은 언급만 남는다.")
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": TASK.format(text=ai_text, catalog=catalog(sd))}])
        data = json.loads(resp.choices[0].message.content or "{}")
        if not isinstance(data, dict):
            raise TypeError("JSON 객체가 아니다")
    except Exception as exc:                                    # noqa: BLE001 -- 네트워크·API·JSON 오류는 규칙 값으로
        log.warning("발주 자료 AI 읽기 실패: %s", exc)
        out["fallback_reason"] = f"llm_error:{type(exc).__name__}"
        return out
    u = getattr(resp, "usage", None)
    pin, pout = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
    pr = price or {}
    out["usage"] = {"latency_s": round(time.monotonic() - t0, 2), "in": pin, "out": pout, "model": model,
                    "cost_usd": round((pin * pr.get("input_per_1m", 0) + pout * pr.get("output_per_1m", 0)) / 1e6, 6)}
    dropped: list[dict] = []
    skills: list[dict] = []
    seen: set[str] = set()

    def place(item) -> tuple[str, tuple[int, int]] | None:
        if not isinstance(item, dict):
            return None
        cid, quote = str(item.get("id") or ""), str(item.get("quote") or "")
        if cid not in sd.concepts:
            dropped.append({"id": cid, "why": "사전에 없는 id"})
            return None
        span = _locate(ai_text, quote)
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
                       "headcount": _int_in(head, 1, HEADCOUNT_MAX), "quote": ai_text[s:e], "start": s, "end": e,
                       "kind": "preferred" if item.get("kind") == "preferred" else "required"})
    mentioned: dict[str, dict] = {}
    for item in data.get("not_required") or []:
        got = place(item)
        if got is None or got[0] in seen:
            continue
        cid, (s, e) = got
        mentioned.setdefault(cid, {"concept_id": cid, "name": sd.concepts[cid]["label"], "quote": ai_text[s:e], "start": s, "end": e})
    # 규칙이 찾았지만 AI가 요구로 고르지 않은 언급도 "요구 아님"으로 보인다(사람이 다시 볼 수 있게)
    for r in rule["skills"] + rule["mentioned_not_required"]:
        if r["concept_id"] not in seen:
            mentioned.setdefault(r["concept_id"], {k: r[k] for k in ("concept_id", "name", "quote", "start", "end")})
    out.update(skills=sorted(skills, key=lambda r: r["start"]), mentioned_not_required=sorted(mentioned.values(), key=lambda r: r["start"]),
               unmapped_terms=[str(t)[:60] for t in (data.get("unmapped_terms") or []) if isinstance(t, str) and t.strip()][:20],
               method="ai", model=model, dropped=dropped)
    return out
