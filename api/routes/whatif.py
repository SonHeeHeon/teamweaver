"""What-if 스왑: 재계산은 MILP 재풀이가 아니라 S/C 행렬 직접 평가로 한다
(드래그&드롭은 즉시 응답해야 하고, MILP는 Plan 3 실측 기준 7초 이상 걸린다).
XAI 브리핑은 LLM structured output을 우선 시도하고, 실패하면 결정론적
fallback으로 전환한다 -- 발표 중 API 장애에도 데모가 죽지 않게 하기 위해서다."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_graph, get_openai_client_or_none, get_sqlite_conn
from api.rag.briefing import generate_briefing
from api.rag.context import swap_context
from api.rag.fallback import rule_based_briefing
from api.schemas import WhatifResponse
from core.config import load_pricing
from core.graph.memory_graph import MemoryGraph
from core.scoring.engine import ScoringEngine

router = APIRouter()


class EntryIn(BaseModel):
    person_id: str
    project_id: str
    alloc: float = Field(ge=0.0, le=1.0)


class SwapIn(BaseModel):
    out_person_id: str
    in_person_id: str
    project_id: str


class WhatifRequest(BaseModel):
    entries: list[EntryIn]
    swap: SwapIn
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}


def _objective_delta(graph: MemoryGraph, S, C, entries: list[EntryIn], swap: SwapIn) -> float:
    pdx, jdx = graph.pid_index, graph.project_index
    if swap.project_id not in jdx:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {swap.project_id!r}")
    if swap.out_person_id not in pdx:
        raise HTTPException(status_code=404,
                            detail=f"unknown out_person_id: {swap.out_person_id!r}")
    if swap.in_person_id not in pdx:
        raise HTTPException(status_code=404,
                            detail=f"unknown in_person_id: {swap.in_person_id!r}")
    j = jdx[swap.project_id]
    out_i, in_i = pdx[swap.out_person_id], pdx[swap.in_person_id]
    my_entry = next((e for e in entries if e.person_id == swap.out_person_id
                     and e.project_id == swap.project_id), None)
    if my_entry is None:
        raise HTTPException(status_code=422,
                            detail=f"entries has no entry for {swap.out_person_id!r} "
                                   f"on {swap.project_id!r}")

    skill_delta = (S[in_i, j] - S[out_i, j]) * my_entry.alloc

    teammates = [pdx[e.person_id] for e in entries
                if e.project_id == swap.project_id
                and e.person_id != swap.out_person_id
                and e.person_id != swap.in_person_id]
    synergy_delta = sum(C[in_i, t] - C[out_i, t] for t in teammates)

    return float(skill_delta + synergy_delta)


@router.post("/api/whatif", response_model=WhatifResponse)
def whatif(req: WhatifRequest, graph: MemoryGraph = Depends(get_graph),
          conn=Depends(get_sqlite_conn),
          client=Depends(get_openai_client_or_none)):
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(req.weights), eng.synergy_matrix()
    delta = _objective_delta(graph, S, C, req.entries, req.swap)

    ctx = swap_context(conn, req.swap.out_person_id, req.swap.in_person_id)
    fallback_used = False
    if client is None:
        briefing = rule_based_briefing(ctx, req.swap.out_person_id, req.swap.in_person_id)
        fallback_used = True
    else:
        try:
            model = load_pricing()["briefing_model"]
            briefing = generate_briefing(client, model, ctx, req.swap.out_person_id,
                                         req.swap.in_person_id)
        except Exception:                               # noqa: BLE001 -- LLM 장애는 데모를 죽이지 않는다
            briefing = rule_based_briefing(ctx, req.swap.out_person_id, req.swap.in_person_id)
            fallback_used = True

    return {"objective_delta": delta, "briefing": briefing, "fallback_used": fallback_used}
