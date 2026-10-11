"""인사팀 소명 글(GraphRAG) API -- claude-a 계약 `docs/requests/2026-10-09-hr-justification.md`.

계산·검사는 claude-a(`core.kg.justify.justification_input`, `api.rag.justification.generate_justification`).
여기서는 (1) 명단을 서버가 정하고(원 플랜 + 적용한 변경을 다시 적용, PDF와 같은 방식 -- 조작 방지),
(2) 데이터 종류에 따라 AI를 쓸지 정하고(api.deps.llm_client_for: 실데이터는 사내·허용일 때만),
(3) 화면에 보내도 되는 것만 내보낸다: `llm_text`(검사 전 AI 원문, 탈락한 글 포함)와 검사 위반 문장은 보내지 않는다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Annotated

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_openai_client_or_none, llm_client_for
from api.schemas import MAX_APPLIED_SWAPS, MAX_PLAN_ENTRIES, EntryIn, MilpParamsIn, StepIn
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine

log = logging.getLogger(__name__)
router = APIRouter()

AI_TIMEOUT_S = 40.0          # 한 사업 AI 글 p95 10~18초(E7b) -- 걸리면 템플릿으로
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
            "facts": out.get("facts") or [], "fallback_reason": out.get("fallback_reason"), "verification": summary,
            "usage": ({k: usage.get(k) for k in ("model", "latency_s", "cost_usd", "reasoning_effort")} if usage else None)}


def justify_one(dataset: ActiveDataset, kg, project_id: str, entries, graph, S, C, params, client, *, ai: bool) -> dict:
    """사업 하나의 소명. client가 None이면 템플릿."""
    from api.rag.justification import generate_justification
    from core.kg.justify import justification_input
    from core.config import load_pricing
    from core.optimize.types import AssignEntry
    roster = [e if isinstance(e, AssignEntry) else AssignEntry(**(e.model_dump() if hasattr(e, "model_dump") else e))
              for e in entries]
    inp = justification_input(kg, project_id, roster, graph=graph, S=S, C=C, params=params)
    out = generate_justification(client if ai else None, load_pricing()["briefing_model"], inp)
    if not ai:
        out["fallback_reason"] = None          # 화면이 템플릿을 먼저 달라고 했다(실패가 아니다)
    return public_view(out)


def _client_with_timeout(client):
    if client is None:
        return None
    try:
        return client.with_options(timeout=AI_TIMEOUT_S, max_retries=0)
    except Exception:                                   # noqa: BLE001 -- 테스트 대역 등
        return client


def prepare(dataset: ActiveDataset, weights: dict, milp_params: MilpParamsIn | None):
    kg = dataset.knowledge_graph()
    if kg is None:
        raise HTTPException(status_code=409, detail="인사팀 소명 글은 CSV 묶음 데이터(업로드·시연 데이터)에서만 만들 수 있다 "
                                                    "-- 지금 데이터는 고정 데모 데이터라 원 표가 없다.")
    params = milp_params.to_milp_params() if milp_params else MilpParams()
    eng = ScoringEngine(dataset.graph)
    return kg, params, eng.skill_matrix(weights), eng.synergy_matrix()


@router.post("/api/justification")
async def justification(req: JustificationRequest, dataset: ActiveDataset = Depends(get_dataset),
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
        out = justify_one(dataset, kg, req.project_id, entries, dataset.graph, S, C, params,
                          _client_with_timeout(client), ai=req.ai)
        if req.ai and client is None:
            out["fallback_reason"] = blocked          # no_client | external_blocked
        return out
    out = await anyio.to_thread.run_sync(go)
    return {**out, "dataset_version": dataset.info.version, "ai_available": client is not None,
            "ai_unavailable_reason": None if client is not None else blocked}


def justify_projects(dataset: ActiveDataset, entries, weights: dict, milp_params: MilpParamsIn | None, client) -> list[dict]:
    """PDF용: 배치에 팀원이 있는 사업마다 소명(동시 PDF_WORKERS개). 스레드에서 부른다."""
    from core.optimize.types import AssignEntry
    entries = [e if isinstance(e, AssignEntry) else AssignEntry(**(e.model_dump() if hasattr(e, "model_dump") else e))
               for e in entries]
    kg, params, S, C = prepare(dataset, weights, milp_params)
    client, blocked = llm_client_for(dataset, client)
    client = _client_with_timeout(client)
    projects = [p.id for p in dataset.graph.projects if any(e.project_id == p.id for e in entries)]

    def one(pid: str) -> dict:
        out = justify_one(dataset, kg, pid, entries, dataset.graph, S, C, params, client, ai=True)
        if client is None:
            out["fallback_reason"] = blocked
        return out
    with ThreadPoolExecutor(max_workers=PDF_WORKERS) as pool:
        return list(pool.map(one, projects))
