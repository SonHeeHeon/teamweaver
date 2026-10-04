"""POST /api/report -> PDF.

async def 인 것이 중요하다: Playwright가 이 서버 자신에게 /report와 정적
에셋을 요청하므로, 핸들러가 이벤트 루프를 막으면 자기 요청을 받지 못해
교착한다.

자원 상한(K4): 본문 크기는 ReportBodyLimit 미들웨어가 JSON 파싱 전에 끊고(413),
동시 생성 수(429)와 전체 시간(504)은 여기서 건다. 브라우저 하나가 수백 MB를
쓰므로, 상한이 없으면 요청 몇 개로 서버 메모리를 다 쓸 수 있다.
"""
import asyncio
import concurrent.futures
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.pdf import (DEFAULT_MAX_BODY_BYTES, PDF_SLOTS, BrowserLaunchError, OriginUnavailable, PdfSettings,
                     render_report_pdf, resolve_internal_origin)
from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_graph
from api.plan_token import verify_plan
from api.routes.meta import build_meta
from api.routes.plans import apply_one, roster_metrics
from api.schemas import EntryIn, ReportRequest
from core.config import REPO_ROOT
from core.graph.memory_graph import MemoryGraph
from core.optimize.metrics import _skill_relaxation_upper_bound
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine

router = APIRouter()
log = logging.getLogger(__name__)

REPORT_PATH = "/api/report"
_DIST_INDEX = REPO_ROOT / "web" / "dist" / "index.html"


def _settings() -> PdfSettings:
    try:
        return PdfSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"PDF 설정 오류: {exc}") from exc


