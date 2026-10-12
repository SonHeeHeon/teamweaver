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
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.pdf import (DEFAULT_MAX_BODY_BYTES, PDF_SLOTS, BrowserLaunchError, OriginUnavailable, PdfSettings,
                     render_report_pdf, resolve_internal_origin)
from api.briefing_evidence import verify_report_evidence
from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_evidence, get_graph, get_openai_client_or_none
from api.plan_token import verify_evidence, verify_plan
from api.routes.meta import build_meta
from api.routes.plans import apply_step, roster_metrics
from api.schemas import EntryIn, ReportRequest
from core.config import REPO_ROOT
from core.graph.memory_graph import MemoryGraph
from core.optimize.alternatives import _LABELS
from core.optimize.metrics import _skill_relaxation_upper_bound
from core.optimize.milp import MilpParams
from core.scoring.engine import ScoringEngine

router = APIRouter()
log = logging.getLogger(__name__)

REPORT_PATH = "/api/report"
_ALTERNATIVE_LABELS = frozenset(_LABELS[1:])            # 안 A 다음의 대안 이름(B~G)
_DIST_INDEX = REPO_ROOT / "web" / "dist" / "index.html"


def _settings() -> PdfSettings:
    try:
        return PdfSettings.from_env()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"PDF 설정 오류: {exc}") from exc


# /api/report 밖의 JSON 쓰기 요청 본문 상한(claude-a 점검 2026-10-12 (6): 예전엔 /api/report에만 있어 /api/whatif 등에
# 큰 본문을 보내면 다 읽어 파싱했다). 업로드(/api/datasets)는 자체 상한(api/routes/datasets)이 있어 뺀다.
JSON_LIMIT_ENV = "TEAMWEAVER_MAX_JSON_BODY_BYTES"
DEFAULT_MAX_JSON_BODY_BYTES = 2 * 1024 * 1024
OWN_LIMIT_PATHS = frozenset({"/api/datasets"})
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _json_limit() -> int:
    try:
        value = int(os.environ.get(JSON_LIMIT_ENV, DEFAULT_MAX_JSON_BODY_BYTES))
    except ValueError:
        return DEFAULT_MAX_JSON_BODY_BYTES
    return value if value > 0 else DEFAULT_MAX_JSON_BODY_BYTES


def _too_large_json(limit: int) -> str:
    return f"요청 본문이 상한 {limit:,}바이트를 넘는다({JSON_LIMIT_ENV})."


def _route_path(scope: Scope) -> str:
    """라우터가 매칭에 쓰는 경로(Starlette get_route_path와 같은 규칙): root_path는 경로 구간 경계('/')에서만 뗀다.
    예전처럼 접두사만 보고 떼면 프록시가 접두사를 이미 뗀 배포(root_path "/a", path "/api/whatif")에서 "pi/whatif"가 되어
    상한을 건너뛰었다(Codex 리뷰 2026-10-12)."""
    path, root = scope.get("path", ""), scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root):] if path[len(root)] == "/" else path


