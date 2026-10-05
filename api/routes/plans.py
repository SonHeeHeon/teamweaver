"""POST /api/plans/apply-swap -- 검토한 교체를 명단에 '적용'한 결과(K10).

서버는 명단을 저장하지 않는다(stateless). 이 엔드포인트는 교체 후 명단과 그 명단 전체를
현행 MILP 목적·제약으로 다시 평가한 결과(core.evaluate.plan_eval), 화면 지표(충족률·
최적화율)를 돌려줄 뿐이다. 적용 이력은 웹이 들고 PDF에 실어 보낸다. 승인·결재는 범위 밖.

교체 규칙·검증은 /api/whatif와 같은 함수(_swapped_entries)를 쓴다 -- 검토한 교체와 적용한
교체가 서로 다르게 해석될 수 없다. 위반이 있는 교체도 적용은 되며(결정은 사람이 한다),
위반 목록과 feasible=false가 그대로 돌아간다. 경고는 화면이 맡는다.
"""
from dataclasses import asdict

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_graph
from api.plan_edits import TOKEN_RE
from api.plan_token import verify_plan
from api.routes.whatif import WhatifRequest, _evaluate, _swapped_entries
from pydantic import BaseModel, Field
from typing import Annotated

from api.schemas import AllocChangeIn, ApplySwapResponse, EntryIn, MilpParamsIn, PlanEditIn, StepIn, SwapIn
from core.optimize.milp import MilpParams, _floor6
from core.graph.memory_graph import MemoryGraph
from core.optimize.metrics import _skill_relaxation_upper_bound, matching_fulfillment
from core.optimize.types import AssignEntry, PlanAssignment
from core.scoring.engine import ScoringEngine

router = APIRouter()


def _unfilled(shortfalls) -> list[str]:
    """솔버가 내는 미충원 표기(core/optimize/milp.py)와 같은 모양으로 맞춘다."""
    return [f"{s.project_id}:{s.grade}:{s.missing}명 미충원" for s in shortfalls]


def swap_warnings(before, after) -> list[str]:
    """교체로 *새로* 생긴 위반·미충원 문장. web/src/api/whatifWarnings.ts와 같은 규칙이다."""
    old_v = {(v.code, v.location) for v in before.violations}
    new_v = [v.message for v in after.violations if (v.code, v.location) not in old_v]
    old_s = {(s.project_id, s.grade): s.missing for s in before.shortfalls}
    new_s = [f"{s.project_id}의 {s.grade} {s.missing}명 미충원" for s in after.shortfalls
             if s.missing > old_s.get((s.project_id, s.grade), 0)]
    out = new_v + new_s
    if after.violations and not new_v:
        out.append("교체 전 배치에도 제약 위반이 있다")
    return out


def _upper_bound(graph: MemoryGraph, S, params, entries, ubs: dict | None) -> float:
    """최적화율 분모. 명단에 월별 항목이 있으면 월별 정식의 상한을 쓴다 -- 섞인 명단도 월별 정식의
    가능한 해이고, 월별 상한은 고정 상한 이상이라 비율이 1을 넘지 않는다(사람별 달별 조정, 2026-10-05).
    ubs는 같은 요청 안에서 방식별 상한을 한 번만 풀기 위한 캐시."""
    mode = "monthly" if (getattr(params, "allocation_mode", "fixed") == "monthly"
                         or any(e.monthly_alloc for e in entries)) else "fixed"
    if ubs is not None and mode in ubs:
        return ubs[mode]
    p = params if params.allocation_mode == mode else params.model_copy(update={"allocation_mode": mode})
    ub = _skill_relaxation_upper_bound(graph, S, p)
    if ubs is not None:
        ubs[mode] = ub
    return ub


def roster_metrics(graph: MemoryGraph, S, C, params, weights: dict, entries,
                   ubs: dict | None = None) -> dict:
    """명단 하나의 지표를 서버가 다시 계산한다(교체 없는 PDF, claude-a 교차 리뷰 S1).
    plan_eval로 목적 4항·위반·미충원을, metrics로 충족률·최적화율을 낸다."""
    roster = [AssignEntry(**e.model_dump()) for e in entries]
    ev = _evaluate(graph, S, C, params, roster)
    unfilled = _unfilled(ev.shortfalls)
    ratio = None
    if not ev.violations:
        pidx, jidx = graph.pid_index, graph.project_index
        skill_term = sum(S[pidx[e.person_id], jidx[e.project_id]] * e.alloc for e in roster)
        ub = _upper_bound(graph, S, params, roster, ubs)
        ratio = (skill_term / ub) if ub > 0 else 0.0
    plan = PlanAssignment(entries=roster, objective=ev.objective.total, unfilled=unfilled,
                          violations=[], label="report")
    return {"objective": ev.objective.total, "fulfillment": matching_fulfillment(graph, plan, weights),
            "optimization_ratio": ratio, "unfilled": unfilled,
            "violations": [v.message for v in ev.violations]}


