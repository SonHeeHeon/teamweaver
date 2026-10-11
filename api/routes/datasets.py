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
from pydantic import BaseModel, ConfigDict

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
    """(활성 후보, 리포트, 묶음 단계 오류 메시지). 스레드에서 돈다(CPU·파일 작업, 리뷰 글 LLM 판정)."""
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
                              version=bundle_version(root), source="upload", synthetic=synthetic,
                              manifest=manifest, bundle=bundle)
        return active, _report_dict(report), None


def rebuild_active(app) -> tuple[ActiveDataset | None, str | None]:
    """지금 활성 데이터셋을 같은 원천으로 다시 만든다(LLM 판정 다시 시도, 스레드에서 돈다).

    업로드 데이터는 서버에 저장된 묶음에서 다시 읽는다. 저장본이 없으면(저장 실패) 다시 만들 수 없다 --
    (None, 이유)를 돌려주고 지금 데이터를 그대로 둔다."""
    info = app.state.dataset.info
    if info.source != "upload":
        return app.state.build_fixture_dataset(), None
    try:
        saved = app.state.dataset_store.load()
    except ValueError as exc:
        return None, f"저장된 업로드 묶음을 읽지 못해 다시 만들지 못했다: {exc}"
    if saved is None or saved[0].get("version") != info.content_version:
        return None, "지금 업로드 데이터의 저장본이 없어 다시 만들지 못했다(다시 업로드할 것)."
    active, _report, err = validate_and_build(saved[1])
    if active is None:
        return None, f"저장된 업로드 묶음을 다시 만들지 못했다: {err or '검증 오류'}"
    return active, None


def _info(active: ActiveDataset, **extra) -> dict:
    """전환 응답의 데이터셋 정보. 다음 업로드의 글이 갈 곳(judge_endpoint)도 싣는다 -- 화면 안내가 사라지지 않게."""
    from api.review_judge import endpoint
    # scenario: 운영 중 시연 묶음("operating")인지 -- 화면이 '전부 다시 짜기'에 빈자리 안내를 붙인다(claude-a 리허설 요청)
    return {**active.info.to_dict(), **extra, "judge_endpoint": endpoint(),
            "scenario": active.scenario.get("scenario") or "planning"}


def _activate(request: Request, new: ActiveDataset) -> None:
    """한 번의 대입으로 바꿔 끼운다. 옛 데이터셋의 SQLite(메모리)는 그것을 쓰는 요청이
    모두 끝나면 닫힌다(ActiveDataset.retire)."""
    state = request.app.state
    old = state.dataset
    state.dataset = new
    old.retire()
    # 판정 캐시는 지금 데이터의 판정만 둔다. 기본 가상 데이터(fixture)로 돌아가면 이전 데이터의 판정(글 해시·값)을
    # 지운다. 판정에 실패한 데이터("items")는 지우지 않는다 -- 받은 만큼은 남아 있어야 "판정 다시 시도"가 남은 건만
    # 부른다(리뷰 M1). judge_reviews가 이미 지금 데이터의 판정만 남겼다.
    # 판정하지 않고 끝난 실데이터("blocked")일 때도 이전 실데이터의 판정을 남기지 않는다(리뷰 2라운드 NIT).
    if new.info.review_judge in ("fixture", "blocked"):
        from api.datasets import judge_cache_path
        from api.review_judge import clear_cache
        clear_cache(judge_cache_path())


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
        # 검증·변환·리뷰 글 LLM 판정(수 분 걸릴 수 있다)은 잠금 밖에서 한다 -- 적용 교체 저장이 그동안 멈추지 않게
        # (리뷰 S1). 다른 전환은 dataset_switching 표시가 막는다. 바꿔 끼우기·정리만 잠금 안에서 한다.
        data = await _read_capped(request)
        active, report, archive_error = await anyio.to_thread.run_sync(validate_and_build, data)
        if archive_error is not None:
            return JSONResponse(status_code=422, content={
                "activated": False, "detail": archive_error, "report": None})
        if active is None:
            return JSONResponse(status_code=422, content={
                "activated": False, "detail": "검증 오류가 있어 전환하지 않았다.", "report": report})
        async with state.dataset_lock:
            _activate(request, active)
            from api.demo_precomputed import install
            install(request.app, None)                  # 미리 계산은 시연 묶음만(2026-10-07 claude-a)
            state.demo_active_root = None
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
            return {"activated": True, "dataset": _info(active), "report": report,
                    "persisted": persist_error is None, "persist_error": persist_error}
    finally:
        state.dataset_switching = False