class ReportBodyLimit:
    """요청 본문 크기 상한: /api/report는 TEAMWEAVER_PDF_MAX_BODY_BYTES, 그 밖의 /api JSON 쓰기 요청은
    TEAMWEAVER_MAX_JSON_BODY_BYTES(기본 2 MiB, 업로드 제외).

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
        path = _route_path(scope)
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if path == REPORT_PATH:
            try:
                limit = PdfSettings.from_env().max_body_bytes
            except ValueError:
                # 설정 오류는 라우트가 503으로 알린다. 여기서는 더 엄격한 기본값으로 막는다.
                limit = DEFAULT_MAX_BODY_BYTES
            describe = _too_large
        elif path.startswith("/api/") and path not in OWN_LIMIT_PATHS and scope.get("method") in _BODY_METHODS:
            limit, describe = _json_limit(), _too_large_json
        else:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > limit:
                    await _send_413(send, describe(limit))
                    return

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(status_code=413, detail=describe(limit))
            return message

        await self.app(scope, counted, send)


def _too_large(limit: int) -> str:
    return f"요청 본문이 PDF 상한 {limit:,}바이트를 넘는다(TEAMWEAVER_PDF_MAX_BODY_BYTES)."


async def _send_413(send: Send, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
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


def _justify_allowance(req: ReportRequest) -> float:
    """사업별 소명 글에 더 줄 시간: 배치된 사업 수 ÷ 동시 호출 수(올림) × AI 한 건 상한 + 그래프·템플릿 여유 30초."""
    import math
    from api.routes.justification import PDF_WORKERS, ai_timeout_s
    projects = {e.project_id for e in req.entries} | {e.project_id for e in (req.base_entries or [])}
    return math.ceil(len(projects) / PDF_WORKERS) * ai_timeout_s() + 30.0       # 모델별 대기 상한(사내 AI면 더 길다, 2026-10-11)


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
        if s.get("kind") == "alloc":                          # 달별 투입률 조정: 사람 한 명
            people_ids.add(s["person_id"])
        else:
            people_ids.update((s["out_person_id"], s["in_person_id"]))
        project_ids.add(s["project_id"])
    full = build_meta(graph, version).model_dump()
    full["people"] = [p for p in full["people"] if p["id"] in people_ids]
    full["projects"] = [j for j in full["projects"] if j["id"] in project_ids]
    full["coworks"] = [c for c in full["coworks"]
                       if c["a_id"] in people_ids and c["b_id"] in people_ids]
    return full


def _prepare_payload(req: ReportRequest, graph: MemoryGraph, version: str,
                     provenance: str, dataset: ActiveDataset | None = None, client=None) -> dict:
    """전용 스레드에서 돈다: 교체 재계산(K10) + 리포트 meta(K9) + (선택) 사업별 소명 글 + 최종 크기 검사."""
    payload = req.model_dump()
    payload.update(_replay_applied_swaps(req, graph))
    if provenance == "verified" and re.fullmatch(r"K\d+", req.plan_label or ""):
        # 운영 중 편성 K 결과(api/routes/operating._signed): 백지에서 전부 짠 안이 아니다 -- PDF가 그 맥락을 밝힌다(폴백 리뷰 SHOULD)
        k = req.plan_label[1:]
        payload["operating_note"] = (f"운영 중 편성 결과: 지금 배치에서 최대 {k}명만 옮겨 다시 짠 명단이다(백지에서 전부 짠 안 A~D가 아니다). "
                                     "더 많이 옮겨도 나아지지 않으면 더 적게 옮긴 결과를 그대로 쓴다. 최적화율은 백지 배치의 상한 기준이라 "
                                     "운영 중 결과에는 참고값이다.")
    if req.include_justifications and dataset is not None and not dataset.has_kg:
        payload["justifications"] = []
        payload["justifications_note"] = "인사팀 소명 글은 CSV 묶음 데이터(업로드·시연 데이터)에서만 만들 수 있다."
    elif req.include_justifications and provenance != "verified":
        # 서버가 계산한 원 플랜임을 확인한 명단에만 소명 글을 만든다(Codex 리뷰 P1, /api/justification과 같은 규칙)
        payload["justifications"] = []
        payload["justifications_note"] = "원 플랜 서명이 없어(서버 계산 명단 확인 불가) 인사팀 소명 글을 넣지 않았다."
    elif req.include_justifications and dataset is not None:
        from api.routes.justification import justify_projects
        # 최종 명단(교체를 서버가 다시 적용한 것)으로 만든다. PDF에는 글·덧붙임·사실 목록만(사실 문구는 서버가 채운 것).
        out = justify_projects(dataset, payload["entries"], req.weights, req.milp_params, client)
        payload["justifications"] = [{k: j.get(k) for k in ("project_id", "method", "text", "addendum", "fallback_reason",
                                                            "addendum_groups")}
                                     | {"facts": [{"id": f["id"], "kind": f["kind"], "text": f["text"], "adverse": f["adverse"],
                                                   # 밑줄 위치 찾기용(사용자 결정 2026-10-11: 근거 번호 대신 근거 문구)
                                                   "phrase": f.get("phrase"), "phrase_full": f.get("phrase_full")}
                                                  for f in j.get("facts", [])]}
                                     for j in out]
    payload["plan_provenance"] = provenance
    payload["solve_evidence"] = _solve_evidence(req, provenance)
    payload.pop("evidence_token", None)
    payload["meta"] = _report_meta(graph, version, payload)
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > MAX_RENDER_PAYLOAD_BYTES:
        raise PayloadTooLarge(f"PDF 데이터가 {size:,}바이트로 상한 {MAX_RENDER_PAYLOAD_BYTES:,}바이트를 넘는다.")
    return payload


def _solve_evidence(req: ReportRequest, provenance: str) -> dict | None:
    """원 플랜의 계산 신뢰도(결과 카드 배지와 같은 값) -- 원 플랜 서명과 신뢰도 서명이 모두 맞을 때만 PDF에 싣는다.
    교체·조정을 적용한 명단이면 원안 기준이라고 함께 밝힌다(화면과 같은 규칙: 변경한 명단은 솔버가 푼 해가 아니다)."""
    if req.solve_evidence is None or provenance != "verified" or not req.plan_token:
        return None
    evidence = req.solve_evidence.model_dump()
    if not req.evidence_token or not verify_evidence(req.evidence_token, req.plan_token, evidence):
        raise HTTPException(status_code=422, detail="계산 신뢰도 서명이 맞지 않는다(서버가 계산한 값이 아니다).")
    label = req.plan_label or ""
    # 대안 B~G(core/optimize/alternatives._LABELS, 최대 6개)는 다양성 조건을 더한 문제의 증명이다 -- 화면 배지와 같은 규칙
    return {**evidence, "within": "다양성 조건" if label in _ALTERNATIVE_LABELS else None,
            "changed": len(req.applied_swaps)}


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
    ubs: dict = {}                                             # 방식별 상한을 요청당 한 번
    entries, records, last = req.base_entries, [], None
    for swap in req.applied_swaps:                             # 교체·달별 투입률 조정(kind)
        last = apply_step(graph, S, C, params, req.weights, entries, swap, ubs=ubs)
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
                 graph: MemoryGraph = Depends(get_graph),
                 evidence=Depends(get_evidence), client=Depends(get_openai_client_or_none)) -> Response:
    # 데이터셋 버전은 가장 먼저 본다 -- 원인이 더 정확하고(빌드가 없어도 409), 값싸다
    # (claude-a 교차 리뷰 S3).
    check_dataset_version(req.dataset_version, dataset)
    # 화면이 보낸 근거가 "직접 인용" 배지로 찍히기 전에 서버 색인으로 확인한다(K5 연결 리뷰 S1).
    verify_report_evidence(req.briefing, evidence)
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

    # 값싼 검사(원 플랜 서명·계산 신뢰도 서명)는 슬롯을 잡기 전에 끝낸다(K10).
    provenance = _plan_provenance(req, dataset.info.version)
    _solve_evidence(req, provenance)

    # 소명 글을 넣으면 사업마다 AI 글이 더 걸린다 -- 최악(사업 수 ÷ 동시 수 × AI 상한)만큼 상한을 늘린다(Codex 리뷰 P2).
    # 모델별 상한(pricing.json)을 읽다 실패해도 슬롯을 잡기 전이라 새지 않는다(2026-10-11 폴백 리뷰).
    limit = settings.timeout_s + (_justify_allowance(req) if req.include_justifications else 0)

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
        prep = _PREP_POOL.submit(_prepare_payload, req, graph, dataset.info.version, provenance, dataset, client)
        payload = await asyncio.wrap_future(prep)
        return await render_report_pdf(payload, origin, settings.timeout_s)

    try:
        # 교체 재생과 렌더를 합친 전체 시간에 상한을 건다(K4).
        pdf = await asyncio.wait_for(build_and_render(), timeout=limit)
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
            detail=f"PDF 생성이 {limit:g}초 안에 끝나지 않았다"
                   "(TEAMWEAVER_PDF_TIMEOUT_S).") from exc
    finally:
        if prep is not None and not prep.done():
            # 시간 초과로 응답은 끝나도 스레드는 아직 돈다 -- 끝날 때 슬롯을 반납한다.
            prep.add_done_callback(lambda _f: loop.call_soon_threadsafe(PDF_SLOTS.release))
        else:
            PDF_SLOTS.release()

    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{pdf_filename(req.plan_label)}"'})


def pdf_filename(label: str | None) -> str:
    """PDF 파일 이름: 라벨에서 영문·숫자만 8자까지(없으면 plan). 서명 없는 요청은 라벨이 아무 글자나 될 수 있어,
    한글·따옴표면 PDF를 다 만든 뒤 헤더 인코딩(latin-1)에서 500이 나고 따옴표로 헤더를 깰 수 있었다(점검 2026-10-12 (5))."""
    safe = re.sub(r"[^A-Za-z0-9]", "", label or "")[:8]
    return f"teamweaver-plan-{safe or 'plan'}.pdf"
