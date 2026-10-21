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
                      get_openai_client_or_none, get_sqlite_conn, llm_client_for, SQLITE_LOCK)
from api.rag.briefing import generate_briefing
from api.rag.context import swap_context
from api.rag.fallback import rule_based_briefing
from api.schemas import MAX_PLAN_ENTRIES, EntryIn, MilpParamsIn, SwapIn, WhatifResponse
from core.config import load_pricing
from core.evaluate.plan_eval import PlanEvaluation, evaluate_plan
from core.graph.memory_graph import MemoryGraph
from core.optimize.types import AssignEntry
from core.scoring.engine import ScoringEngine

router = APIRouter()


class WhatifRequest(BaseModel):
    entries: list[EntryIn] = Field(max_length=MAX_PLAN_ENTRIES)     # 개수 상한(점검 2026-10-12 (6)) -- 본문 상한과 별개로
    swap: SwapIn
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: MilpParamsIn = MilpParamsIn()
    dataset_version: str | None = None      # 화면이 본 데이터셋. 다르면 409(K9)
    # What-if(지금 배치, 2026-10-21): 지금 배치 → 이 명단을 확정할 때와 같은 등급 대체(사유를 받을 것)·예산 증액을 적용해 평가한다.
    # 안 A~D 다듬기(연간 계획 ②)는 끈 채로 둔다 -- 거기서는 등급이 바뀐 교체가 지금처럼 경고로 보인다.
    grade_swap: bool = False


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
    src = swap.in_from_project_id
    if src is None and swap.exchange:
        raise HTTPException(status_code=422, detail="맞바꾸기(exchange)는 데려올 사업(in_from_project_id)이 있어야 한다")
    if src is not None:
        # 다른 사업에서 데려오기(2026-10-20): 그 사업에서 뺀다 -- 빈자리는 평가기가 센다(new_shortfalls)
        if src not in jdx:
            raise HTTPException(status_code=404, detail=f"unknown in_from_project_id: {src!r}")
        if src == swap.project_id:
            raise HTTPException(status_code=422, detail="같은 사업 안에서는 데려올 수 없다")
        moved = next((e for e in after if e.person_id == swap.in_person_id and e.project_id == src), None)
        if moved is None:
            raise HTTPException(status_code=422, detail=f"{swap.in_person_id!r} is not on {src!r}")
        after = [e for e in after if e is not moved]
        if swap.exchange:
            if any(e.person_id == swap.out_person_id and e.project_id == src for e in after):
                raise HTTPException(status_code=422, detail=f"{swap.out_person_id!r} is already on {src!r}")
            after.append(AssignEntry(person_id=swap.out_person_id, project_id=src,
                                     alloc=moved.alloc, monthly_alloc=moved.monthly_alloc))
    # 들어오는 사람은 빠지는 사람의 투입률을 그대로 이어받는다(월별 투입률이면 달별 값까지).
    after.append(AssignEntry(person_id=swap.in_person_id, project_id=swap.project_id,
                             alloc=out.alloc, monthly_alloc=out.monthly_alloc))
    return before, after


def source_loss_notes(swap: SwapIn, new_shortfalls: list[dict]) -> list[str]:
    """데려오기면 원래 사업이 잃는 것을 정해진 문장으로(AI 설명이 빼먹어도 서버가 붙인다 -- 소명 덧붙임과 같은 방식)."""
    src = swap.in_from_project_id
    if src is None:
        return []
    gaps = [f"{s['grade']} {s['missing']}명" for s in new_shortfalls if s["project_id"] == src]
    if swap.exchange:
        return [f"맞바꾸기: {swap.out_person_id}가 {src}로 가고 {swap.in_person_id}가 {swap.project_id}로 온다"
                + (f" -- {src}에 {', '.join(gaps)} 빈자리가 남는다." if gaps else ".")]
    return [f"{swap.in_person_id}를 {src}에서 데려오면 {src}에 " + (", ".join(gaps) + " 빈자리가 생긴다." if gaps
                                                                  else "자리가 하나 빈다(정원 밖 인원이라 빈자리로 세지는 않는다).")]


def _evaluate(graph, S, C, params, entries) -> PlanEvaluation:
    try:
        # 월별 투입률(monthly_alloc)은 평가기가 달별로 검사한다(claude-a 020ee6d) -- 달·평균이 틀린 입력은 ValueError → 422.
        return evaluate_plan(graph, S, C, params, entries)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _named(briefing: dict, dataset) -> dict:
    """가상 데이터면 설명 글(명분·위험·대안) 속 사람 ID를 이름으로(claude-a 요청 2026-10-11 (2), 소명 글과 같은 display_names).
    AI·인용 가드는 ID로 검증하므로 브리핑 모듈은 ID 그대로 두고, 보여 줄 때만 바꾼다. 근거 출처 표시 [rv:…]와 근거 목록은 그대로."""
    from api.routes.justification import display_names, with_names
    names = display_names(dataset)
    if not names:
        return briefing
    return {**briefing, "rationale": with_names(briefing.get("rationale"), names),
            "risks": [with_names(r, names) for r in briefing.get("risks", [])],
            "alternatives": [with_names(a, names) for a in briefing.get("alternatives", [])]}


