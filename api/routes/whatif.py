"""What-if 스왑: MILP를 다시 풀지 않고, 교체 전후 배치를 현행 MILP 전체 목적과 제약으로
재평가한다(core.evaluate.plan_eval). 드래그&드롭은 즉시 응답해야 하고 MILP는 수 초 이상
걸리기 때문이다. 결과는 검토용 참고값이며 재최적화가 아니다.
XAI 브리핑은 LLM structured output을 우선 시도하고, 실패하면 결정론적
fallback으로 전환한다 -- 발표 중 API 장애에도 데모가 죽지 않게 하기 위해서다."""
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.datasets import ActiveDataset
from api.briefing_evidence import clamp_briefing
from api.deps import (check_dataset_version, get_dataset, get_evidence, get_graph,
                      get_openai_client_or_none, get_sqlite_conn, llm_client_for)
from api.rag.briefing import generate_briefing
from api.rag.context import swap_context
from api.rag.fallback import rule_based_briefing
from api.schemas import EntryIn, MilpParamsIn, SwapIn, WhatifResponse
from core.config import load_pricing
from core.evaluate.plan_eval import PlanEvaluation, evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.types import AssignEntry
from core.scoring.engine import ScoringEngine

router = APIRouter()


class WhatifRequest(BaseModel):
    entries: list[EntryIn]
    swap: SwapIn
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: MilpParamsIn = MilpParamsIn()
    dataset_version: str | None = None      # 화면이 본 데이터셋. 다르면 409(K9)


def _swapped_entries(graph: MemoryGraph, entries: list[EntryIn],
                     swap: SwapIn) -> tuple[list[AssignEntry], list[AssignEntry]]:
    pdx, jdx = graph.pid_index, graph.project_index
    if swap.project_id not in jdx:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {swap.project_id!r}")
    if swap.out_person_id not in pdx:
        raise HTTPException(status_code=404,
                            detail=f"unknown out_person_id: {swap.out_person_id!r}")
    if swap.in_person_id not in pdx:
        raise HTTPException(status_code=404,
                            detail=f"unknown in_person_id: {swap.in_person_id!r}")
    before = [AssignEntry(**e.model_dump()) for e in entries]
    out = next((e for e in before if e.person_id == swap.out_person_id
                and e.project_id == swap.project_id), None)
    if out is None:
        raise HTTPException(status_code=422,
                            detail=f"entries has no entry for {swap.out_person_id!r} "
                                   f"on {swap.project_id!r}")
    if any(e.person_id == swap.in_person_id and e.project_id == swap.project_id
           for e in before):
        raise HTTPException(status_code=422,
                            detail=f"{swap.in_person_id!r} is already on {swap.project_id!r}")
    after = [e for e in before if e is not out]
    # 들어오는 사람은 빠지는 사람의 투입률을 그대로 이어받는다(월별 투입률이면 달별 값까지).
    after.append(AssignEntry(person_id=swap.in_person_id, project_id=swap.project_id,
                             alloc=out.alloc, monthly_alloc=out.monthly_alloc))
    return before, after


def _evaluate(graph, S, C, params, entries) -> PlanEvaluation:
    try:
        # 월별 투입률(monthly_alloc)은 평가기가 달별로 검사한다(claude-a 020ee6d) -- 달·평균이 틀린 입력은 ValueError → 422.
        return evaluate_plan(graph, S, C, params, entries)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/whatif", response_model=WhatifResponse)
def whatif(req: WhatifRequest, graph: MemoryGraph = Depends(get_graph),
          conn=Depends(get_sqlite_conn), dataset: ActiveDataset = Depends(get_dataset),
          evidence=Depends(get_evidence), client=Depends(get_openai_client_or_none)):
    check_dataset_version(req.dataset_version, dataset)
    params = req.milp_params.to_milp_params()
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(req.weights), eng.synergy_matrix()
    before_entries, after_entries = _swapped_entries(graph, req.entries, req.swap)
    before = _evaluate(graph, S, C, params, before_entries)
    after = _evaluate(graph, S, C, params, after_entries)

    old_violations = {(v.code, v.location) for v in before.violations}
    new_violations = [asdict(v) for v in after.violations
                      if (v.code, v.location) not in old_violations]
    old_missing = {(s.project_id, s.grade): s.missing for s in before.shortfalls}
    new_shortfalls = [asdict(s) for s in after.shortfalls
                      if s.missing > old_missing.get((s.project_id, s.grade), 0)]

    # 근거 색인은 두 곳에 같이 넘긴다(K5): 문맥에만 넘기면 브리핑의 인용 검증이 꺼진다.
    # 교체 대상 프로젝트의 요구 기술과 점수 변화를 넘긴다(claude-a 요청 2026-10-05): 없으면 LLM이
    # "정보 부족으로 단정 어렵다"를 반복했다.
    ctx = swap_context(conn, req.swap.out_person_id, req.swap.in_person_id, evidence=evidence,
                       project_id=req.swap.project_id)
    score_change = {k: getattr(after.objective, k) - getattr(before.objective, k)
                    for k in ("skill", "synergy", "overfamiliarity", "unfilled")}
    score_change["total"] = after.objective.total - before.objective.total
    # 점수만 보면 가용률·예산을 어기면서 total만 오른 교체도 "권고"로 읽힌다 -- 위반 여부를 같이 준다.
    score_change["feasible"] = not after.violations
    if new_violations:
        score_change["new_violations"] = [v["message"] for v in new_violations][:5]
    fallback_used = False
    client, fallback_reason = llm_client_for(dataset, client)
    if client is None:
        briefing = rule_based_briefing(ctx, req.swap.out_person_id, req.swap.in_person_id)
        fallback_used = True
    else:
        try:
            model = load_pricing()["briefing_model"]
            briefing = generate_briefing(client, model, ctx, req.swap.out_person_id,
                                         req.swap.in_person_id, evidence=evidence,
                                         score_change=score_change)
        except Exception:                               # noqa: BLE001 -- LLM 장애는 데모를 죽이지 않는다
            briefing = rule_based_briefing(ctx, req.swap.out_person_id, req.swap.in_person_id)
            fallback_used = True
            fallback_reason = "llm_error"

    return {
        "objective_delta": after.objective.total - before.objective.total,
        "before": asdict(before.objective),
        "after": asdict(after.objective),
        "new_violations": new_violations,
        "new_shortfalls": new_shortfalls,
        "feasible": not after.violations,
        "briefing": clamp_briefing(briefing),
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason if fallback_used else None,
    }
