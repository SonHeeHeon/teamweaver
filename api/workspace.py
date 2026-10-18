"""작업 데이터의 판(version) -- 꺼내기·켜기·이력·되돌리기(2026-10-18, claude-b, 3가지 핵심 상황 설계).

- `current_files`: 지금 켜진 데이터의 원천 CSV 묶음. 편성 기록에서 연 데이터(`active.record_files`, claude-a) → 시연 묶음 폴더·zip
  → 업로드 저장본 순으로 찾는다. 셋 다 아니면(기본 fixture) 고칠 수 없다.
- `commit`: 고친 묶음을 업로드와 **같은 경로**로 켠다 -- `validate_and_build`(core.ingest 검증·변환·리뷰 판정 캐시) →
  `dataset_lock` 안에서 `_activate`·교체 기록 정리·`DatasetStore.save`(재기동해도 이 판으로 뜬다)·밤 결과 다시 넣기.
  계산 버전이 바뀌므로 캐시·서명·저장된 교체가 옛 판에 섞이지 않는다.
- 이력: `<데이터 폴더>/workspace/history.json`(최근 20판: 계산 버전·원천 버전·시각·종류·메모·요약)과
  `<데이터 폴더>/workspace/versions/<원천 버전>.zip`. 마지막 항목이 지금 판이다. 되돌리기 = 직전 판 zip을 다시 켜고 마지막 항목을 지운다.
  실데이터라면 판 zip에도 인사 자료가 들어간다 -- 업로드 저장본과 같은 비공개 폴더, 다른 데이터로 바뀌면 지운다.
"""
from __future__ import annotations

import io
import json
import logging
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import anyio
from fastapi import HTTPException, Request

from api.storage import atomic_write, data_dir, ensure_private_dir
from api.workspace_edits import Files

log = logging.getLogger(__name__)
MAX_HISTORY = 20
_BUNDLE_FILES = None


class WorkspaceUnavailable(Exception):
    """지금 데이터는 원천 CSV 묶음이 아니다(기본 fixture) -- 고칠 수 없다."""


def _allowed() -> set[str]:
    global _BUNDLE_FILES
    if _BUNDLE_FILES is None:
        from api.datasets import _ALLOWED
        _BUNDLE_FILES = set(_ALLOWED)
    return _BUNDLE_FILES


def _read_dir(root: Path) -> Files:
    # 원문 그대로(줄바꿈 포함) 읽는다 -- read_text는 CRLF를 LF로 바꿔, 시작 판 보관본이 manifest 해시와 어긋나 되돌리기가 실패했다
    # (Codex 리뷰 2026-10-19)
    return {p.name: p.read_bytes().decode("utf-8") for p in sorted(root.iterdir())
            if p.is_file() and p.name in _allowed()}


def _read_zip(data: bytes) -> Files:
    from api.datasets import extract_bundle_zip
    with tempfile.TemporaryDirectory(prefix="teamweaver-workspace-") as tmp:
        return _read_dir(extract_bundle_zip(data, Path(tmp) / "bundle"))


def current_files(app, active) -> Files:
    """지금 켜진 데이터의 원천 묶음(파일 이름 → 원문). 버전별로 한 번만 읽어 둔다."""
    cached = getattr(app.state, "workspace_files", None)
    if cached and cached[0] == active.info.version:
        return dict(cached[1])
    files = getattr(active, "record_files", None)
    if files:
        files = {k: v for k, v in dict(files).items() if k in _allowed()}
    elif active.info.source == "demo-bundle" and getattr(app.state, "demo_active_root", None):
        root = Path(app.state.demo_active_root)
        files = _read_zip(root.read_bytes()) if root.suffix.lower() == ".zip" else _read_dir(root)
    elif active.info.source == "upload":
        saved = app.state.dataset_store.load()
        if saved is None or saved[0].get("version") != active.info.content_version:
            raise WorkspaceUnavailable("지금 업로드 데이터의 저장본이 없다(다시 업로드할 것)")
        files = _read_zip(saved[1])
    else:
        raise WorkspaceUnavailable("기본 데이터는 고칠 수 없다 -- CSV 묶음(업로드·편성 기록·시연 묶음)을 연다")
    if "manifest.json" not in files:
        raise WorkspaceUnavailable("원천 묶음에 manifest.json이 없다")
    if "mapping.json" in files:
        # 열 이름을 바꿔 쓰는 묶음은 편집기가 표준 열 이름으로 읽지 못한다 -- 고치지 않고 이유를 알린다(Codex 리뷰 2026-10-19)
        raise WorkspaceUnavailable("열 이름 매핑(mapping.json)을 쓰는 묶음은 화면에서 고칠 수 없다 -- 표준 열 이름으로 다시 올린다")
    app.state.workspace_files = (active.info.version, dict(files))
    return dict(files)


