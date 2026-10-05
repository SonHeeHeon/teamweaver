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
import logging
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
log = logging.getLogger(__name__)

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


def validate_and_build(data: bytes) -> tuple[ActiveDataset | None, dict | None, str | None]:
    """(활성 후보, 리포트, 묶음 단계 오류 메시지). 스레드에서 돈다(CPU·파일 작업)."""
    with tempfile.TemporaryDirectory(prefix="teamweaver-upload-") as tmp:
        try:
            root = extract_bundle_zip(data, Path(tmp) / "bundle")
        except BundleArchiveError as exc:
            return None, None, str(exc)
        bundle, report = load_bundle(root)
        try:
            ds, parsed = to_dataset(bundle, report)
        except ValueError as exc:
            # 변환 중 예외가 리포트에 오류를 남기지 않은 경우(예: 모델 검증 실패)에도
            # "오류 0건인데 전환 안 됨"이 되지 않게 예외 문장을 싣는다(claude-a 교차 리뷰 S2).
            if not report.errors:
                report.error("변환", str(exc)[:2000])
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
    state = request.app.state
    # "전환 진행 중"은 잠금이 아니라 별도 표시로 본다 -- 적용 교체 저장이 잠깐 잡는 잠금 때문에
    # 업로드가 거짓 409를 받지 않게(Opus 검증 S2). 확인과 표시 사이에 await가 없어 원자적이다.
    if state.dataset_switching:
        raise HTTPException(status_code=409, detail="다른 데이터셋 업로드를 처리하는 중이다.")
    state.dataset_switching = True
    try:
        async with state.dataset_lock:
            data = await _read_capped(request)
            active, report, archive_error = await anyio.to_thread.run_sync(
                validate_and_build, data)
            if archive_error is not None:
                return JSONResponse(status_code=422, content={
                    "activated": False, "detail": archive_error, "report": None})
            if active is None:
                return JSONResponse(status_code=422, content={
                    "activated": False, "detail": "검증 오류가 있어 전환하지 않았다.", "report": report})
            _activate(request, active)
            # 다른 데이터셋의 교체 기록은 지운다(이전 업로드의 사번·명단이 남지 않게, Opus 리뷰 S2).
            await anyio.to_thread.run_sync(request.app.state.plan_edit_store.prune, active.info.version)
            # 재기동 후에도 이 데이터로 뜨도록 저장한다(K13). 저장에 실패해도 전환은 유효하다 --
            # 대신 "재기동하면 사라진다"는 사실을 응답에 싣는다.
            persist_error = None
            try:
                await anyio.to_thread.run_sync(request.app.state.dataset_store.save, data, active.info)
            except OSError as exc:
                log.error("업로드 데이터 저장 실패: %s", exc)
                persist_error = (f"서버에 저장하지 못했다(재기동하면 이전에 저장된 데이터, 없으면 기본 "
                                 f"데이터로 뜬다): {exc}")
            request.app.state.dataset_restore_error = None
            return {"activated": True, "dataset": active.info.to_dict(), "report": report,
                    "persisted": persist_error is None, "persist_error": persist_error}
    finally:
        state.dataset_switching = False


@router.get("/api/datasets/active")
def active_dataset(request: Request) -> dict:
    """restore_error: 부팅 때 저장된 업로드 데이터를 복원하지 못해 기본 데이터로 떴으면 그 이유."""
    return {**request.app.state.dataset.info.to_dict(),
            "restore_error": getattr(request.app.state, "dataset_restore_error", None)}


@router.post("/api/datasets/reset", dependencies=[Depends(require_admin)])
async def reset_dataset(request: Request) -> dict:
    # JSON 요청만 받는다: 본문 없는 단순 POST는 교차 사이트 폼으로도 보낼 수 있어(CORS 사전
    # 요청 없음), 다른 웹페이지가 저장된 업로드 데이터를 지울 수 있었다(claude-a 교차 리뷰 L1).
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=415, detail="되돌리기는 JSON 요청으로 보낸다(Content-Type: application/json).")
    state = request.app.state
    if state.dataset_switching:
        raise HTTPException(status_code=409, detail="다른 데이터셋 작업을 처리하는 중이다.")
    state.dataset_switching = True
    try:
        async with state.dataset_lock:
            fixture = await anyio.to_thread.run_sync(request.app.state.build_fixture_dataset)
            _activate(request, fixture)
            await anyio.to_thread.run_sync(request.app.state.dataset_store.clear)
            await anyio.to_thread.run_sync(request.app.state.plan_edit_store.prune, fixture.info.version)
            # 시연 묶음을 못 읽어 예전 fixture로 되돌아갔으면 그 이유를 알린다(claude-a 요청 -- 조용히 바뀌지 않게).
            err = getattr(request.app.state, "demo_bundle_error", None)
            request.app.state.dataset_restore_error = err
            return {**fixture.info.to_dict(), "restore_error": err}
    finally:
        state.dataset_switching = False
