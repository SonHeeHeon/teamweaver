"""POST /api/plans/apply-swap -- 검토한 교체를 명단에 '적용'한 결과(K10).

서버는 명단을 저장하지 않는다(stateless). 이 엔드포인트는 교체 후 명단과 그 명단 전체를
현행 MILP 목적·제약으로 다시 평가한 결과(core.evaluate.plan_eval), 화면 지표(충족률·
최적화율)를 돌려줄 뿐이다. 적용 이력은 웹이 들고 PDF에 실어 보낸다. 승인·결재는 범위 밖.

교체 규칙·검증은 /api/whatif와 같은 함수(_swapped_entries)를 쓴다 -- 검토한 교체와 적용한
교체가 서로 다르게 해석될 수 없다. 위반이 있는 교체도 적용은 되며(결정은 사람이 한다),
위반 목록과 feasible=false가 그대로 돌아간다. 경고는 화면이 맡는다.
"""
from dataclasses import asdict

from fastapi import APIRouter, Depends

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_graph
from api.routes.whatif import WhatifRequest, _evaluate, _swapped_entries
from api.schemas import ApplySwapResponse
from core.graph.memory_graph import MemoryGraph
from core.optimize.metrics import _skill_relaxation_upper_bound, matching_fulfillment
from core.optimize.types import PlanAssignment
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