@router.post("/api/whatif", response_model=WhatifResponse)
def whatif(req: WhatifRequest, graph: MemoryGraph = Depends(get_graph),
          conn=Depends(get_sqlite_conn), dataset: ActiveDataset = Depends(get_dataset),
          evidence=Depends(get_evidence), client=Depends(get_openai_client_or_none)):
    check_dataset_version(req.dataset_version, dataset)
    params = req.milp_params.to_milp_params()
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(req.weights), eng.synergy_matrix()
    before_entries, after_entries = _swapped_entries(graph, req.entries, req.swap)
    g_before, g_after, swaps, budget = substituted(graph, dataset, req, before_entries, after_entries)
    before = _evaluate(g_before, S, C, params, before_entries)
    after = _evaluate(g_after, S, C, params, after_entries)

    old_violations = {(v.code, v.location) for v in before.violations}
    new_violations = [asdict(v) for v in after.violations
                      if (v.code, v.location) not in old_violations]
    old_missing = {(s.project_id, s.grade): s.missing for s in before.shortfalls}
    new_shortfalls = [asdict(s) for s in after.shortfalls
                      if s.missing > old_missing.get((s.project_id, s.grade), 0)]

    # 근거 색인은 두 곳에 같이 넘긴다(K5): 문맥에만 넘기면 브리핑의 인용 검증이 꺼진다.
    # 교체 대상 프로젝트의 요구 기술과 점수 변화를 넘긴다(claude-a 요청 2026-10-05): 없으면 LLM이
    # "정보 부족으로 단정 어렵다"를 반복했다.
    with SQLITE_LOCK:                                   # 공유 연결 -- 동시 요청이 겹치면 500(api.deps.SQLITE_LOCK)
        ctx = swap_context(conn, req.swap.out_person_id, req.swap.in_person_id, evidence=evidence,
                           project_id=req.swap.project_id)
    # AI 설명에 넘기는 값은 소수 둘째 자리로(화면 친절하게 2026-10-20 -- 그대로 넘기면 글에 -101.79133333333334가 나왔다)
    score_change = {k: round(getattr(after.objective, k) - getattr(before.objective, k), 2)
                    for k in ("skill", "synergy", "overfamiliarity", "unfilled")}
    score_change["total"] = round(after.objective.total - before.objective.total, 2)
    # 점수만 보면 가용률·예산을 어기면서 total만 오른 교체도 "권고"로 읽힌다 -- 위반 여부를 같이 준다.
    score_change["feasible"] = not after.violations
    if new_violations:
        score_change["new_violations"] = [v["message"] for v in new_violations][:5]
    fallback_used = False
    client, fallback_reason = llm_client_for(dataset, client)
    if client is None:
        # 위반 교체면 규칙 설명도 결론을 "보류"로 쓴다 -- 넘기지 않으면 "뚜렷한 리스크가 관측되지 않는다"가 나와
        # 화면의 위반 경고와 어긋났다(claude-a 요청 2026-10-12 (1))
        briefing = rule_based_briefing(ctx, req.swap.out_person_id, req.swap.in_person_id, score_change=score_change)
        fallback_used = True
    else:
        try:
            model = load_pricing()["briefing_model"]
            briefing = generate_briefing(client, model, ctx, req.swap.out_person_id,
                                         req.swap.in_person_id, evidence=evidence,
                                         score_change=score_change)
        except Exception:                               # noqa: BLE001 -- LLM 장애는 데모를 죽이지 않는다
            briefing = rule_based_briefing(ctx, req.swap.out_person_id, req.swap.in_person_id,
                                           score_change=score_change)
            fallback_used = True
            fallback_reason = "llm_error"

    return {
        "objective_delta": after.objective.total - before.objective.total,
        "before": asdict(before.objective),
        "after": asdict(after.objective),
        "new_violations": new_violations,
        "new_shortfalls": new_shortfalls,
        "feasible": not after.violations,
        "briefing": _named(clamp_briefing({**briefing, "risks": grade_swap_notes(swaps) + source_loss_notes(req.swap, new_shortfalls)
                                                       + list(briefing.get("risks") or [])}), dataset),
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason if fallback_used else None,
        "grade_swaps": swaps, "budget_add": budget,
    }


def substituted(graph, dataset, req: WhatifRequest, before_entries, after_entries):
    """grade_swap이면 지금 배치 대비 등급 대체·예산 증액을 적용한 (교체 전 그래프, 교체 뒤 그래프, 뒤의 대체, 뒤의 증액) -- 확정 미리 계산과 같은
    `core.evaluate.grade_swap.substitution`. 아니면 원래 그래프 그대로."""
    if not req.grade_swap:
        return graph, graph, {}, {}
    from core.evaluate.grade_swap import substitution
    current = [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in dataset.current]
    g_before = substitution(graph, current, before_entries)[0]
    g_after, swaps, budget = substitution(graph, current, after_entries)
    return g_before, g_after, swaps, budget


def grade_swap_notes(swaps: dict) -> list[str]:
    """등급 대체를 위험 항목 맨 앞에 정해진 문장으로(AI 지시문은 바꾸지 않는다 -- source_loss_notes와 같은 방식)."""
    return [f"등급 대체 — {jid}의 {s['seat']} 자리 {s['count']}개를 {s['grade']}으로 채운다(확정할 때 사유를 적고 그 사업 정원이 바뀐다)"
            for jid, xs in swaps.items() for s in xs]
