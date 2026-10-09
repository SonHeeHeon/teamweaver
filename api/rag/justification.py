"""인사팀 소명 글 생성(GraphRAG, 2026-10-09 사용자 결정) -- AI가 지식 그래프 사실로 글을 쓴다.

자리표시 방식(측정 전 설계 변경, core/kg/justify.py 참조): AI는 글의 구성·순서·연결 말만 쓰고 사실은 {F12} 자리표시로 끼운다.
연결 말은 허용 목록(문법: 주어·주제 라벨·연결어·맺음말)만 쓸 수 있다. 서버가 검사(core/kg/justify.verify)를 통과한 원문의 자리표시를
사실 문구로 채운다(render) -- 숫자·충족/미달·다른 사람 ID는 AI가 쓸 수 없다. 채운 뒤 중괄호·근거 칩 수를 한 번 더 확인한다(render_ok).
AI 호출 실패·형식 오류·검사 탈락이면 템플릿 글(method="template")로 대신하고 이유를 남긴다(틀린 부분만 지우지 않는다, K5와 같은 원칙).
모델·추론 강도는 fixtures/pricing.json(briefing_model, models[모델].reasoning_effort). 실데이터의 외부 전송 여부는 교체 설명(/api/whatif)과 같은 서버 정책 --
사실 목록에는 이름·평가 원문·업무 요약이 없다(지식 그래프가 뺀다).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

from core.config import load_pricing
from core.kg.justify import CONNECTIVES, LABELS, SUBJECT_KINDS, JustificationInput, render, render_ok, template_text, verify

log = logging.getLogger(__name__)

def _system() -> str:
    labels = ", ".join(f"'{lb}'" for lb in LABELS)
    return (
        "너는 인사팀에 인력 배치를 소명하는 글의 구성을 맡는다. 사실은 네가 쓰지 않고 자리표시 {F번호}로 끼운다 — 서버가 그 자리에 사실 문구를 그대로 채운다."
        " 네가 쓸 수 있는 말은 아래 문법뿐이다. 그 밖의 말(숫자, 평가·판정, 등급·기술 이름, 수식어)이 한 글자라도 섞이면 글 전체가 버려진다."
        " [문장 머리] 이어 주는 말(" + ", ".join(CONNECTIVES) + ") 없이 또는 하나 + 다음 중 하나:"
        " 팀원 주어('DP0035는', 'DP0035의 경우', 여러 명이면 'DP0035와 DP0036은 각각' — '각각'이면 사실도 주어 순서대로 한 사람에 하나씩),"
        " 또는 주제 라벨 + '은/는'(앞에 'DP0035의 '를 붙일 수 있다)."
        f" 주제 라벨은 이것만: {labels}."
        " [자리표시 사이] ',', '이며', '이고', '및', ', 그리고' 중 하나, 그 뒤에 새 주어나 주제 라벨을 둘 수 있다."
        " [문장 끝] '입니다.' 또는 '.'만. [자리표시 없는 문장] 맺음말 '이상의 근거로 이 배치를 제안합니다.'만."
        " 규칙: (1) '사람' 표시 사실은 그 사람이 주어인 문장에 두거나 주제 라벨 뒤에 둔다(이때 서버가 'DP0035의 …'처럼 주인을 붙인다)."
        " 다른 팀원이 주어인 자리에 두지 않는다. (2) 요구 기술·규칙 같은 사업 전체 사실은 주제 라벨 뒤에 둔다(사람 주어 뒤 금지)."
        " (3) 주제 라벨 뒤에는 그 라벨에 맞는 종류의 사실만 둔다(예: '동료 평가는' 뒤에는 동료 평가 사실만)."
        " (4) 팀원 전원에게 '사람' 사실을 하나 이상 붙인다. 팀원이 많으면 사람마다 한 문장, 자리표시 1~2개로 짧게."
        " (5) 한국어로만 쓴다(다른 언어 글자 금지). 제목·목록 없이 문단으로 쓴다. 문단 나누기는 줄바꿈. 같은 주어·라벨을 여러 문장에 되풀이하지 말고 한 문장에 ','·'이며'로 이어 쓴다."
        " 예: '이 사업은 {F1}입니다. 요구 기술은 {F2}, {F3}입니다. DP0035는 {F4}이며 {F5}입니다. 동료 평가는 {F9}입니다."
        " 함께 일한 이력은 {F8}입니다. 후보 비교 결과는 {F10}입니다. 마지막으로 규칙 준수 현황은 {F11}입니다. 이상의 근거로 이 배치를 제안합니다.'"
        " 출력은 JSON {\"text\": \"...\"} 하나."
    )


SYSTEM = _system()
TASK = ("이 사업에 왜 이 사람들을 배치했는지 인사팀에 소명하는 글을 써라. 팀원 {n}명: {team}. 사업 개요·요구 기술 → 사람별 근거(기술·산업·고객사) → "
        "동료 평가 → 함께 일한 이력 → 다른 후보와의 비교 → 규칙 준수 순서로, 근거가 있는 만큼 다룬다. "
        "'불리' 표시 사실(부족·미달·위반·더 나은 후보·부정 평가)도 빼지 말고 넣는다('다만 {{F7}}입니다'처럼).")


def prompt(inp: JustificationInput) -> str:
    lines = []
    for f in inp.facts:
        tag = f"[{f.kind}" + (f", 사람: {f.owners[0]}" if f.kind in SUBJECT_KINDS else "") + (", 불리" if f.adverse else "") + "]"
        lines.append(f"{{{f.id}}} {tag} {f.phrase}")
    return TASK.format(n=len(inp.team), team=", ".join(inp.team)) + "\n사실 목록(자리표시 → 서버가 채울 문구):\n" + "\n".join(lines)


def generate_justification(client, model: str, inp: JustificationInput, *, reasoning_effort: str | None = None) -> dict:
    """반환: {method: graphrag|template, text(보여 줄 글), facts, verification, fallback_reason, llm_text(AI 원문, 자리표시 포함), usage}.
    client가 None이면(키 없음·외부 전송 불가) 바로 템플릿."""
    out = {"method": "template", "text": template_text(inp.facts), "facts": [asdict(f) for f in inp.facts],
           "verification": None, "fallback_reason": None, "llm_text": None, "usage": None, "project_id": inp.project_id}
    if client is None:
        out["fallback_reason"] = "no_client"
        return out
    price = load_pricing().get("models", {}).get(model, {})
    effort = reasoning_effort if reasoning_effort is not None else price.get("reasoning_effort")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt(inp)}]   # 지시문 오류는 호출 실패로 숨기지 않는다
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, **({"reasoning_effort": effort} if effort else {}), messages=messages)
    except Exception as exc:                                    # noqa: BLE001 -- 네트워크·API 오류는 모두 템플릿으로
        log.warning("소명 글 AI 호출 실패 %s: %s", inp.project_id, exc)
        out["fallback_reason"] = f"llm_error:{type(exc).__name__}"
        return out
    u = getattr(resp, "usage", None)
    pin, pout = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
    out["usage"] = {"latency_s": round(time.monotonic() - t0, 2), "in": pin, "out": pout, "model": model, "reasoning_effort": effort,
                    "cost_usd": round((pin * price.get("input_per_1m", 0) + pout * price.get("output_per_1m", 0)) / 1e6, 6)}
    try:
        raw = json.loads(resp.choices[0].message.content or "{}").get("text", "")
        if not isinstance(raw, str):
            raise TypeError("text가 문자열이 아니다")
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        out["fallback_reason"] = f"llm_bad_json:{type(exc).__name__}"
        return out
    out["llm_text"] = raw
    try:
        ver = verify(raw, inp)
    except Exception as exc:                                    # noqa: BLE001 -- 검사기 오류도 채택하지 않고 템플릿으로
        log.warning("소명 글 검사 오류 %s: %s", inp.project_id, exc)
        out["fallback_reason"] = f"verify_error:{type(exc).__name__}"
        return out
    out["verification"] = ver
    if ver["ok"]:
        text = render(raw, inp)
        if render_ok(raw, text):
            out.update(method="graphrag", text=text)
        else:                                                   # 검사와 따로 하는 마지막 확인(중괄호·근거 칩 수)
            out["fallback_reason"] = "render_check"
            log.warning("소명 글 채움 확인 실패 %s", inp.project_id)
    else:
        out["fallback_reason"] = "verify:" + ",".join(sorted({v["rule"] for v in ver["violations"]}))
        log.info("소명 글 검사 탈락 %s: %s", inp.project_id, out["fallback_reason"])
    return out
