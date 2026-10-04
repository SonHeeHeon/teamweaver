"""POST /api/report -> PDF.

async def 인 것이 중요하다: Playwright가 이 서버 자신에게 /report와 정적
에셋을 요청하므로, 핸들러가 이벤트 루프를 막으면 자기 요청을 받지 못해
교착한다.
"""
import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_graph
from core.graph.memory_graph import MemoryGraph
from api.routes.meta import build_meta
from api.routes.plans import apply_one
from api.plan_token import verify_plan
from core.optimize.metrics import _skill_relaxation_upper_bound
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine
from api.pdf import BrowserLaunchError, render_report_pdf
from api.schemas import EntryIn, ReportRequest
from core.config import REPO_ROOT

router = APIRouter()


def _plan_provenance(req: ReportRequest, version: str) -> str:
    """원 플랜이 서버가 계산한 그 플랜인지. "verified" | "unverified"; 토큰이 틀리면 422."""
    if req.plan_token is None:
        return "unverified"
    base = req.base_entries if req.applied_swaps else req.entries
    params = req.milp_params.to_milp_params() if req.milp_params else MilpParams()
    if not verify_plan(req.plan_token, version, req.plan_label,
                       [e.model_dump() for e in base], req.weights, params):
        raise HTTPException(status_code=422,
                            detail="원 플랜 서명이 맞지 않는다(명단·가중치·설정·데이터셋 중 하나가 바뀌었다).")
    return "verified"


def _replay_applied_swaps(req: ReportRequest, graph: MemoryGraph) -> dict:
    """적용한 교체를 원 플랜 명단(base_entries)에서 서버가 다시 적용한다(K10).
    PDF에 찍히는 명단·지표·교체별 Δ·경고·최종 위반은 모두 여기서 나온다. 교체가 없으면
    아무것도 덮어쓰지 않는다(기존 PDF 계약 그대로). 잘못된 교체는 404/422로 끝난다."""
    if not req.applied_swaps:
        return {"applied_swaps": [], "applied_violations": []}
    params = req.milp_params.to_milp_params() if req.milp_params else MilpParams()
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(req.weights), eng.synergy_matrix()
    ub = _skill_relaxation_upper_bound(graph, S, params)       # 요청당 한 번
    entries, records, last = req.base_entries, [], None
    for swap in req.applied_swaps:
        last = apply_one(graph, S, C, params, req.weights, entries, swap, ub=ub)
        records.append({**swap.model_dump(), "objective_delta": last["objective_delta"],
                        "feasible": last["feasible"], "warnings": last["warnings"]})
        entries = [EntryIn(**e) for e in last["entries"]]
    return {"entries": last["entries"], "objective": last["objective"],
            "fulfillment": last["fulfillment"], "optimization_ratio": last["optimization_ratio"],
            "unfilled": last["unfilled"], "applied_swaps": records,
            "applied_violations": [v["message"] for v in last["evaluation"]["violations"]]}

_DIST_INDEX = REPO_ROOT / "web" / "dist" / "index.html"


@router.post("/api/report")
async def report(req: ReportRequest, request: Request,
                 dataset: ActiveDataset = Depends(get_dataset),
                 graph: MemoryGraph = Depends(get_graph)) -> Response:
    check_dataset_version(req.dataset_version, dataset)
    # 적용한 교체는 렌더 전에 서버가 다시 계산한다. 잘못된 교체는 여기서 404/422로 끝난다
    # (아래 렌더 오류 처리에 묶여 500이 되지 않게).
    provenance = _plan_provenance(req, dataset.info.version)
    # 재계산은 CPU 작업이다 -- 이벤트 루프를 막지 않게 워커 스레드에서 돈다.
    replayed = await anyio.to_thread.run_sync(_replay_applied_swaps, req, graph)
    replayed["plan_provenance"] = provenance
    try:
        import playwright                           # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="playwright가 설치돼 있지 않다. `uv add --dev playwright && "
                   "uv run playwright install chromium` 후 다시 시도할 것.")

    # web/dist가 없으면 마운트도 /report 라우트도 없다(api/main.py) -- 그
    # 상태로 그냥 진행하면 Playwright가 404를 받고 wait_for_function이 30초
    # 타임아웃을 다 태운 뒤에야 "Timeout 30000ms exceeded"라는 불친절한 500이
    # 난다. 원인을 즉시 알 수 있게 여기서 먼저 걸러낸다.
    if not _DIST_INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail="web/dist 없음 -- `cd web && npm run build` 먼저 실행할 것")

    base_url = str(request.base_url).rstrip("/")
    try:
        # 이름·등급·협업선을 붙일 meta를 이 요청이 잡은 데이터셋에서 만들어 함께 넣는다.
        # 페이지가 /api/meta를 따로 부르면, 렌더 중 다른 사용자의 전환으로 옛 명단에
        # 새 데이터의 이름이 붙을 수 있다(Codex 2라운드 지적, K9).
        payload = req.model_dump()
        payload["meta"] = build_meta(graph, dataset.info.version).model_dump()
        payload.update(replayed)
        pdf = await render_report_pdf(payload, base_url)
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF 생성 실패: {exc}") from exc

    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="teamweaver-plan-{req.plan_label}.pdf"'})
