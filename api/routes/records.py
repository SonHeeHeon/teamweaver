"""편성 기록 API(2026-10-18). 설계·검증 규칙: api/records.py docstring.

GET    /api/records                목록
POST   /api/records                기록 파일(JSON) 불러오기 -- 검증 후 목록에 저장(작업 데이터는 그대로)
POST   /api/records/{id}/open      열기 -- 그 기록의 데이터가 작업 데이터가 되고 편성안이 결과 캐시에 들어간다
GET    /api/records/{id}/file      내려받기
DELETE /api/records/{id}           삭제
POST   /api/records/export         지금 작업(화면이 받은 서명된 안)을 기록 파일로
"""
from __future__ import annotations

import logging
import re

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from api.admin import require_admin
from api.records import (MAX_BYTES, RecordError, RecordStore, build_and_verify, export_record, normalize_request,
                         parse_record, summary)

router = APIRouter()
log = logging.getLogger(__name__)


def store(request: Request) -> RecordStore:
    return request.app.state.record_store


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="기록 파일은 20MB까지다.")
    chunks, seen = [], 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > MAX_BYTES:
            raise HTTPException(status_code=413, detail="기록 파일은 20MB까지다.")
        chunks.append(chunk)
    return b"".join(chunks)


def _json_only(request: Request) -> None:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=415, detail="JSON 요청으로 보낸다(Content-Type: application/json).")


@router.get("/api/records")
def list_records(request: Request) -> list[dict]:
    return store(request).list()


@router.post("/api/records", dependencies=[Depends(require_admin)])
async def import_record(request: Request):
    _json_only(request)
    raw = await _read_capped(request)

    def work():
        rec = parse_record(raw)
        active, _pre, check = build_and_verify(rec)
        active.retire()                                   # 불러오기는 확인만 -- 열 때 다시 만든다
        return rec, check
    try:
        rec, check = await anyio.to_thread.run_sync(work)
        item = await anyio.to_thread.run_sync(store(request).save, rec, check)
    except RecordError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return {"record": item, "check": check}


@router.post("/api/records/{rid}/open", dependencies=[Depends(require_admin)])
async def open_record(rid: str, request: Request):
    _json_only(request)
    state = request.app.state
    try:
        rec = store(request).load(rid)
    except KeyError:
        raise HTTPException(status_code=404, detail="그런 편성 기록이 없다.") from None
    if state.dataset_switching:
        raise HTTPException(status_code=409, detail="다른 데이터 작업을 처리하는 중이다.")
    state.dataset_switching = True
    try:
        try:
            active, pre, check = await anyio.to_thread.run_sync(lambda: build_and_verify(parse_record_dict(rec)))
        except RecordError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})
        from api.demo_precomputed import install
        from api.routes.datasets import _activate, _info
        async with state.dataset_lock:
            _activate(request, active)
            install(request.app, pre)
            state.demo_active_root = None
            await anyio.to_thread.run_sync(state.plan_edit_store.prune, active.info.version)
            from api.routes.datasets import after_switch
            await anyio.to_thread.run_sync(after_switch, request.app)
            # 포인터를 먼저 쓴다 -- 실패하면 업로드 저장본은 남는다(재기동하면 그 데이터로 뜬다, 폴백 리뷰 N2)
            try:
                await anyio.to_thread.run_sync(store(request).set_active, rid)
                await anyio.to_thread.run_sync(state.dataset_store.clear)
                state.dataset_restore_error = None
            except OSError as exc:
                log.error("열어 둔 기록 포인터를 저장하지 못했다: %s", exc)
                state.dataset_restore_error = f"이 기록은 열었지만 서버에 기억하지 못했다(재기동하면 이전 데이터로 뜬다): {exc}"
        return {"dataset": _info(active, record_id=rid, record_title=rec["title"]),
                "record": summary(rec, check, id=rid), "request": normalize_request(rec["request"]),
                "operating_params": (pre.operating or {}).get("milp_params"), "check": check}
    finally:
        state.dataset_switching = False


def parse_record_dict(rec: dict) -> dict:
    """저장본도 같은 모양 검사를 거친다(손으로 고친 저장 파일)."""
    import json
    return parse_record(json.dumps(rec, ensure_ascii=False).encode("utf-8"))


def _filename(title: str) -> str:
    safe = re.sub(r"[^\w가-힣 .-]+", "_", title).strip() or "편성기록"
    return safe[:80] + ".json"


def _download(body: bytes, title: str) -> Response:
    from urllib.parse import quote
    return Response(content=body, media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename=\"record.json\"; "
                                                    f"filename*=UTF-8''{quote(_filename(title))}"})


@router.get("/api/records/{rid}/file")
def download_record(rid: str, request: Request) -> Response:
    try:
        raw = store(request).raw(rid)
        title = store(request).load(rid).get("title", "편성기록")
    except KeyError:
        raise HTTPException(status_code=404, detail="그런 편성 기록이 없다.") from None
    return _download(raw, title)


@router.delete("/api/records/{rid}", dependencies=[Depends(require_admin)])
def delete_record(rid: str, request: Request) -> dict:
    try:
        store(request).delete(rid)
    except KeyError:
        raise HTTPException(status_code=404, detail="그런 편성 기록이 없다.") from None
    except RecordError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"deleted": rid}


class ExportPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = Field(min_length=1, max_length=8)
    entries: list[dict] = Field(max_length=20_000)
    unfilled: list[str] = Field(default_factory=list, max_length=10_000)
    objective: float | None = None
    plan_token: str = Field(max_length=200)
    evidence: dict = Field(default_factory=dict)
    evidence_token: str = Field(max_length=200)


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    weights: dict[str, int] = Field(default_factory=dict)
    milp_params: dict = Field(default_factory=dict)
    n_alternatives: int = Field(default=3, ge=0, le=6)
    dataset_version: str | None = None
    plans: list[ExportPlan] = Field(min_length=1, max_length=7)
    stop_reason: str | None = Field(default=None, max_length=80)


@router.post("/api/records/export")
async def export(body: ExportIn, request: Request) -> Response:
    from api.deps import check_dataset_version
    active = request.app.state.dataset
    check_dataset_version(body.dataset_version, active)
    active.acquire()
    try:
        rec = await anyio.to_thread.run_sync(lambda: export_record(
            request.app, active, title=body.title, weights=body.weights, milp_params=body.milp_params,
            n_alternatives=body.n_alternatives, plans=[p.model_dump() for p in body.plans],
            stop_reason=body.stop_reason))
    except RecordError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    finally:
        active.release()
    import json
    return _download(json.dumps(rec, ensure_ascii=False).encode("utf-8"), body.title)
