import os
import threading
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.cache import ResultCache
from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_cache, get_dataset, get_graph
from api.plan_token import evidence_of, sign_evidence, sign_plan
from api.schemas import MilpParamsIn
from api import runs
from api.sse import CancellableStreamResponse, SyncStream, sse_event
from core.graph.memory_graph import MemoryGraph
from core.optimize.alternatives import cacheable, generate_plans_streaming
from core.optimize.metrics import _skill_relaxation_upper_bound, matching_fulfillment
from core.scoring.engine import ScoringEngine

router = APIRouter()

# 새로 푸는 최적화(캐시·미리 계산이 아닌 것)를 동시에 몇 건까지 받나. 넘치면 기다리지 않고 429 -- 예전엔 익명 요청 40개가
# 스레드풀을 묶어 /api/meta가 9초 넘게 막혔고 끊어도 끝까지 풀었다(claude-a 점검 2026-10-12 (3)). 한 건이 시드 수만큼 프로세스를 쓴다.
SLOTS_ENV = "TEAMWEAVER_OPTIMIZE_SLOTS"
DEFAULT_SLOTS = 2


def solve_slots(app) -> threading.BoundedSemaphore:
    """앱 하나에 하나(처음 쓸 때 만든다). 슬롯은 풀이 스레드가 끝날 때 놓는다 -- 화면이 끊겨도(운영 비교와 같은 원칙)."""
    sem = getattr(app.state, "optimize_slots", None)
    if sem is None:
        try:
            n = max(1, int(os.environ.get(SLOTS_ENV, DEFAULT_SLOTS)))
        except ValueError:
            n = DEFAULT_SLOTS
        sem = app.state.optimize_slots = threading.BoundedSemaphore(n)
    return sem


class OptimizeRequest(BaseModel):
    weights: dict[str, Annotated[int, Field(ge=1, le=5)]] = {}
    milp_params: MilpParamsIn = MilpParamsIn()
    n_alternatives: int = Field(default=3, ge=0, le=6)
    # 화면이 본 데이터셋(meta.dataset_version). 다르면 409 -- api/deps.check_dataset_version
    dataset_version: str | None = None
    # 캐시(부팅 사전계산·시연 묶음의 미리 계산 결과 포함)를 쓰지 않고 다시 푼다 -- 화면의 "다시 계산"(2026-10-06)
    fresh: bool = False


