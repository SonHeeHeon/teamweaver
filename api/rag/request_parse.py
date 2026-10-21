"""긴급 투입 "상황을 말로"(2026-10-22 사용자 "4번은 말로 하게되면 추천안(인력)을 받아 선택하도록", claude-a, 설계 루트 `.omc/plan/2026-10-22-ai-value.md`).

부서장이 쓴 상황 글을 **요구(기술·인원·등급)로만** 바꾼다. 사람은 AI에 보내지 않는다 -- 추천은 기존 계산(최선 조합·후보 순위)이, 결정은 부서장이 한다
(조사: LLM 순위 편향·없는 항목 추천, EU AI Act 업무 배정 고위험·사람 감독, `.omc/research/reports/자연어 인력 요청과 기술명 정규화.md`).

순서
1. 사전 별칭 찾기(AI 없음): 기술 이름 사전(228개념·별칭 621개)의 이름이 글에 그대로 있으면 원문 위치와 함께 잡는다.
2. AI 한 번(json_object -- 사내 OpenAI 호환 서버도 같은 방식): 사전 id 목록 안에서만 `skills[{id, quote, kind}]`·`headcount`·`grade`·`unmapped_terms`·`question`.
3. 서버 검사: id가 사전에 있고 quote가 원문에 있어야 받는다(위치는 서버가 다시 잰다). 인원 1~5, 등급은 넷 중 하나. 어긋난 칸만 버린다.
   나이·성별 같은 사람 특성 말은 조건으로 쓰지 않고 경고한다.
4. AI가 없거나(키 없음·실데이터 외부 차단) 실패하면 1번 + 숫자·등급 정규식(method rule).
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata

from core.ingest.skills import SkillDictionary, norm_key

log = logging.getLogger(__name__)

GRADES = ("특급", "고급", "중급", "초급")
MAX_TEXT = 300
HEADCOUNT_MAX = 5
# 사람 특성(나이·성별·혼인·출신·종교·장애 여부·외모)으로 사람을 거르는 말 -- 요구로 쓰지 않는다(조사: 보호 속성 반영 0건이 기준).
# "장애"는 IT에서 대개 시스템 장애라 장애인·장애 여부만, 나이는 "30대"·"35세"처럼 사람 나이 꼴만("3대 서버"·"3세대"는 아니다)
PROTECTED = re.compile(r"나이|연령|[1-9]\d?\s*(?:세|살)(?!대)(?:\s*(?:이하|이상|미만|까지))?|[2-6]0\s*대(?!\s*(?:서버|장비|노드|이상의?\s*서버))|젊은|어린|"
                       r"여성|여자|남성|남자|성별|결혼|미혼|기혼|출산|임신|국적|출신|고향|종교|장애인|장애\s*여부|외모|키\s*큰")
_NUM_WORD = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
_HEAD = re.compile(r"(다섯|한|두|세|네|\d+)\s*(?:명|분|인|사람)")
_GRADE = re.compile(r"(특급|고급|중급|초급)\s*(이상)?")


def _norm_map(text: str) -> tuple[str, list[int]]:
    """norm_key와 같은 규칙으로 바꾼 글과, 바꾼 글의 각 글자가 원문 몇 번째 글자에서 왔는지."""
    out, idx = [], []
    for i, ch in enumerate(text):
        k = norm_key(ch)
        for c in k:
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def _ascii_word(c: str) -> bool:
    return c.isascii() and c.isalnum()


def alias_hits(sd: SkillDictionary, text: str) -> list[dict]:
    """사전 이름(대표·별칭)이 글에 그대로 있는 곳 -- 긴 이름 먼저, 겹치지 않게. 영문 이름은 앞뒤가 영문·숫자로 이어지면 다른 단어다("Go" in "Google")."""
    norm, idx = _norm_map(text)
    taken = [False] * len(norm)
    hits: list[dict] = []
    for key in sorted(sd.by_key, key=len, reverse=True):
        if len(key) < 2:
            continue
        start = 0
        while (pos := norm.find(key, start)) >= 0:
            end = pos + len(key)
            start = pos + 1
            if any(taken[pos:end]):
                continue
            if _ascii_word(key[0]) and pos > 0 and _ascii_word(norm[pos - 1]):
                continue
            if _ascii_word(key[-1]) and end < len(norm) and _ascii_word(norm[end]):
                continue
            for k in range(pos, end):
                taken[k] = True
            cid = sd.by_key[key]
            s, e = idx[pos], idx[end - 1] + 1
            hits.append({"id": cid, "name": sd.concepts[cid]["label"], "quote": text[s:e], "start": s, "end": e,
                         "kind": "explicit", "source": "alias"})
    hits.sort(key=lambda h: h["start"])
    return hits


def rule_fields(text: str) -> dict:
    """인원·등급을 글에서 정규식으로(AI가 없을 때·AI 값이 틀렸을 때 쓴다)."""
    out: dict = {"headcount": None, "grade": None, "grade_at_least": False}
    m = _HEAD.search(text)
    if m and _NUM_WORD.get(m.group(1)):
        out["headcount"] = min(HEADCOUNT_MAX, _NUM_WORD[m.group(1)])
    m = _GRADE.search(text)
    if m:
        out["grade"], out["grade_at_least"] = m.group(1), bool(m.group(2))
    return out


def protected_terms(text: str) -> list[str]:
    return sorted({m.group(0).strip() for m in PROTECTED.finditer(text)})


def _locate(text: str, quote: str) -> tuple[int, int] | None:
    """quote가 원문 어디에 있나(그대로 → 정규화해서). 없으면 None -- 그 칸은 받지 않는다."""
    if not quote or not quote.strip():
        return None
    q = quote.strip()
    pos = text.find(q)
    if pos >= 0:
        return pos, pos + len(q)
    norm, idx = _norm_map(text)
    k = norm_key(q)
    pos = norm.find(k) if k else -1
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(k) - 1] + 1


SYSTEM = ("너는 SI 회사 부서장이 쓴 긴급 인력 요청 글을 요구 항목으로 바꾸는 도우미다. 사람을 고르거나 추천하지 않는다. "
          "기술은 주어진 사전 목록의 id만 쓴다. JSON 하나만 답한다.")

TASK = """요청 글:
<<<{text}>>>

