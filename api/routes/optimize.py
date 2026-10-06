from typing import Annotated

import anyio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.cache import ResultCache
from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_cache, get_dataset, get_graph
from api.plan_token import sign_plan
from api.schemas import MilpParamsIn
from api.sse import sse_event, stream_sync_generator
from core.graph.memory_graph import MemoryGraph
from core.optimize.alternatives import cacheable, generate_plans_streaming
from core.optimize.metrics import _skill_relaxation_upper_bound, matching_fulfillment
from core.scoring.engine import ScoringEngine

router = APIRouter()


class OptimizeRequest(BaseModel):
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: MilpParamsIn = MilpParamsIn()
    n_alternatives: int = Field(default=3, ge=0, le=6)
    # 화면이 본 데이터셋(meta.dataset_version). 다르면 409 -- api/deps.check_dataset_version
    dataset_version: str | None = None


@router.post("/api/optimize")
async def optimize(req: OptimizeRequest, graph: MemoryGraph = Depends(get_graph),
                   dataset: ActiveDataset = Depends(get_dataset),
                   cache: ResultCache = Depends(get_cache)):
    eng = ScoringEngine(graph)
    S = eng.skill_matrix(req.weights)
    C = eng.synergy_matrix()
    # 요청 본문 검증(422)은 스트림 시작 전에 끝난다 -- 잘못된 milp_params가
    # SSE error 프레임(HTTP 200)으로 숨지 않는다.
    check_dataset_version(req.dataset_version, dataset)
    params = req.milp_params.to_milp_params()
    key = ResultCache.key(req.weights, params, req.n_alternatives, dataset.info.version)

    def _plan_payload(plan, index: int, cached: bool, ub: float) -> dict:
        """캐시 히트/미스 두 경로가 같은 모양을 내도록 조립을 한 곳에 모은다."""
        pidx, jidx = graph.pid_index, graph.project_index
        skill_term = sum(S[pidx[e.person_id], jidx[e.project_id]] * e.alloc
                         for e in plan.entries)
        return {
            "label": plan.label,
            "entries": [e.model_dump() for e in plan.entries],
            "objective": plan.objective,
            "unfilled": plan.unfilled,
            "fulfillment": matching_fulfillment(graph, plan, req.weights),
            "optimization_ratio": (skill_term / ub) if ub > 0 else 0.0,
            "index": index,
            "cached": cached,
            # 솔버가 시간 한도에서 멈춘 해(최선 증명 전) -- 화면 배지(claude-a 요청)
            "time_limited": bool(getattr(plan, "time_limited", False)),
            # 계산 신뢰도 배지: 서비스가 내는 해는 모두 독립 검증(C0)을 통과한 해다. 종료 사유·증명 상한·허용 차이.
            "termination": getattr(plan, "termination", None),
            "best_bound": getattr(plan, "best_bound", None),
            "gap_allowed": getattr(plan, "gap_used", None) if getattr(plan, "gap_used", None) is not None
                           else params.gap,
            "dataset_version": dataset.info.version,
            # 서버가 이 데이터셋·가중치·파라미터로 계산한 플랜이라는 서명(PDF가 검증, K10).
            "plan_token": sign_plan(dataset.info.version, plan.label,
                                    [e.model_dump() for e in plan.entries], req.weights, params),
        }

    async def event_stream():
        cached = cache.get(key)
        count = 0
        outcome: dict = {}
        try:
            # UB는 (graph, S, params)에만 의존하고 플랜별로 달라지지 않는다 --
            # 요청당 1회만 푼다. 플랜마다 풀면 그 배수만큼 낭비다.
            # 실측(동결 fixture): 프로세스 첫 solve는 CBC 기동 비용이 섞여
            # 0.36~1.05초로 튀지만, 정상 상태는 0.08~0.10초다. 캐시 히트
            # 경로도 이 값을 다시 풀지만 그래서 체감이 0.09초 수준이다.
            ub = await anyio.to_thread.run_sync(
                lambda: _skill_relaxation_upper_bound(graph, S, params))
            if cached is not None:
                for plan in cached:
                    count += 1
                    yield sse_event("plan", _plan_payload(plan, count, True, ub))
            else:
                collected = []
                async for plan in stream_sync_generator(
                        generate_plans_streaming, graph, S, C, params, req.n_alternatives, outcome):
                    count += 1
                    collected.append(plan)
                    yield sse_event("plan", _plan_payload(plan, count, False, ub))
                # 시간 한도·검증 거절처럼 부하에 따라 달라질 수 있는 결과는 캐시하지 않는다 -- 한 번
                # 짧게 끊긴 묶음이 같은 요청에 계속 쓰이지 않게(통합 리뷰 N2, C2 리뷰).
                if cacheable(outcome):
                    cache.put(key, collected)
            # 요청한 대안 수도 싣는다: 조건(품질·미충원·중복·빈 팀, C2)을 만족하는 대안이 모자라면
            # 서버는 억지로 채우지 않고 덜 낸다 -- 화면이 "대안 없음"을 알릴 수 있게.
            done = {"count": count, "requested_alternatives": req.n_alternatives}
            if cached is None and outcome.get("stop_reason"):
                done["stop_reason"] = outcome["stop_reason"]   # 화면이 시간 초과를 조건 미충족과 구분한다
            yield sse_event("done", done)
        except Exception as exc:                        # noqa: BLE001
            yield sse_event("error", {"message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