def _changed_entries(graph: MemoryGraph, entries, change: AllocChangeIn):
    """한 사람·한 프로젝트의 투입률을 달별 값으로 바꾼 명단(전, 후). 달이 모두 같으면 일반 항목으로 정리한다."""
    jdx = graph.project_index
    if change.project_id not in jdx:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {change.project_id!r}")
    months = set(graph.projects[jdx[change.project_id]].months)
    if set(change.monthly_alloc) != months:
        raise HTTPException(status_code=422, detail=f"{change.project_id}의 진행 달 {sorted(months)}을 모두 입력해야 한다"
                                                    f"(받은 달 {sorted(change.monthly_alloc)})")
    before = [AssignEntry(**e.model_dump()) for e in entries]
    target = next((e for e in before if e.person_id == change.person_id and e.project_id == change.project_id), None)
    if target is None:
        raise HTTPException(status_code=422, detail=f"entries has no entry for {change.person_id!r} "
                                                    f"on {change.project_id!r}")
    values = {int(m): float(v) for m, v in change.monthly_alloc.items()}
    if len(set(values.values())) == 1:
        new = AssignEntry(person_id=target.person_id, project_id=target.project_id, alloc=next(iter(values.values())))
    else:
        new = AssignEntry(person_id=target.person_id, project_id=target.project_id,
                          alloc=_floor6(sum(values.values()) / len(values)), monthly_alloc=values)
    after = [new if e is target else e for e in before]
    return before, after


def apply_step(graph: MemoryGraph, S, C, params, weights: dict, entries, step,
               ubs: dict | None = None) -> dict:
    """적용 단계 하나(교체 또는 달별 투입률 조정)를 명단에 적용하고 전체를 다시 평가한다.
    /api/plans/apply-swap·apply-alloc, 저장분 재생, PDF 재계산이 같이 쓴다."""
    if isinstance(step, AllocChangeIn):
        before_entries, after_entries = _changed_entries(graph, entries, step)
    else:
        before_entries, after_entries = _swapped_entries(graph, entries, step)
    return _evaluated(graph, S, C, params, weights, before_entries, after_entries, ubs)


def apply_one(graph: MemoryGraph, S, C, params, weights: dict, entries, swap,
              ubs: dict | None = None) -> dict:
    """교체 하나를 적용하고 명단 전체를 다시 평가한다(apply_step의 교체 전용 이름, 호환용)."""
    return apply_step(graph, S, C, params, weights, entries, swap, ubs)


def _evaluated(graph, S, C, params, weights, before_entries, after_entries, ubs) -> dict:
    before = _evaluate(graph, S, C, params, before_entries)
    after = _evaluate(graph, S, C, params, after_entries)
    unfilled = _unfilled(after.shortfalls)
    feasible = not after.violations
    if feasible:
        pidx, jidx = graph.pid_index, graph.project_index
        skill_term = sum(S[pidx[e.person_id], jidx[e.project_id]] * e.alloc for e in after_entries)
        ub = _upper_bound(graph, S, params, after_entries, ubs)
        ratio = (skill_term / ub) if ub > 0 else 0.0
    else:
        # 상한(LP 완화)은 제약을 지키는 배치에만 의미가 있다. 위반 명단에 나누면 100%를
        # 넘을 수 있어 "더 좋은 배치"로 오해된다 -- 산정하지 않는다(Codex 지적).
        ratio = None
    plan = PlanAssignment(entries=after_entries, objective=after.objective.total,
                          unfilled=unfilled, violations=[], label="applied")
    return {
        "entries": [e.model_dump() for e in after_entries],
        "evaluation": {"objective": asdict(after.objective),
                       "violations": [asdict(v) for v in after.violations],
                       "shortfalls": [asdict(s) for s in after.shortfalls]},
        "objective_delta": after.objective.total - before.objective.total,
        "feasible": feasible,
        "objective": after.objective.total,
        "fulfillment": matching_fulfillment(graph, plan, weights),
        "optimization_ratio": ratio,
        "unfilled": unfilled,
        "warnings": swap_warnings(before, after),
    }