사전(기술 id | 대표 이름 | 다른 이름):
{catalog}

규칙
- skills: 이 일에 필요한 기술. 각 항목 {{"id": 사전 id, "quote": 근거가 되는 요청 글의 말 그대로(글자 그대로 복사), "kind": "explicit" 또는 "implied"}}.
  explicit = 기술 이름(또는 다른 이름)을 글에 직접 썼다. implied = 이름은 없지만 글에 적힌 일 때문에 필요하다고 미루어 본 기술(근거가 되는 말을 quote에).
  사전에 맞는 id가 없으면 넣지 말고 unmapped_terms에 그 말을 적는다. 같은 id는 한 번만.
- headcount: 필요한 인원(정수 1~5). 글에 없으면 null.
- grade: "특급"|"고급"|"중급"|"초급" 중 하나 또는 null. grade_at_least: "이상"이라고 썼으면 true.
- 나이·성별·혼인·출신·외모 같은 사람 특성은 요구로 쓰지 않는다(무시).
- question: 기술을 하나도 정할 수 없으면 부서장에게 물을 짧은 질문 하나, 아니면 null.
답 형식: {{"skills": [...], "headcount": null, "grade": null, "grade_at_least": false, "unmapped_terms": [], "question": null}}"""


def catalog(sd: SkillDictionary, max_aliases: int = 4) -> str:
    lines = []
    for cid, c in sd.concepts.items():
        al = [a for a in (c.get("aliases") or [])][:max_aliases]
        lines.append(f"{cid} | {c['label']}" + (f" | {', '.join(al)}" if al else ""))
    return "\n".join(lines)


def parse_request(text: str, sd: SkillDictionary, *, client=None, model: str | None = None,
                  reasoning_effort: str | None = None, price: dict | None = None) -> dict:
    """상황 글 → {skills[{id, name, quote, start, end, kind, source}], headcount, grade, grade_at_least, unmapped_terms, question,
    warnings, method: ai|rule, fallback_reason, usage}. skills는 별칭 찾기(source alias)와 AI(source ai)를 합친 것(같은 id는 별칭 쪽 위치를 쓴다)."""
    text = unicodedata.normalize("NFC", str(text or ""))[:MAX_TEXT]
    hits = alias_hits(sd, text)
    rule = rule_fields(text)
    prot = protected_terms(text)
    warnings = ([f"사람 특성 말({', '.join(prot)})은 요구로 쓰지 않는다 -- 기술·인원·등급만 본다."] if prot else [])
    out = {"text": text, "skills": hits, **rule, "unmapped_terms": [], "question": None, "warnings": warnings,
           "method": "rule", "fallback_reason": None, "usage": None, "dropped": []}
    if client is None or not model:
        out["fallback_reason"] = "no_client"
        return out
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": TASK.format(text=text, catalog=catalog(sd))}])
        data = json.loads(resp.choices[0].message.content or "{}")
        if not isinstance(data, dict):
            raise TypeError("JSON 객체가 아니다")
    except Exception as exc:                                    # noqa: BLE001 -- 네트워크·API·JSON 오류는 규칙 값으로
        log.warning("요청 글 AI 읽기 실패: %s", exc)
        out["fallback_reason"] = f"llm_error:{type(exc).__name__}"
        return out
    u = getattr(resp, "usage", None)
    pin, pout = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
    pr = price or {}
    out["usage"] = {"latency_s": round(time.monotonic() - t0, 2), "in": pin, "out": pout, "model": model,
                    "cost_usd": round((pin * pr.get("input_per_1m", 0) + pout * pr.get("output_per_1m", 0)) / 1e6, 6)}
    seen = {h["id"] for h in hits}
    dropped = []
    for item in data.get("skills") or []:
        if not isinstance(item, dict):
            continue
        cid, quote, kind = str(item.get("id") or ""), str(item.get("quote") or ""), item.get("kind")
        if cid not in sd.concepts:
            dropped.append({"id": cid, "why": "사전에 없는 id"})
            continue
        if cid in seen:
            continue
        span = _locate(text, quote)
        if span is None:
            dropped.append({"id": cid, "why": "근거 말이 글에 없다"})
            continue
        if PROTECTED.search(text[span[0]:span[1]]):
            dropped.append({"id": cid, "why": "사람 특성 말을 근거로 썼다"})
            continue
        seen.add(cid)
        out["skills"].append({"id": cid, "name": sd.concepts[cid]["label"], "quote": text[span[0]:span[1]], "start": span[0], "end": span[1],
                              "kind": "implied" if kind == "implied" else "explicit", "source": "ai"})
    out["skills"].sort(key=lambda h: (h["start"], h["source"] != "alias"))
    hc = data.get("headcount")
    if isinstance(hc, int) and not isinstance(hc, bool) and 1 <= hc <= HEADCOUNT_MAX:
        out["headcount"] = hc
    g = data.get("grade")
    if g in GRADES:
        out["grade"], out["grade_at_least"] = g, bool(data.get("grade_at_least"))
    out["unmapped_terms"] = [str(t)[:40] for t in (data.get("unmapped_terms") or []) if isinstance(t, str) and t.strip()][:10]
    q = data.get("question")
    out["question"] = str(q)[:200] if isinstance(q, str) and q.strip() else None
    out.update(method="ai", dropped=dropped)
    return out
