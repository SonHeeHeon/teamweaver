"""데이터 원천 -- 인사 시스템 API 받기(기본)·CSV(대체)·진행 중 사업 기술 경력 갱신 주체(2026-10-19 사용자 요청, claude-a).

- 서버에 `TEAMWEAVER_HR_API_URL`이 있으면 API 모드: 밤 배치 첫 단계(api/nightly)와 관리자 "지금 받기"가 인사 표를 받아
  지금 작업 데이터 판에 갈아 끼우고(core.ingest.hr_api.merge) 업로드와 같은 경로로 켠다(api.workspace.commit -- 이력·되돌리기,
  검증 오류면 켜지 않고 지금 데이터 그대로). 원천이 그대로면 새 판을 만들지 않는다.
- 없으면 CSV 모드(지금처럼 데이터 탭에서 묶음 업로드). API 모드에서도 업로드는 그대로 된다(비상·시험용).
- 진행 중 사업 기술 경력 갱신 주체(manifest `skill_months_owner`, core.ingest.aging): 설정 화면에서 관리자가 바꾼다 -- 작업 데이터 판의
  새 버전으로 켠다(계산 버전이 바뀐다). 받기는 지금 판의 값을 이어 쓴다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import threading

import anyio
from fastapi import HTTPException

from core.ingest import hr_api
from core.ingest.aging import OWNERS, owner_of

log = logging.getLogger(__name__)
DIRNAME = "hr_source"
OWNER_LABEL = {"service": "TeamWeaver가 늘린다", "source": "원천 시스템이 갱신한다"}

_sync_lock = threading.Lock()                    # 한 번에 하나(밤 배치와 "지금 받기"가 겹치면 뒤의 것은 바쁨)


class SyncBusy(Exception):
    pass


class _AppRequest:
    """api.workspace.commit은 request.app만 쓴다 -- 밤 배치(요청 없음)도 같은 켜기 경로를 탄다."""

    def __init__(self, app):
        self.app = app


def _store_path():
    from api.storage import data_dir
    return data_dir() / DIRNAME / "last.json"


def last_result() -> dict | None:
    try:
        return json.loads(_store_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _save(result: dict) -> None:
    from api.storage import atomic_write
    try:
        atomic_write(_store_path(), json.dumps(result, ensure_ascii=False, indent=1).encode("utf-8"))
    except OSError:
        log.exception("인사 시스템 받기 결과를 저장하지 못했다")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def config() -> tuple[hr_api.HrApiConfig | None, str | None]:
    try:
        return hr_api.HrApiConfig.from_env(), None
    except hr_api.HrApiError as exc:
        return None, str(exc)


def _bundle(active):
    src = getattr(active, "_kg_source", None)          # (묶음, 데이터셋, 판정) -- 묶음에서 만든 데이터만
    return src[0] if src else None


def status(app) -> dict:
    from api.workspace import WorkspaceUnavailable, current_files
    cfg, cfg_error = config()
    active = app.state.dataset
    b = _bundle(active)
    editable, why = True, None
    try:
        current_files(app, active)
    except WorkspaceUnavailable as exc:
        editable, why = False, str(exc)
    api_tables = set(cfg.tables) if cfg else set()
    return {
        "mode": "api" if (cfg or cfg_error) else "csv",
        "config_error": cfg_error,
        "host": cfg.host if cfg else None,
        "tables": [{"name": n, "from": "api" if n in api_tables else "data", "service": n in hr_api.SERVICE_TABLES}
                   for n in hr_api.HR_TABLES + hr_api.SERVICE_TABLES],
        "last": last_result(),
        "running": _sync_lock.locked(),
        "editable": editable, "not_editable_reason": why,
        "skill_months_owner": owner_of(b.manifest) if b is not None else None,
        "aging": getattr(b, "aging", None) if b is not None else None,
        "as_of": (b.manifest.get("as_of") if b is not None else None),
    }


async def sync(app, *, trigger: str, client=None, today: dt.date | None = None) -> dict:
    """인사 시스템에서 받아 켠다. 반환(저장도 함): {at, trigger, status(off|fail|invalid|unchanged|applied), detail, …}.
    한 번에 하나 -- 이미 받는 중이면 SyncBusy. 받기·합치기는 바깥에서 취소해도 되지만, 켜기(_apply)는 시작하면 바깥 취소와 상관없이
    끝까지 간다(켜기·저장·이력이 어긋나지 않게, Codex 리뷰 2026-10-19) -- 잠금과 결과 저장도 _apply가 끝날 때 한다."""
    from api.workspace import WorkspaceUnavailable, current_files
    cfg, cfg_error = config()
    result: dict = {"at": _now(), "trigger": trigger, "status": "fail", "detail": "", "changed": [], "counts": {}}
    if cfg is None:
        result.update(status="off" if cfg_error is None else "fail",
                      detail=cfg_error or "인사 시스템 연결 설정이 없다(CSV 모드)")
        if cfg_error:
            _save(result)
        return result
    if not _sync_lock.acquire(blocking=False):
        raise SyncBusy("인사 시스템에서 받는 중이다")
    handed = False
    try:
        result["host"] = cfg.host
        active = app.state.dataset
        try:
            current = current_files(app, active)
        except WorkspaceUnavailable as exc:
            result["detail"] = (f"사업 정보를 담은 작업 데이터가 없어 합칠 수 없다 -- 사업·현재 배치는 이 서비스가 관리하므로 "
                                f"CSV 묶음을 먼저 올린다({exc})")
            return result
        try:
            fetched = await anyio.to_thread.run_sync(hr_api.fetch, cfg, client)
        except hr_api.HrApiError as exc:
            result["detail"] = str(exc)
            return result
        result["counts"] = {n: len(v) for n, v in fetched.items()}
        as_of = (today or dt.date.today()).strftime("%Y-%m")
        files = hr_api.merge(current, fetched, cfg, as_of=as_of)
        result["changed"] = hr_api.changed_tables(current, files)
        if files == current:
            result.update(status="unchanged", detail="원천이 그대로라 새 판을 만들지 않았다", version=active.info.version)
            return result
        if app.state.dataset is not active:
            result["detail"] = "받는 동안 데이터가 바뀌어 켜지 않았다(다음 받기에서 다시)"
            return result
        n_people = len(fetched.get("people.csv", []))
        note = (f"인사 시스템 받기({'밤 배치' if trigger == 'nightly' else '지금 받기'}, {cfg.host}): "
                f"바뀐 표 {len(result['changed'])}개" + (f" · 인력 {n_people}명" if "people.csv" in fetched else ""))
        task = asyncio.ensure_future(_apply(app, current, files, note, result, as_of=as_of))
        handed = True                                  # 이제 잠금·저장은 _apply 몫
        return await asyncio.shield(task)
    finally:
        if not handed:
            _sync_lock.release()
            _save(result)


async def _apply(app, current: dict, files: dict, note: str, result: dict, *, as_of: str) -> dict:
    from api.workspace import commit
    try:
        try:
            out = await commit(_AppRequest(app), current, files, kind="hr_sync", note=note,
                               summary={"changed": result["changed"], "as_of": as_of})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            report = detail.get("report") or {}
            errs = [f"{e.get('file')}{':' + str(e['row']) if e.get('row') else ''} {e.get('message')}"
                    for e in (report.get("errors") or [])[:5]]
            result.update(status="invalid" if exc.status_code == 422 else "fail",
                          detail=(detail.get("message") or "켜지 못했다") + (" -- " + " / ".join(errs) if errs else ""),
                          errors=len(report.get("errors") or []))
            return result
        except Exception as exc:                       # noqa: BLE001 -- 결과에 남기고 잠금은 반드시 푼다
            log.exception("인사 시스템 받기: 켜기 실패")
            result["detail"] = f"켜지 못했다: {type(exc).__name__}: {str(exc)[:200]}"
            return result
        b = _bundle(app.state.dataset)
        result.update(status="applied", version=out["dataset"]["version"], detail=note,
                      warnings=len((out.get("report") or {}).get("warnings") or []),
                      aging=getattr(b, "aging", None) if b is not None else None, persisted=out.get("persisted"),
                      persist_error=out.get("persist_error"))
        return result
    finally:
        _sync_lock.release()
        _save(result)


async def set_owner(request, owner: str) -> dict:
    """진행 중 사업 기술 경력 갱신 주체를 바꾼다 → 작업 데이터 판의 새 버전(이력·되돌리기). 같은 값이면 그대로."""
    from api.workspace import WorkspaceUnavailable, commit, current_files
    from api.workspace_edits import read_manifest, write_manifest
    if owner not in OWNERS:
        raise HTTPException(status_code=422, detail=f"owner는 {list(OWNERS)} 중 하나")
    app = request.app
    try:
        current = current_files(app, app.state.dataset)
    except WorkspaceUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    m = read_manifest(current)
    if owner_of(m) == owner:                       # 적힌 값이 없으면 기본(service) -- 실제로 쓰는 값이 같으면 새 판을 만들지 않는다
        return {"unchanged": True, "dataset": app.state.dataset.info.to_dict()}
    before = owner_of(m)
    files = dict(current)
    m["skill_months_owner"] = owner
    write_manifest(files, m)
    return await commit(request, current, files, kind="skill_months_owner",
                        note=f"진행 중 사업 기술 경력: {OWNER_LABEL[before]} → {OWNER_LABEL[owner]}",
                        summary={"from": before, "to": owner})
