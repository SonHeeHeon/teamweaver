from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.cache import ResultCache
from api.deps import get_cache, get_graph
from api.sse import sse_event, stream_sync_generator
from core.graph.memory_graph import MemoryGraph
from core.optimize.alternatives import generate_plans_streaming
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

    async def event_stream():
        cached = cache.get(key)
        count = 0
        try:
            params = MilpParams(**req.milp_params)
            if cached is not None:
                for plan in cached:
                    count += 1
                    yield sse_event("plan", {
                        "label": plan.label,
                        "entries": [e.model_dump() for e in plan.entries],
                        "objective": plan.objective, "unfilled": plan.unfilled,
                        "index": count, "cached": True})
            else:
                collected = []
                async for plan in stream_sync_generator(
                        generate_plans_streaming, graph, S, C, params, req.n_alternatives):
                    count += 1
                    collected.append(plan)
                    yield sse_event("plan", {
                        "label": plan.label,
                        "entries": [e.model_dump() for e in plan.entries],
                        "objective": plan.objective, "unfilled": plan.unfilled,
                        "index": count, "cached": False})
                cache.put(key, collected)
            yield sse_event("done", {"count": count})
        except Exception as exc:                        # noqa: BLE001
            yield sse_event("error", {"message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