@router.get("/api/datasets/active")
def active_dataset(request: Request) -> dict:
    """restore_error: 부팅 때 저장된 업로드 데이터를 복원하지 못해 기본 데이터로 떴으면 그 이유."""
    # judge_endpoint: 다음에 올릴 묶음의 평가 사유(글)를 어디로 보내 판정하는가(화면 업로드 안내용)
    # demo_name: 지금 켜진 시연 묶음 이름(GET /api/datasets/demos의 name, 아니면 None).
    # precomputed: 시연 묶음의 미리 계산 결과를 쓰는지(쓰지 않은 부분은 skipped에 이유) -- 시연 전 점검용(2026-10-07 claude-a)
    from api.demos import active_demo_name
    return _info(request.app.state.dataset,
                 restore_error=getattr(request.app.state, "dataset_restore_error", None),
                 demo_name=active_demo_name(request.app),
                 precomputed=getattr(request.app.state, "precomputed_status", None))


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
        # "기본 데이터로 되돌리기"는 고른 시연 묶음도 잊는다(서버 기본 = TEAMWEAVER_DEMO_BUNDLE 또는 고정 fixture).
        from api.demos import clear_choice
        from api.storage import data_dir
        await anyio.to_thread.run_sync(clear_choice, data_dir())
        fixture = await anyio.to_thread.run_sync(request.app.state.build_fixture_dataset)   # 잠금 밖(시연 묶음은 LLM 판정)
        from api.demo_precomputed import install, prepare_for
        from api.demos import current_demo_root
        root = current_demo_root() if fixture.info.source == "demo-bundle" else None
        pre = await anyio.to_thread.run_sync(prepare_for, request.app, fixture, root)
        async with state.dataset_lock:
            _activate(request, fixture)
            install(request.app, pre)
            state.demo_active_root = root
            await anyio.to_thread.run_sync(request.app.state.dataset_store.clear)
            await anyio.to_thread.run_sync(request.app.state.plan_edit_store.prune, fixture.info.version)
            # 시연 묶음을 못 읽어 예전 fixture로 되돌아갔으면 그 이유를 알린다(claude-a 요청 -- 조용히 바뀌지 않게).
            err = getattr(request.app.state, "demo_bundle_error", None)
            request.app.state.dataset_restore_error = err
            from api.demos import active_demo_name
            return _info(fixture, restore_error=err, demo_name=active_demo_name(request.app),
                         precomputed=state.precomputed_status)
    finally:
        state.dataset_switching = False


@router.post("/api/datasets/rejudge", dependencies=[Depends(require_admin)])
async def rejudge_dataset(request: Request) -> dict:
    """LLM 판정에 실패해 항목 점수로 만든 데이터셋을 같은 원천으로 다시 만들어 판정을 다시 시도한다."""
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=415, detail="다시 판정은 JSON 요청으로 보낸다(Content-Type: application/json).")
    state = request.app.state
    if state.dataset_switching:
        raise HTTPException(status_code=409, detail="다른 데이터셋 작업을 처리하는 중이다.")
    state.dataset_switching = True
    try:
        new, err = await anyio.to_thread.run_sync(rebuild_active, request.app)              # 잠금 밖(LLM 판정)
        if new is None:
            raise HTTPException(status_code=409, detail=err)
        from api.demo_precomputed import install, prepare_for
        from api.demos import current_demo_root
        root = current_demo_root() if new.info.source == "demo-bundle" else None
        pre = await anyio.to_thread.run_sync(prepare_for, request.app, new, root)
        async with state.dataset_lock:
            _activate(request, new)
            install(request.app, pre)
            state.demo_active_root = root
            # 판정이 성공하면 버전이 바뀐다 -- 옛 버전의 교체 기록(사번·명단)을 남기지 않는다(리뷰 S6).
            await anyio.to_thread.run_sync(state.plan_edit_store.prune, new.info.version)
            if new.info.source != "upload":
                # 시연 묶음을 못 읽어 예전 fixture가 됐으면 조용히 바뀌지 않게 이유를 알린다(리뷰 S6).
                state.dataset_restore_error = getattr(state, "demo_bundle_error", None)
            from api.demos import active_demo_name
            return _info(new, restore_error=getattr(state, "dataset_restore_error", None),
                         demo_name=active_demo_name(request.app), precomputed=state.precomputed_status)
    finally:
        state.dataset_switching = False


