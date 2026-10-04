"""데이터셋 업로드·전환(K9). 설계: api/datasets.py docstring, .omc/plan/2026-10-05-k9-dataset-upload.md.

POST /api/datasets         zip 원본 본문(application/zip) → 검증 리포트, 통과하면 전환
GET  /api/datasets/active  지금 계산에 쓰는 데이터셋
POST /api/datasets/reset   기본 fixture로 되돌림

업로드는 multipart가 아니라 원본 본문이다 -- multipart는 python-multipart 의존성이
필요한데 pyproject/uv.lock은 공유 계약이다. 업로드 데이터는 영속하지 않는다:
해제 폴더는 요청이 끝나면 지우고, SQLite는 메모리 DB이며, 재기동하면 fixture로
돌아온다(실데이터를 서버 디스크에 두지 않는 쪽이 기본). 업로드·되돌리기는
TEAMWEAVER_ADMIN_TOKEN이 있으면 관리자 토큰을 요구한다(api/admin.py).
"""
import tempfile
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from api.admin import require_admin
from api.datasets import (ActiveDataset, BundleArchiveError, build_active, bundle_version,
                          extract_bundle_zip)
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.report import IngestReport

router = APIRouter()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_ZIP_TYPES = {"application/zip", "application/x-zip-compressed", "application/octet-stream"}


def _report_dict(report: IngestReport) -> dict:
    def issue(i):
        return {"level": i.level, "file": i.file, "row": i.row, "column": i.column,
                "message": i.message}
    return {"errors": [issue(i) for i in report.errors],
            "warnings": [issue(i) for i in report.warnings],
            "notes": list(report.notes),
            "row_counts": dict(report.row_counts)}


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_too_big())
    chunks, seen = [], 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=_too_big())
        chunks.append(chunk)
    return b"".join(chunks)


def _too_big() -> str:
    return f"업로드는 {MAX_UPLOAD_BYTES // (1024 * 1024)}MiB(zip)까지다."


def _validate_and_build(data: bytes) -> tuple[ActiveDataset | None, dict | None, str | None]:
    """(활성 후보, 리포트, 묶음 단계 오류 메시지). 스레드에서 돈다(CPU·파일 작업)."""
    with tempfile.TemporaryDirectory(prefix="teamweaver-upload-") as tmp:
        try:
            root = extract_bundle_zip(data, Path(tmp) / "bundle")
        except BundleArchiveError as exc:
            return None, None, str(exc)
        bundle, report = load_bundle(root)
        try:
            ds, parsed = to_dataset(bundle, report)
        except ValueError:
            return None, _report_dict(report), None
        manifest = bundle.manifest
        synthetic = manifest.get("synthetic") if isinstance(manifest.get("synthetic"), bool) else None
        active = build_active(ds, parsed, dataset_id=str(manifest["dataset_id"]),
                              version=bundle_version(root), source="upload", synthetic=synthetic)
        return active, _report_dict(report), None


def _activate(request: Request, new: ActiveDataset) -> None:
    """한 번의 대입으로 바꿔 끼운다. 옛 데이터셋의 SQLite(메모리)는 그것을 쓰는 요청이
    모두 끝나면 닫힌다(ActiveDataset.retire)."""
    state = request.app.state
    old = state.dataset
    state.dataset = new
    old.retire()


@router.post("/api/datasets", dependencies=[Depends(require_admin)])
async def upload_dataset(request: Request):
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in _ZIP_TYPES:
        raise HTTPException(status_code=415,
                            detail="묶음은 zip 파일 하나로 보낸다(Content-Type: application/zip).")
    lock = request.app.state.dataset_lock
    if lock.locked():
        raise HTTPException(status_code=409, detail="다른 데이터셋 업로드를 처리하는 중이다.")
    async with lock:
        data = await _read_capped(request)
        active, report, archive_error = await anyio.to_thread.run_sync(
            _validate_and_build, data)
        if archive_error is not None:
            return JSONResponse(status_code=422, content={
                "activated": False, "detail": archive_error, "report": None})
        if active is None:
            return JSONResponse(status_code=422, content={
                "activated": False, "detail": "검증 오류가 있어 전환하지 않았다.", "report": report})
        _activate(request, active)
        return {"activated": True, "dataset": active.info.to_dict(), "report": report}


@router.get("/api/datasets/active")
def active_dataset(request: Request) -> dict:
    return request.app.state.dataset.info.to_dict()


@router.post("/api/datasets/reset", dependencies=[Depends(require_admin)])
async def reset_dataset(request: Request) -> dict:
    lock = request.app.state.dataset_lock
    if lock.locked():
        raise HTTPException(status_code=409, detail="다른 데이터셋 작업을 처리하는 중이다.")
    async with lock:
        fixture = await anyio.to_thread.run_sync(request.app.state.build_fixture_dataset)
        _activate(request, fixture)
        return fixture.info.to_dict()