@router.post("/api/optimize")
async def optimize(req: OptimizeRequest, request: Request, graph: MemoryGraph = Depends(get_graph),
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

    from api.demo_precomputed import forget, precomputed_at, precomputed_stop_reason

    def _plan_payload(plan, index: int, cached: bool, ub: float, pre_at: str | None = None) -> dict:
        """캐시 히트/미스 두 경로가 같은 모양을 내도록 조립을 한 곳에 모은다."""
        pidx, jidx = graph.pid_index, graph.project_index
        skill_term = sum(S[pidx[e.person_id], jidx[e.project_id]] * e.alloc
                         for e in plan.entries)
        out = {
            "label": plan.label,
            "entries": [e.model_dump() for e in plan.entries],
            "objective": plan.objective,
            "unfilled": plan.unfilled,
            "fulfillment": matching_fulfillment(graph, plan, req.weights),
            "optimization_ratio": (skill_term / ub) if ub > 0 else 0.0,
            "index": index,
            "cached": cached,
            # 시연 묶음의 미리 계산 결과면 그 계산 시각(같은 데이터·같은 설정, api/demo_precomputed) -- 화면 표시용
            "precomputed_at": pre_at,
            # 솔버가 시간 한도에서 멈춘 해(최선 증명 전) -- 화면 배지(claude-a 요청)
            "time_limited": bool(getattr(plan, "time_limited", False)),
            # 계산 신뢰도 배지: 서비스가 내는 해는 모두 독립 검증(C0)을 통과한 해다. 종료 사유·증명 상한·허용 차이.
            "termination": getattr(plan, "termination", None),
            "best_bound": getattr(plan, "best_bound", None),
            "gap_allowed": getattr(plan, "gap_used", None) if getattr(plan, "gap_used", None) is not None
                           else params.gap,
            "dataset_version": dataset.info.version,
        }
        # 서버가 이 데이터셋·가중치·파라미터로 계산한 플랜이라는 서명(PDF가 검증, K10).
        out["plan_token"] = sign_plan(dataset.info.version, plan.label,
                                      [e.model_dump() for e in plan.entries], req.weights, params)
        # 계산 신뢰도(배지)도 서명한다 -- PDF가 꾸며지지 않은 값만 찍게(2026-10-12). 화면은 evidence를 그대로 돌려보낸다.
        out["evidence"] = evidence_of(out)
        out["evidence_token"] = sign_evidence(out["plan_token"], out["evidence"])
        return out

    cached = None if req.fresh else cache.get(key)
    pre_at = precomputed_at(request.app, key) if cached is not None else None
    outcome: dict = {}
    solving: SyncStream | None = None
    run_id: str | None = None
    if cached is None:
        slots = solve_slots(request.app)
        if not slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="다른 최적화 계산이 진행 중이다. 끝난 뒤 다시 실행한다.")
        # 풀이는 응답을 돌려주기 전에 시작한다 -- 슬롯은 언제나 풀이 스레드가 놓는다(스트림을 읽든 말든).
        # 화면이 끊으면(abort) 지금 푸는 HiGHS까지 끊고, "계산 멈추기"(finish, POST /api/runs/stop)면 그때까지의 해를 내고 끝낸다.
        app = request.app

        def finished() -> None:
            slots.release()
            runs.unregister(app, run_id)
        solving = SyncStream(generate_plans_streaming, graph, S, C, params, req.n_alternatives, outcome,
                             on_exit=finished, start=False)
        run_id = runs.register(app, solving.stop)
        solving.start()

    def user_stopped() -> bool:
        return solving is not None and solving.stop.mode == "finish"

    async def event_stream():
        count = 0
        try:
            if run_id is not None:
                yield sse_event("start", {"run_id": run_id})   # 화면의 "계산 멈추기"가 쓴다
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
                    yield sse_event("plan", _plan_payload(plan, count, True, ub, pre_at))
            else:
                collected = []
                async for plan in solving.items():
                    count += 1
                    collected.append(plan)
                    yield sse_event("plan", _plan_payload(plan, count, False, ub))
                # 시간 한도·검증 거절처럼 부하에 따라 달라질 수 있는 결과는 캐시하지 않는다 -- 한 번
                # 짧게 끊긴 묶음이 같은 요청에 계속 쓰이지 않게(통합 리뷰 N2, C2 리뷰). 멈춘 묶음도 마찬가지.
                if cacheable(outcome) and not user_stopped():
                    cache.put(key, collected)
                    forget(request.app, key)
            # 요청한 대안 수도 싣는다: 조건(품질·미충원·중복·빈 팀, C2)을 만족하는 대안이 모자라면
            # 서버는 억지로 채우지 않고 덜 낸다 -- 화면이 "대안 없음"을 알릴 수 있게.
            done = {"count": count, "requested_alternatives": req.n_alternatives, "precomputed_at": pre_at}
            if user_stopped():
                done["stop_reason"] = "user_stopped"            # 사용자가 멈췄다 -- 대안이 모자란 이유
            elif cached is None and outcome.get("stop_reason"):
                done["stop_reason"] = outcome["stop_reason"]   # 화면이 시간 초과를 조건 미충족과 구분한다
            elif pre_at is not None and precomputed_stop_reason(request.app, key):   # 시각이 빈 미리 계산도 이유를 싣는다
                done["stop_reason"] = precomputed_stop_reason(request.app, key)   # 미리 계산 때의 이유
            yield sse_event("done", done)
        except Exception as exc:                        # noqa: BLE001
            if user_stopped():
                # 멈춘 뒤의 실패(해를 찾기 전에 멈춤·다음 풀이를 시작하지 않음)는 오류가 아니라 멈춤이다
                yield sse_event("done", {"count": count, "requested_alternatives": req.n_alternatives,
                                         "precomputed_at": None, "stop_reason": "user_stopped"})
            else:
                yield sse_event("error", {"message": str(exc)})
        finally:
            if solving is not None:
                solving.cancel("abort")                 # 끊겼거나 UB 계산이 실패했다 -- 남은 풀이를 버린다

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if solving is None:
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)
    return CancellableStreamResponse(event_stream(), on_close=lambda: solving.cancel("abort"),
                                     media_type="text/event-stream", headers=headers)


class StopRequest(BaseModel):
    run_id: str = Field(min_length=8, max_length=64)


@router.post("/api/runs/stop")
async def stop_run(req: StopRequest, request: Request) -> dict:
    """진행 중인 계산을 멈춘다(그때까지의 해를 쓰고 끝냄). 이미 끝났거나 없는 번호면 404."""
    if not runs.request_stop(request.app, req.run_id):
        raise HTTPException(status_code=404, detail="이미 끝났거나 없는 계산이다.")
    return {"stopping": True, "run_id": req.run_id}