@router.get("/api/datasets/demos")
def list_demo_datasets() -> list[dict]:
    """데이터 탭에서 고를 수 있는 시연 묶음(demo/ 아래 manifest가 있는 폴더). 운영 중 시나리오는 scenario="operating"."""
    from api.demos import list_demos
    return list_demos()


class DemoChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


@router.post("/api/datasets/demo", dependencies=[Depends(require_admin)])
async def choose_demo_dataset(body: DemoChoice, request: Request) -> dict:
    """시연 묶음으로 전환한다. 저장된 업로드 보관본은 지운다("기본 데이터로 되돌리기"와 같은 정리). 고른 이름은 기억해
    재기동해도 그 묶음으로 뜬다. 이름은 목록(api.demos.list_demos)에 있는 것만 받는다."""
    from api.demos import demo_root, save_choice
    from api.storage import data_dir
    root = demo_root(body.name)
    if root is None:
        raise HTTPException(status_code=404, detail=f"시연 데이터 묶음 {body.name!r}이 없다.")
    state = request.app.state
    if state.dataset_switching:
        raise HTTPException(status_code=409, detail="다른 데이터셋 작업을 처리하는 중이다.")
    state.dataset_switching = True
    try:
        # 고른 묶음을 먼저 만든다(잠금 밖, 리뷰 글 판정). 실패하면 아무것도 바꾸지 않는다 -- 업로드 보관본·선택 기록은
        # 그대로다(리뷰 S3: 예전에는 고정 fixture로 대체하고도 보관본을 지우고 실패한 선택을 기억했다).
        try:
            new = await anyio.to_thread.run_sync(state.build_demo_dataset, root)
        except Exception as exc:                                   # noqa: BLE001 -- 검증 실패 등 이유를 그대로 알린다
            raise HTTPException(status_code=422, detail=f"시연 데이터 묶음을 읽지 못했다: {str(exc)[:300]}") from exc
        # 같은 데이터·같은 설정으로 미리 계산한 결과(시연 확장 E, api/demo_precomputed) -- 잠금 밖에서 검증
        from api.demo_precomputed import install, prepare_for
        pre = await anyio.to_thread.run_sync(prepare_for, request.app, new, root)
        try:
            await anyio.to_thread.run_sync(save_choice, data_dir(), body.name)
        except OSError as exc:
            new.retire()                                           # 만든 데이터셋(메모리 SQLite)을 닫고 아무것도 바꾸지 않는다
            raise HTTPException(status_code=500, detail=f"시연 데이터 선택을 저장하지 못했다: {exc}") from exc
        async with state.dataset_lock:
            _activate(request, new)
            install(request.app, pre)
            state.demo_active_root = root
            await anyio.to_thread.run_sync(state.dataset_store.clear)
            await anyio.to_thread.run_sync(state.plan_edit_store.prune, new.info.version)
            state.demo_bundle_error = None
            state.dataset_restore_error = None
            return _info(new, restore_error=None, demo_name=body.name, precomputed=state.precomputed_status)
    finally:
        state.dataset_switching = False