def zip_files(files: Files) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name].encode("utf-8"))
    return buf.getvalue()


# --- 이력 -----------------------------------------------------------------------------------------------------------

class History:
    def __init__(self, root: Path | None = None):
        self.dir = (root if root is not None else data_dir()) / "workspace"

    @property
    def path(self) -> Path:
        return self.dir / "history.json"

    def entries(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def _write(self, entries: list[dict]) -> None:
        ensure_private_dir(self.dir)
        atomic_write(self.path, json.dumps(entries[-MAX_HISTORY:], ensure_ascii=False, indent=1).encode("utf-8"))
        keep = {e.get("content_version") for e in entries[-MAX_HISTORY:]}
        for z in (self.dir / "versions").glob("*.zip") if (self.dir / "versions").exists() else []:
            if z.stem not in keep:
                z.unlink(missing_ok=True)

    def save_zip(self, content_version: str, data: bytes) -> None:
        ensure_private_dir(self.dir / "versions")
        atomic_write(self.dir / "versions" / f"{content_version}.zip", data)

    def load_zip(self, content_version: str) -> bytes | None:
        p = self.dir / "versions" / f"{content_version}.zip"
        return p.read_bytes() if p.is_file() else None

    def append(self, entry: dict) -> None:
        entries = self.entries()
        self._write(entries + [entry])

    def ensure_base(self, info, data: bytes) -> None:
        """첫 편집 전 지금 판을 '시작 데이터'로 남긴다(되돌릴 곳). 이력의 마지막 판이 지금 데이터가 아니면 새로 시작한다."""
        entries = self.entries()
        if entries and entries[-1].get("version") == info.version:
            return
        self._write([])
        self.save_zip(info.content_version, data)
        self.append({"version": info.version, "content_version": info.content_version, "at": _now(),
                     "kind": "base", "note": f"시작 데이터({info.dataset_id})", "summary": {}})

    def pop(self) -> None:
        entries = self.entries()
        self._write(entries[:-1])

    def clear(self) -> None:
        self._write([])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- 켜기 -----------------------------------------------------------------------------------------------------------

async def _activate_blob(request: Request, data: bytes, check=None, prepare=None):
    """업로드와 같은 경로로 검증·변환해 켠다. 검증 오류면 422(리포트 포함).
    check(후보 데이터셋): 켜기 전에 부른다(확정의 새 위반 검사 등) -- HTTPException을 올리면 켜지 않는다."""
    from api.routes.datasets import _activate, validate_and_build
    state = request.app.state
    active, report, archive_error = await anyio.to_thread.run_sync(validate_and_build, data)
    if archive_error is not None or active is None:
        raise HTTPException(status_code=422, detail={"message": archive_error or "고친 데이터가 검증을 통과하지 못했다.",
                                                     "report": report})
    try:
        if check is not None:
            await anyio.to_thread.run_sync(check, active)
        if prepare is not None:
            # 이력(판 zip·항목)은 켜기 **전에** 쓴다 -- 켠 뒤에 쓰다 실패하면 새 판은 켜졌는데 되돌리기가 끊겼다(Codex 리뷰 2026-10-19)
            await anyio.to_thread.run_sync(prepare, active)
    except BaseException:
        active.retire()
        raise
    async with state.dataset_lock:
        _activate(request, active)
        try:
            from api.demo_precomputed import install
            install(request.app, None)                  # 시연 미리 계산은 원본 시연 묶음 것 -- 고친 판에는 쓰지 않는다
        except ImportError:                             # claude-a 개편으로 없어졌으면 건너뛴다
            pass
        state.demo_active_root = None
        await anyio.to_thread.run_sync(state.plan_edit_store.prune, active.info.version)
        persist_error = None
        try:
            await anyio.to_thread.run_sync(state.dataset_store.save, data, active.info)
        except OSError as exc:
            log.error("작업 데이터 저장 실패: %s", exc)
            persist_error = f"서버에 저장하지 못했다(재기동하면 이전 데이터로 뜬다): {exc}"
        if persist_error is None:
            # 편성 기록에서 벗어났다 -- 재기동은 "열어 둔 기록 → 업로드 저장본" 순이라 지우지 않으면 고친 판 대신 옛 기록으로 뜬다.
            # 새 저장본이 확정된 **뒤에만** 지운다: 저장이 실패했는데 지우면 복원할 곳이 둘 다 없어진다(Codex 리뷰 2026-10-19).
            # 이미 새 판을 켰으므로 지우기 실패는 요청을 실패시키지 않고 알리기만 한다.
            record_store = getattr(state, "record_store", None)
            if record_store is not None:
                try:
                    await anyio.to_thread.run_sync(record_store.clear_active)
                except OSError as exc:
                    log.error("열어 둔 편성 기록 표시를 지우지 못했다: %s", exc)
                    persist_error = f"열어 둔 편성 기록 표시를 지우지 못했다(재기동하면 그 기록으로 뜬다): {exc}"
        state.dataset_restore_error = None
        nightly = getattr(state, "nightly", None)
        if nightly is not None:
            try:
                await anyio.to_thread.run_sync(nightly.install_prepared)
            except Exception:                           # noqa: BLE001 -- 밤 결과는 없어도 된다
                log.warning("밤 결과를 다시 넣지 못했다", exc_info=True)
    return active, report, persist_error


def _begin(request: Request) -> None:
    state = request.app.state
    if state.dataset_switching:
        raise HTTPException(status_code=409, detail="다른 데이터 변경을 처리하는 중이다. 잠시 뒤 다시 한다.")
    state.dataset_switching = True


def _end(request: Request) -> None:
    request.app.state.dataset_switching = False


async def commit(request: Request, before_files: Files, files: Files, *, kind: str, note: str,
                 summary: dict | None = None, check=None) -> dict:
    """고친 묶음을 새 판으로 켜고 이력에 남긴다. 반환: {dataset(정보), report, persisted, persist_error}."""
    state = request.app.state
    _begin(request)
    try:
        prev = state.dataset
        hist = History()
        await anyio.to_thread.run_sync(hist.ensure_base, prev.info, zip_files(before_files))
        data = zip_files(files)

        def prepare(active) -> None:
            hist.save_zip(active.info.content_version, data)
            hist.append({"version": active.info.version, "content_version": active.info.content_version, "at": _now(),
                         "kind": kind, "note": note[:300], "summary": summary or {}})
        active, report, persist_error = await _activate_blob(request, data, check, prepare)
        state.workspace_files = (active.info.version, dict(files))
        return {"dataset": active.info.to_dict(), "report": report, "persisted": persist_error is None,
                "persist_error": persist_error}
    finally:
        _end(request)


async def undo(request: Request) -> dict:
    """직전 판으로 되돌린다. 되돌릴 판이 없으면 409."""
    state = request.app.state
    hist = History()
    entries = hist.entries()
    if len(entries) < 2 or entries[-1].get("version") != state.dataset.info.version:
        raise HTTPException(status_code=409, detail="되돌릴 판이 없다(지금 데이터에서 고친 이력이 없다).")
    target = entries[-2]
    data = hist.load_zip(str(target.get("content_version")))
    if data is None:
        raise HTTPException(status_code=409, detail="직전 판의 원천 묶음이 남아 있지 않다.")
    _begin(request)
    try:
        active, report, persist_error = await _activate_blob(request, data, prepare=lambda _a: hist.pop())
        state.workspace_files = (active.info.version, _read_zip(data))
        return {"dataset": active.info.to_dict(), "report": report, "persisted": persist_error is None,
                "persist_error": persist_error, "undone": entries[-1]}
    finally:
        _end(request)
