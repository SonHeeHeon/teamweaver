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
from api.schemas import ApplySwapResponse, EntryIn, MilpParamsIn, PlanEditIn, SwapIn
from core.optimize.milp import MilpParams
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


def roster_metrics(graph: MemoryGraph, S, C, params, weights: dict, entries,
                   ub: float | None = None) -> dict:
    """명단 하나의 지표를 서버가 다시 계산한다(교체 없는 PDF, claude-a 교차 리뷰 S1).
    plan_eval로 목적 4항·위반·미충원을, metrics로 충족률·최적화율을 낸다."""
    roster = [AssignEntry(**e.model_dump()) for e in entries]
    ev = _evaluate(graph, S, C, params, roster)
    unfilled = _unfilled(ev.shortfalls)
    ratio = None
    if not ev.violations:
        pidx, jidx = graph.pid_index, graph.project_index
        skill_term = sum(S[pidx[e.person_id], jidx[e.project_id]] * e.alloc for e in roster)
        if ub is None:
            ub = _skill_relaxation_upper_bound(graph, S, params)
        ratio = (skill_term / ub) if ub > 0 else 0.0
    plan = PlanAssignment(entries=roster, objective=ev.objective.total, unfilled=unfilled,
                          violations=[], label="report")
    return {"objective": ev.objective.total, "fulfillment": matching_fulfillment(graph, plan, weights),
            "optimization_ratio": ratio, "unfilled": unfilled,
            "violations": [v.message for v in ev.violations]}


def apply_one(graph: MemoryGraph, S, C, params, weights: dict, entries, swap,
              ub: float | None = None) -> dict:
    """교체 하나를 적용하고 명단 전체를 다시 평가한다. /api/plans/apply-swap과 PDF(적용 이력
    재계산)가 같이 쓴다 -- 화면이 본 값과 PDF에 찍히는 값이 같은 함수에서 나온다."""
    before_entries, after_entries = _swapped_entries(graph, entries, swap)
    before = _evaluate(graph, S, C, params, before_entries)
    after = _evaluate(graph, S, C, params, after_entries)
    unfilled = _unfilled(after.shortfalls)
    feasible = not after.violations
    if feasible:
        pidx, jidx = graph.pid_index, graph.project_index
        skill_term = sum(S[pidx[e.person_id], jidx[e.project_id]] * e.alloc for e in after_entries)
        if ub is None:      # 같은 (graph, S, params)면 같은 값 -- PDF 재계산은 한 번만 풀어 넘긴다
            ub = _skill_relaxation_upper_bound(graph, S, params)
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


# --- K13: 적용 교체 저장·복원 ------------------------------------------------

def _edit_params(milp_params: dict | None):
    return MilpParamsIn(**milp_params).to_milp_params() if milp_params else MilpParams()


def _replay_steps(graph: MemoryGraph, record: dict) -> list[dict]:
    """저장된 교체를 원 플랜에서 순서대로 다시 적용한 단계별 결과(웹이 적용 스택을 복원한다)."""
    params = _edit_params(record.get("milp_params"))
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(record["weights"]), eng.synergy_matrix()
    ub = _skill_relaxation_upper_bound(graph, S, params)
    entries = [EntryIn(**e) for e in record["base_entries"]]
    steps = []
    for sw in record["swaps"]:
        step = apply_one(graph, S, C, params, record["weights"], entries, SwapIn(**sw), ub=ub)
        steps.append({**step, "swap": sw})
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
              "swaps": [s.model_dump() for s in body.swaps]}
    if body.swaps:
        await anyio.to_thread.run_sync(_replay_steps, graph, record)     # 잘못된 교체는 404/422
    try:
        # 빈 목록(취소·원래대로)도 revision과 함께 남긴다 -- 늦게 온 옛 저장이 되살리지 못하게.
        applied = await anyio.to_thread.run_sync(store.put, plan_token, record, body.revision)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"적용 교체를 저장하지 못했다: {exc}") from exc
    return {"saved": len(body.swaps), "applied": applied}


@router.get("/api/plans/edits/{plan_token}")
async def load_plan_edits(plan_token: str, request: Request,
                          graph: MemoryGraph = Depends(get_graph),
                          dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    """저장된 교체를 지금 데이터로 다시 적용해 돌려준다. 없거나 다른 데이터셋의 것이면 빈 목록
    (200) -- "저장분 없음"은 오류가 아니다(플랜마다 조회하므로 404면 브라우저 콘솔이 오류로 찬다)."""
    if not TOKEN_RE.fullmatch(plan_token):
        raise HTTPException(status_code=422, detail="plan_token 형식이 아니다.")
    record = await anyio.to_thread.run_sync(request.app.state.plan_edit_store.get, plan_token)
    if (record is None or record.get("dataset_version") != dataset.info.version
            or not record.get("swaps")):
        return {"swaps": [], "steps": [], "updated_at": None}
    steps = await anyio.to_thread.run_sync(_replay_steps, graph, record)
    return {"swaps": record["swaps"], "steps": steps, "updated_at": record.get("updated_at")}