@router.post("/api/plans/apply-swap", response_model=ApplySwapResponse)
def apply_swap(req: WhatifRequest, graph: MemoryGraph = Depends(get_graph),
               dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    check_dataset_version(req.dataset_version, dataset)
    params = req.milp_params.to_milp_params()
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(req.weights), eng.synergy_matrix()
    return apply_one(graph, S, C, params, req.weights, req.entries, req.swap)


class AllocRequest(BaseModel):
    """POST /api/plans/apply-alloc 본문: 지금 명단과 한 사람의 달별 투입률 조정."""
    entries: list[EntryIn]
    change: AllocChangeIn
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: MilpParamsIn = MilpParamsIn()
    dataset_version: str | None = None


@router.post("/api/plans/apply-alloc", response_model=ApplySwapResponse)
def apply_alloc(req: AllocRequest, graph: MemoryGraph = Depends(get_graph),
                dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    """사람별 달별 투입률 조정을 명단에 적용하고 전체를 다시 평가한다. 위반이 생겨도 적용은 되며
    (결정은 사람이 한다) 위반·경고가 그대로 돌아간다 -- 교체 적용과 같은 계약."""
    check_dataset_version(req.dataset_version, dataset)
    params = req.milp_params.to_milp_params()
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(req.weights), eng.synergy_matrix()
    return apply_step(graph, S, C, params, req.weights, req.entries, req.change)


# --- K13: 적용 교체 저장·복원 ------------------------------------------------

def _edit_params(milp_params: dict | None):
    return MilpParamsIn(**milp_params).to_milp_params() if milp_params else MilpParams()


def _replay_steps(graph: MemoryGraph, record: dict) -> list[dict]:
    """저장된 교체를 원 플랜에서 순서대로 다시 적용한 단계별 결과(웹이 적용 스택을 복원한다)."""
    params = _edit_params(record.get("milp_params"))
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(record["weights"]), eng.synergy_matrix()
    ubs: dict = {}
    entries = [EntryIn(**e) for e in record["base_entries"]]
    steps = []
    for sw in record["swaps"]:
        parsed = AllocChangeIn(**sw) if sw.get("kind") == "alloc" else SwapIn(**sw)
        step = apply_step(graph, S, C, params, record["weights"], entries, parsed, ubs=ubs)
        steps.append({**step, "swap": parsed.model_dump()})
        entries = [EntryIn(**e) for e in step["entries"]]
    return steps


@router.put("/api/plans/edits/{plan_token}")
async def save_plan_edits(plan_token: str, body: PlanEditIn, request: Request,
                          graph: MemoryGraph = Depends(get_graph),
                          dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    """원 플랜 서명을 검증하고 교체를 실제로 다시 적용해 본 뒤 저장한다(빈 목록이면 삭제).
    서명이 맞지 않으면 422 -- 아무 명단에나 교체 기록을 붙일 수 없다."""
    check_dataset_version(body.dataset_version, dataset)
    params = body.milp_params.to_milp_params() if body.milp_params else MilpParams()
    if not verify_plan(plan_token, dataset.info.version, body.plan_label,
                       [e.model_dump() for e in body.base_entries], body.weights, params):
        raise HTTPException(status_code=422, detail="원 플랜 서명이 맞지 않는다.")
    if not TOKEN_RE.fullmatch(plan_token):
        raise HTTPException(status_code=422, detail="plan_token 형식이 아니다.")
    store = request.app.state.plan_edit_store
    record = {"plan_label": body.plan_label,
              "base_entries": [e.model_dump() for e in body.base_entries],
              "weights": body.weights,
              "milp_params": body.milp_params.model_dump(exclude_none=True) if body.milp_params else None,
              "dataset_version": dataset.info.version,
              "swaps": [s.model_dump() for s in body.swaps]}          # 교체·달별 조정(kind로 구분)
    if body.swaps:
        await anyio.to_thread.run_sync(_replay_steps, graph, record)     # 잘못된 교체는 404/422
    # 저장은 데이터셋 전환(업로드·되돌리기, 같은 잠금 안에서 prune)과 겹치지 않게 같은 잠금 안에서
    # 버전을 다시 확인한 뒤 한다 -- 진행 중이던 PUT이 되돌리기로 지운 인사 데이터를 다시 쓰지
    # 못하게(Codex 3차 리뷰).
    async with request.app.state.dataset_lock:
        check_dataset_version(dataset.info.version, request.app.state.dataset)
        try:
            ok, revision = await anyio.to_thread.run_sync(
                store.put, plan_token, record, body.expected_revision)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"적용 교체를 저장하지 못했다: {exc}") from exc
    if not ok:
        raise HTTPException(status_code=409, detail={
            "code": "edits_changed", "revision": revision,
            "message": "다른 화면에서 먼저 저장했다. 최신 저장분을 불러올 것."})
    return {"saved": len(body.swaps), "revision": revision}


@router.get("/api/plans/edits/{plan_token}")
async def load_plan_edits(plan_token: str, request: Request,
                          graph: MemoryGraph = Depends(get_graph),
                          dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    """저장된 교체를 지금 데이터로 다시 적용해 돌려준다. 없거나 다른 데이터셋의 것이면 빈 목록
    (200) -- "저장분 없음"은 오류가 아니다(플랜마다 조회하므로 404면 브라우저 콘솔이 오류로 찬다)."""
    if not TOKEN_RE.fullmatch(plan_token):
        raise HTTPException(status_code=422, detail="plan_token 형식이 아니다.")
    record = await anyio.to_thread.run_sync(request.app.state.plan_edit_store.get, plan_token)
    revision = request.app.state.plan_edit_store.revision_of(record)
    if record is None or record.get("dataset_version") != dataset.info.version:
        return {"swaps": [], "steps": [], "updated_at": None, "revision": 0}
    if not record.get("swaps"):
        return {"swaps": [], "steps": [], "updated_at": record.get("updated_at"), "revision": revision}
    steps = await anyio.to_thread.run_sync(_replay_steps, graph, record)
    return {"swaps": record["swaps"], "steps": steps, "updated_at": record.get("updated_at"),
            "revision": revision}
