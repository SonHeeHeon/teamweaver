from typing import Annotated

import anyio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.cache import ResultCache
from api.deps import get_cache, get_graph
from api.sse import sse_event, stream_sync_generator
from core.graph.memory_graph import MemoryGraph
from core.optimize.alternatives import generate_plans_streaming
from core.optimize.metrics import _skill_relaxation_upper_bound, matching_fulfillment
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine

router = APIRouter()


class OptimizeRequest(BaseModel):
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: dict = {}
    n_alternatives: int = Field(default=3, ge=0, le=6)


@router.post("/api/optimize")
async def optimize(req: OptimizeRequest, graph: MemoryGraph = Depends(get_graph),
                   cache: ResultCache = Depends(get_cache)):
    eng = ScoringEngine(graph)
    S = eng.skill_matrix(req.weights)
    C = eng.synergy_matrix()
    key = ResultCache.key(req.weights, req.milp_params, req.n_alternatives)

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
        }

    async def event_stream():
        cached = cache.get(key)
        count = 0
        try:
            params = MilpParams(**req.milp_params)
            # UB는 (graph, S, params)에만 의존하고 플랜별로 달라지지 않는다 --
            # 요청당 1회만 푼다(실측 1.05초). 플랜마다 풀면 4배 낭비다.
            ub = await anyio.to_thread.run_sync(
                lambda: _skill_relaxation_upper_bound(graph, S, params))
            if cached is not None:
                for plan in cached:
                    count += 1
                    yield sse_event("plan", _plan_payload(plan, count, True, ub))
            else:
                collected = []
                async for plan in stream_sync_generator(
                        generate_plans_streaming, graph, S, C, params, req.n_alternatives):
                    count += 1
                    collected.append(plan)
                    yield sse_event("plan", _plan_payload(plan, count, False, ub))
                cache.put(key, collected)
            yield sse_event("done", {"count": count})
        except Exception as exc:                        # noqa: BLE001
            yield sse_event("error", {"message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
