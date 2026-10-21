"""인사팀 소명 글(GraphRAG) API -- claude-a 계약 `docs/requests/2026-10-09-hr-justification.md`.

계산·검사는 claude-a(`core.kg.justify.justification_input`, `api.rag.justification.generate_justification`).
여기서는 (1) 명단을 서버가 정하고(원 플랜 + 적용한 변경을 다시 적용, PDF와 같은 방식 -- 조작 방지),
(2) 데이터 종류에 따라 AI를 쓸지 정하고(api.deps.llm_client_for: 실데이터는 사내·허용일 때만),
(3) 화면에 보내도 되는 것만 내보낸다: `llm_text`(검사 전 AI 원문, 탈락한 글 포함)와 검사 위반 문장은 보내지 않는다.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Annotated

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_openai_client_or_none, llm_client_for
from api.schemas import MAX_APPLIED_SWAPS, MAX_PLAN_ENTRIES, EntryIn, MilpParamsIn, StepIn
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine

log = logging.getLogger(__name__)
router = APIRouter()

AI_TIMEOUT_S = 40.0          # 한 사업 AI 글 p95 10~18초(E7b) -- 걸리면 템플릿으로. 모델별 값은 ai_timeout_s()(아래)


def ai_model() -> str:
    from core.config import load_pricing
    return load_pricing()["briefing_model"]


def ai_timeout_s(model: str | None = None) -> float:
    """AI 글 한 건을 기다리는 상한(초). 모델별 설정 `api.rag.justification.justification_settings`(사용자 결정 2026-10-11 (가):
    사내 AI로 바꿔 적용하면 더 오래 기다린다 -- GLM-5.3-Flash 300초, 외부 luna는 이 파일의 AI_TIMEOUT_S와 같은 40초).
    claude-a가 사용자 지시로 고쳤다(work-split 2026-10-11)."""
    from api.rag.justification import justification_settings
    return justification_settings(model or ai_model())["timeout_s"]
AI_SLOTS = threading.BoundedSemaphore(6)   # 화면 요청의 동시 AI 호출 상한 -- 사업을 빠르게 넘겨 쌓인 요청이 스레드·비용을 잡지 않게(폴백 리뷰 SHOULD)
PDF_WORKERS = 6              # PDF에 사업마다 넣을 때 동시 호출 수(계약 제안)


class JustificationRequest(BaseModel):
    dataset_version: str
    project_id: str
    # 화면에 보이는 명단. 변경(applied_swaps)이 있으면 쓰지 않고 base_entries에서 서버가 다시 적용한다.
    entries: list[EntryIn] = Field(max_length=MAX_PLAN_ENTRIES)
    base_entries: list[EntryIn] | None = Field(default=None, max_length=MAX_PLAN_ENTRIES)
    applied_swaps: list[StepIn] = Field(default=[], max_length=MAX_APPLIED_SWAPS)
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: MilpParamsIn | None = None
    # False면 AI를 부르지 않고 정해진 틀(템플릿)만 -- 화면은 이것을 먼저 보이고 AI 글로 바꾼다(AI p50 약 8초)
    ai: bool = True
    # 서버가 계산한 원 플랜의 서명(/api/optimize의 plan_token). 인사팀에 내보내는 근거라 서명이 맞는 명단에만 쓴다
    # (Codex 리뷰 P1: 서명 없이 받으면 아무 명단이나 그럴듯한 소명을 받을 수 있다). 변경은 서버가 원 명단에서 다시 적용한다.
    plan_label: str = Field(min_length=1, max_length=8)
    plan_token: str = Field(min_length=1, max_length=512)


def verify_roster(req: JustificationRequest, version: str) -> None:
    """원 플랜 서명 확인(api.routes.report와 같은 규칙). 틀리면 422."""
    from api.plan_token import verify_plan
    base = req.base_entries if req.applied_swaps else req.entries
    params = req.milp_params.to_milp_params() if req.milp_params else MilpParams()
    if base is None or not verify_plan(req.plan_token, version, req.plan_label,
                                       [e.model_dump() for e in base], req.weights, params):
        raise HTTPException(status_code=422, detail="원 플랜 서명이 맞지 않는다(명단·가중치·설정·데이터셋 중 하나가 바뀌었다) "
                                                    "-- 서버가 계산한 배치에만 소명 글을 만든다.")


def resolved_entries(graph, S, C, params: MilpParams, weights: dict, entries, base_entries, applied_swaps) -> list:
    """소명 대상 명단: 변경이 있으면 원 명단에서 서버가 다시 적용한 결과(api.routes.report와 같은 규칙)."""
    if not applied_swaps:
        return list(entries)
    if base_entries is None:
        raise HTTPException(status_code=422, detail="applied_swaps가 있으면 base_entries(원 플랜 명단)가 필요하다")
    from api.routes.plans import apply_step
    cur, ubs = list(base_entries), {}
    for step in applied_swaps:
        last = apply_step(graph, S, C, params, weights, cur, step, ubs=ubs)
        cur = [EntryIn(**e) for e in last["entries"]]
    return cur


def public_view(out: dict) -> dict:
    """화면·PDF로 보내는 모양. AI 원문(`llm_text`)과 검사 위반 문장(탈락한 AI 문장)은 빼고 규칙 코드만 남긴다."""
    ver = out.get("verification")
    summary = None
    if ver:
        summary = {"ok": ver.get("ok"), "rules": sorted({v.get("rule") for v in ver.get("violations", []) if v.get("rule")}),
                   "cited": ver.get("cited", []), "coverage": ver.get("coverage"), "adverse": ver.get("adverse")}
    usage = out.get("usage") or None
    return {"project_id": out.get("project_id"), "method": out.get("method"), "text": out.get("text"),
            "addendum": out.get("addendum"), "appended_adverse": out.get("appended_adverse") or [],
            # 사람별 덧붙임 줄의 묶음(claude-a 2026-10-11) -- 화면이 "확인 필요 n명 · m건"으로 접고 사람별로 펼친다
            "addendum_groups": [{"owner": g.get("owner"), "owner_label": g.get("owner"), "facts": list(g.get("facts") or [])}
                                for g in (out.get("addendum_groups") or [])],
            "facts": out.get("facts") or [], "fallback_reason": out.get("fallback_reason"), "verification": summary,
            # 요약(2026-10-20, 같은 문법·검사 -- 걸리면 규칙 요약): 화면은 이것을 먼저, 본문은 "전체 보기"로
            "summary": out.get("summary"), "summary_method": out.get("summary_method"),
            "summary_fallback_reason": out.get("summary_fallback_reason"),
            "usage": ({k: usage.get(k) for k in ("model", "latency_s", "cost_usd", "reasoning_effort")} if usage else None)}


_PAIRS = {"은": "은/는", "는": "은/는", "이": "이/가", "가": "이/가", "을": "을/를", "를": "을/를", "과": "과/와", "와": "과/와",
          "으로": "으로/로", "로": "으로/로",
          # ID 뒤에 받침을 몰라 괄호로 쓴 조사(규칙 설명 폴백의 "DP0004(으)로 교체 검토") -- 이름으로 바꾸면 받침에 맞춘다
          "(으)로": "으로/로", "(이)가": "이/가", "은(는)": "은/는", "는(은)": "은/는", "이(가)": "이/가", "가(이)": "이/가",
          "을(를)": "을/를", "를(을)": "을/를", "과(와)": "과/와", "와(과)": "과/와"}


def display_names(dataset: ActiveDataset) -> dict[str, str]:
    """가상 데이터의 사람 ID → 표시 이름(사용자 요청 2026-10-11: "DP0082 대신 가상의 이름"). 실데이터는 빈 사전 --
    실명은 화면·AI에 보내지 않는다(K5). 이름이 아직 자리표시("가상DP0001"처럼 ID를 담은 값)면 바꾸지 않는다."""
    if dataset.info.synthetic is not True:
        return {}
    return {p.id: p.name for p in dataset.graph.people if p.name and p.id not in p.name and p.name.strip()}


def with_names(text: str | None, names: dict[str, str]) -> str | None:
    """글 속 사람 ID를 이름으로 바꾸고 바로 뒤 조사(은/는·이/가·을/를·과/와·으로/로)를 이름 받침에 맞춘다(core.kg.justify.josa)."""
    if not text or not names:
        return text
    import re
    from core.kg.justify import josa
    ids = sorted(names, key=len, reverse=True)
    # 조사는 그 뒤가 띄어쓰기·문장부호·끝일 때만 본다 -- "DP0001이며"의 '이'는 조사가 아니라 서술격이다
    particles = "|".join(re.escape(k) for k in sorted(_PAIRS, key=len, reverse=True))   # 긴 것부터("(으)로"가 "로"보다 먼저)
    pattern = re.compile("(" + "|".join(re.escape(i) for i in ids) + r")(?:(" + particles + r")(?=[\s,.;:!?)\]\[]|$))?(?![0-9A-Za-z])")

    def repl(m):
        name, particle = names[m.group(1)], m.group(2)
        return name + (josa(name, _PAIRS[particle]) if particle else "")
    # 대괄호 표시([F12]·[rv:DP0001>DP0002#1:pos] 근거 출처)는 건드리지 않는다 -- 출처 ID가 깨지면 근거 대조가 안 된다
    parts = re.split(r"(\[[^\]]*\])", text)
    return "".join(p if p.startswith("[") and p.endswith("]") else pattern.sub(repl, p) for p in parts)


def apply_names(out: dict, names: dict[str, str]) -> dict:
    """응답의 글·덧붙임·사실 문구에 같은 치환을 한다 -- 화면이 사실 문구로 글 속 위치를 찾으므로 둘이 같아야 한다."""
    if not names:
        return out
    facts = [{**f, **{k: with_names(f.get(k), names) for k in ("text", "phrase", "phrase_full")}} for f in out.get("facts", [])]
    groups = [{**g, "owner_label": names.get(g.get("owner") or "", g.get("owner"))} for g in out.get("addendum_groups", [])]
    return {**out, "text": with_names(out.get("text"), names), "addendum": with_names(out.get("addendum"), names),
            "summary": with_names(out.get("summary"), names), "facts": facts, "addendum_groups": groups}


def build_input(kg, project_id: str, entries, graph, S, C, params):
    """사업 하나의 소명 재료(사실·불리한 사실·팀). 저장본 키에도 이 재료의 해시가 들어간다."""
    from core.kg.justify import justification_input
    from core.optimize.types import AssignEntry
    roster = [e if isinstance(e, AssignEntry) else AssignEntry(**(e.model_dump() if hasattr(e, "model_dump") else e))
              for e in entries]
    return justification_input(kg, project_id, roster, graph=graph, S=S, C=C, params=params)


def justify_one(dataset: ActiveDataset, kg, project_id: str, entries, graph, S, C, params, client, *, ai: bool,
                inp=None) -> dict:
    """사업 하나의 소명. client가 None이면 템플릿. inp를 이미 만들었으면 넘긴다(저장본 키와 같은 재료)."""
    from api.rag.justification import generate_justification
    if inp is None:
        inp = build_input(kg, project_id, entries, graph, S, C, params)
    out = generate_justification(client if ai else None, ai_model(), inp, summary=True)
    if not ai:
        out["fallback_reason"] = None          # 화면이 템플릿을 먼저 달라고 했다(실패가 아니다)
    # 이름 치환은 검사·채움이 끝난 뒤 보여 줄 글에만 한다(AI·검사기는 ID 문법 그대로 -- E7c 측정 조건 유지)
    return apply_names(public_view(out), display_names(dataset))


def _client_with_timeout(client, timeout: float | None = None, max_retries: int = 0):
    if client is None:
        return None
    timeout = ai_timeout_s() if timeout is None else timeout   # 설정 오류는 감추지 않는다(아래 except가 삼키면 SDK 기본 600초로 돈다)
    try:
        return client.with_options(timeout=timeout, max_retries=max_retries)
    except Exception:                                   # noqa: BLE001 -- 테스트 대역 등
        return client


# --- 저장된 AI 글(2026-10-18, 야간 배치 2번 -- api.justification_cache) ----------------------------------------------

def saved_key(dataset: ActiveDataset, project_id: str, entries, weights: dict, params: MilpParams, client,
              inp) -> str | None:
    """이 요청의 저장본 키. AI를 쓸 수 없는 요청(client None)은 None -- 저장본도 AI 글이라 같은 AI 조건에서만 꺼낸다.
    inp(소명 재료)의 해시가 들어가므로 평가기·점수 코드가 바뀌어 사실이 달라지면 옛 글은 쓰이지 않는다."""
    if client is None:
        return None
    from api.justification_cache import ai_fingerprint, input_fingerprint, key_for
    from api.rag.justification import justification_settings
    model = ai_model()
    return key_for(dataset.info.version, project_id, entries, weights, params,
                   ai_fingerprint(client, model, justification_settings(model)["reasoning_effort"]),
                   inputs=input_fingerprint(inp))


def load_saved(dataset: ActiveDataset, key: str | None) -> dict | None:
    if key is None:
        return None
    from api.justification_cache import JustificationCache
    return JustificationCache().get(dataset.info.version, key)


def prune_other_versions(keep_version: str) -> None:
    from api.justification_cache import JustificationCache
    try:
        JustificationCache().prune(keep_version)
    except OSError as exc:
        log.warning("옛 데이터의 소명 글을 지우지 못했다: %s", exc)


def save_ai_text(dataset: ActiveDataset, key: str | None, out: dict, source: str) -> bool:
    """AI 글(graphrag)이면 저장한다. 저장 실패는 응답을 막지 않는다(로그만)."""
    if key is None or out.get("method") != "graphrag":
        return False
    if getattr(dataset, "retired", False):              # 데이터가 바뀐 뒤 끝난 요청 -- 옛 데이터의 글은 남기지 않는다
        return False
    from api.justification_cache import JustificationCache
    try:
        return JustificationCache().put(dataset.info.version, key, out, source)
    except OSError as exc:
        log.warning("소명 글 저장 실패: %s", exc)
        return False


def prepare(dataset: ActiveDataset, weights: dict, milp_params: MilpParamsIn | None):
    kg = dataset.knowledge_graph()
    if kg is None:
        raise HTTPException(status_code=409, detail="인사팀 소명 글은 CSV 묶음 데이터(업로드·시연 데이터)에서만 만들 수 있다 "
                                                    "-- 지금 데이터는 고정 데모 데이터라 원 표가 없다.")
    params = milp_params.to_milp_params() if milp_params else MilpParams()
    eng = ScoringEngine(dataset.graph)
    return kg, params, eng.skill_matrix(weights), eng.synergy_matrix()


@router.post("/api/justification")
async def justification(req: JustificationRequest, request: Request, dataset: ActiveDataset = Depends(get_dataset),
                        client=Depends(get_openai_client_or_none)) -> dict:
    check_dataset_version(req.dataset_version, dataset)
    if req.project_id not in dataset.graph.project_index:
        raise HTTPException(status_code=404, detail=f"없는 사업 ID: {req.project_id}")
    client, blocked = llm_client_for(dataset, client)
    if req.applied_swaps and req.base_entries is None:
        raise HTTPException(status_code=422, detail="applied_swaps가 있으면 base_entries(원 플랜 명단)가 필요하다")
    verify_roster(req, dataset.info.version)

    def go():
        kg, params, S, C = prepare(dataset, req.weights, req.milp_params)
        entries = resolved_entries(dataset.graph, S, C, params, req.weights, req.entries, req.base_entries, req.applied_swaps)
        if not any(e.project_id == req.project_id for e in entries):
            raise HTTPException(status_code=404, detail=f"이 배치에 {req.project_id} 사업의 팀원이 없다")
        # 옛 데이터의 글은 지운다(지금 켜진 데이터만 남김) -- 데이터 전환 뒤 첫 소명 요청에서. 전환 경로에서 바로 지우는 것은
        # claude-a 요청(work-split 2026-10-18 (5)); 부팅·밤 실행 시작 때도 지운다
        prune_other_versions(request.app.state.dataset.info.version)
        # 같은 명단·설정·AI·생성기로 이미 쓴 AI 글(밤 배치가 미리 썼거나 낮에 쓴 것)이 있으면 그대로 -- AI를 다시 부르지 않는다
        inp = build_input(kg, req.project_id, entries, dataset.graph, S, C, params)
        key = saved_key(dataset, req.project_id, entries, req.weights, params, client, inp) if req.ai else None
        # 근거 한눈에(표·그림 재료) -- 저장본에는 넣지 않고 응답마다 같은 재료에서 붙인다
        from api.justification_evidence import evidence_view   # 화면 모양만 -- 저장본 코드 지문(_CODE_FILES) 밖에 둔다
        evidence = evidence_view(getattr(inp, "evidence", None), display_names(dataset))
        saved = load_saved(dataset, key)
        if saved is not None:
            return {**saved, "evidence": evidence}
        use_ai = req.ai and client is not None and AI_SLOTS.acquire(blocking=False)
        try:
            out = justify_one(dataset, kg, req.project_id, entries, dataset.graph, S, C, params,
                              _client_with_timeout(client) if use_ai else None, ai=bool(use_ai), inp=inp)
        finally:
            if use_ai:
                AI_SLOTS.release()
        if use_ai:
            save_ai_text(dataset, key, out, "live")
        if req.ai and client is None:
            out["fallback_reason"] = blocked          # no_client | external_blocked
        elif req.ai and not use_ai:
            out["fallback_reason"] = "busy"           # 다른 AI 글이 많이 돌고 있다 -- 정해진 틀로, 잠시 뒤 '다시 쓰기'
        return {**out, "evidence": evidence}
    out = await anyio.to_thread.run_sync(go)
    from api.rag.justification import justification_settings
    return {**out, "dataset_version": dataset.info.version, "ai_available": client is not None,
            "ai_unavailable_reason": None if client is not None else blocked,
            "ai_expected_s": justification_settings(ai_model())["expected_s"]}     # 화면 안내("약 N초")용 -- 모델마다 다르다


def justify_projects(dataset: ActiveDataset, entries, weights: dict, milp_params: MilpParamsIn | None, client) -> list[dict]:
    """PDF용: 배치에 팀원이 있는 사업마다 소명(동시 PDF_WORKERS개). 스레드에서 부른다."""
    from core.optimize.types import AssignEntry
    entries = [e if isinstance(e, AssignEntry) else AssignEntry(**(e.model_dump() if hasattr(e, "model_dump") else e))
               for e in entries]
    kg, params, S, C = prepare(dataset, weights, milp_params)
    client, blocked = llm_client_for(dataset, client)
    timed = _client_with_timeout(client)
    projects = [p.id for p in dataset.graph.projects if any(e.project_id == p.id for e in entries)]

    def one(pid: str) -> dict:
        inp = build_input(kg, pid, entries, dataset.graph, S, C, params)
        key = saved_key(dataset, pid, entries, weights, params, client, inp)
        saved = load_saved(dataset, key)                # 밤 배치가 미리 쓴 글이면 PDF가 AI를 기다리지 않는다
        if saved is not None:
            return saved
        out = justify_one(dataset, kg, pid, entries, dataset.graph, S, C, params, timed, ai=True, inp=inp)
        if client is None:
            out["fallback_reason"] = blocked
        else:
            save_ai_text(dataset, key, out, "live")
        return out
    with ThreadPoolExecutor(max_workers=PDF_WORKERS) as pool:
        return list(pool.map(one, projects))