class ReportBodyLimit:
    """/api/report 요청 본문을 TEAMWEAVER_PDF_MAX_BODY_BYTES로 자른다.

    순수 ASGI 미들웨어라 FastAPI가 본문 전체를 메모리에 모아 JSON으로 파싱하기
    *전에* 끊는다. Content-Length가 있으면 바로 거절하고, 없거나(청크 전송)
    거짓이어도 receive 스트림을 세다가 넘는 순간 413을 던진다. 이 예외는
    Starlette HTTPException이라 FastAPI가 "본문 파싱 오류(400)"로 바꾸지 않고
    그대로 413 응답이 된다(fastapi/routing.py의 `except HTTPException: raise`)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # uvicorn은 scope["path"]에 root_path를 붙여 주고(`--root-path /tw` →
        # "/tw/api/report"), 라우터는 그것을 떼고 매칭한다. 같은 방식으로 떼지
        # 않으면 하위 경로 배포에서 상한이 조용히 꺼진다(리뷰 실측: 413 → 422).
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):]
        if scope["type"] != "http" or path != REPORT_PATH:
            await self.app(scope, receive, send)
            return
        try:
            limit = PdfSettings.from_env().max_body_bytes
        except ValueError:
            # 설정 오류는 라우트가 503으로 알린다. 여기서는 더 엄격한 기본값으로 막는다.
            limit = DEFAULT_MAX_BODY_BYTES
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > limit:
                    await _send_413(send, limit)
                    return

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(status_code=413, detail=_too_large(limit))
            return message

        await self.app(scope, counted, send)


def _too_large(limit: int) -> str:
    return f"요청 본문이 PDF 상한 {limit:,}바이트를 넘는다(TEAMWEAVER_PDF_MAX_BODY_BYTES)."


async def _send_413(send: Send, limit: int) -> None:
    body = json.dumps({"detail": _too_large(limit)}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})


# 교체 재계산·meta 생성을 돌리는 전용 스레드 풀(4개). 동시 계산 수는 풀 크기가 아니라 PDF 슬롯
# (기본 2, TEAMWEAVER_PDF_MAX_CONCURRENCY)이 정한다 -- 슬롯은 스레드가 실제로 끝날 때 반납한다
# (아래 report()). 파이썬 스레드는 강제로 멈출 수 없어서다(Codex 통합 리뷰). 풀은 슬롯 상한을
# 환경변수로 4까지 올려도 줄 서지 않게 넉넉히 둔다.
_PREP_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="pdf-prep")
# 서버가 브라우저에 넣는 최종 PDF 데이터(클라이언트 본문 + 재계산 + meta) 상한. 본문 상한(2MiB)은
# 클라이언트 입력만 재므로, 서버가 덧붙인 것까지 포함해 한 번 더 잰다.
MAX_RENDER_PAYLOAD_BYTES = 4 * 1024 * 1024


class PayloadTooLarge(RuntimeError):
    pass


def _report_meta(graph: MemoryGraph, version: str, payload: dict) -> dict:
    """PDF에 필요한 사람·프로젝트·협업선만 담은 meta(K9 스냅숏을 리포트 범위로 줄임).
    전체 meta는 데이터셋 크기에 비례해 커지므로(업로드 데이터에는 행 수 상한이 없다) 넣지 않는다."""
    people_ids, project_ids = set(), set()
    for e in [*payload.get("entries", []), *(payload.get("base_entries") or [])]:
        people_ids.add(e["person_id"])
        project_ids.add(e["project_id"])
    for s in [*payload.get("applied_swaps", []), *([payload["swap"]] if payload.get("swap") else [])]:
        people_ids.update((s["out_person_id"], s["in_person_id"]))
        project_ids.add(s["project_id"])
    full = build_meta(graph, version).model_dump()
    full["people"] = [p for p in full["people"] if p["id"] in people_ids]
    full["projects"] = [j for j in full["projects"] if j["id"] in project_ids]
    full["coworks"] = [c for c in full["coworks"]
                       if c["a_id"] in people_ids and c["b_id"] in people_ids]
    return full


def _prepare_payload(req: ReportRequest, graph: MemoryGraph, version: str,
                     provenance: str) -> dict:
    """전용 스레드에서 돈다: 교체 재계산(K10) + 리포트 meta(K9) + 최종 크기 검사."""
    payload = req.model_dump()
    payload.update(_replay_applied_swaps(req, graph))
    payload["plan_provenance"] = provenance
    payload["meta"] = _report_meta(graph, version, payload)
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > MAX_RENDER_PAYLOAD_BYTES:
        raise PayloadTooLarge(f"PDF 데이터가 {size:,}바이트로 상한 {MAX_RENDER_PAYLOAD_BYTES:,}바이트를 넘는다.")
    return payload


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
    params = req.milp_params.to_milp_params() if req.milp_params else MilpParams()
    eng = ScoringEngine(graph)
    S, C = eng.skill_matrix(req.weights), eng.synergy_matrix()
    if not req.applied_swaps:
        # 교체가 없어도 지표는 서버가 명단으로 다시 계산한다 -- 화면이 보낸 숫자를 그대로 찍고
        # 옆에 "서명 확인"을 붙이면 숫자까지 검증된 것으로 읽힌다(claude-a 교차 리뷰 S1).
        m = roster_metrics(graph, S, C, params, req.weights, req.entries)
        return {"objective": m["objective"], "fulfillment": m["fulfillment"],
                "optimization_ratio": m["optimization_ratio"], "unfilled": m["unfilled"],
                "applied_swaps": [], "applied_violations": m["violations"]}
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


@router.post(REPORT_PATH)
async def report(req: ReportRequest, request: Request,
                 dataset: ActiveDataset = Depends(get_dataset),
                 graph: MemoryGraph = Depends(get_graph)) -> Response:
    # 데이터셋 버전은 가장 먼저 본다 -- 원인이 더 정확하고(빌드가 없어도 409), 값싸다
    # (claude-a 교차 리뷰 S3).
    check_dataset_version(req.dataset_version, dataset)
    try:
        import playwright                           # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="playwright가 설치돼 있지 않다. `uv add --dev playwright && "
                   "uv run playwright install chromium` 후 다시 시도할 것.")

    # web/dist가 없으면 마운트도 /report 라우트도 없다(api/main.py) -- 그
    # 상태로 그냥 진행하면 Playwright가 404를 받고 wait_for_function이
    # 타임아웃을 다 태운 뒤에야 불친절한 500이 난다. 원인을 즉시 알 수 있게
    # 여기서 먼저 걸러낸다.
    if not _DIST_INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail="web/dist 없음 -- `cd web && npm run build` 먼저 실행할 것")

    settings = _settings()
    try:
        # request.base_url(= Host 헤더)이 아니라 바인드 주소/설정값만 쓴다(K4).
        origin = resolve_internal_origin(request.scope, settings.origin_override)
    except OriginUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 값싼 검사(원 플랜 서명)는 슬롯을 잡기 전에 끝낸다(K10).
    provenance = _plan_provenance(req, dataset.info.version)

    # 동시성 상한은 무거운 계산(교체 재생 + 브라우저) *전에* 건다(K4 + Codex K10 리뷰).
    if not PDF_SLOTS.try_acquire(settings.max_concurrency):
        raise HTTPException(
            status_code=429, headers={"Retry-After": "10"},
            detail=f"PDF 생성이 이미 {settings.max_concurrency}건 진행 중이다. 잠시 후 다시 시도할 것.")

    loop = asyncio.get_running_loop()
    prep: concurrent.futures.Future | None = None

    async def build_and_render() -> bytes:
        nonlocal prep
        # 교체 재계산(K10)과 리포트 meta(K9 -- 렌더 중 전환돼도 이름이 섞이지 않게 이 요청이
        # 잡은 데이터셋으로 만든다)는 CPU 작업이라 전용 스레드에서, 시간 상한 안에서 돈다.
        prep = _PREP_POOL.submit(_prepare_payload, req, graph, dataset.info.version, provenance)
        payload = await asyncio.wrap_future(prep)
        return await render_report_pdf(payload, origin, settings.timeout_s)

    try:
        # 교체 재생과 렌더를 합친 전체 시간에 상한을 건다(K4).
        pdf = await asyncio.wait_for(build_and_render(), timeout=settings.timeout_s)
    except HTTPException:
        raise                                       # 잘못된 교체(404/422)는 그대로
    except PayloadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:                        # noqa: BLE001
        # 예외 원문(Playwright call log에 내부 origin URL이 들어 있다)은 로그에만 남긴다.
        # asyncio.wait_for의 TimeoutError와 Playwright 내부 대기의 TimeoutError
        # (playwright._impl._errors.TimeoutError, 내장 TimeoutError가 아님)를
        # 같은 504로 묶는다 -- 둘 다 같은 timeout_s 상한에서 나온다.
        if not (isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"):
            log.exception("PDF 생성 실패")
            raise HTTPException(status_code=500,
                                detail=f"PDF 생성 실패({type(exc).__name__}). 서버 로그를 확인할 것.") from exc
        raise HTTPException(
            status_code=504,
            detail=f"PDF 생성이 {settings.timeout_s:g}초 안에 끝나지 않았다"
                   "(TEAMWEAVER_PDF_TIMEOUT_S).") from exc
    finally:
        if prep is not None and not prep.done():
            # 시간 초과로 응답은 끝나도 스레드는 아직 돈다 -- 끝날 때 슬롯을 반납한다.
            prep.add_done_callback(lambda _f: loop.call_soon_threadsafe(PDF_SLOTS.release))
        else:
            PDF_SLOTS.release()

    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="teamweaver-plan-{req.plan_label}.pdf"'})
