"""활성 데이터셋(K9): 서버가 지금 계산에 쓰는 graph·SQLite·식별 정보 묶음.

app.state.dataset 하나를 통째로 바꿔 끼운다 -- graph만 새것이고 SQLite는 옛것인
중간 상태가 없게 한다. 요청은 시작할 때 이 객체 하나를 잡고 끝까지 쓴다
(api/deps.py::get_dataset을 FastAPI가 요청당 한 번만 푼다).

업로드 묶음은 zip 하나다. 검증·변환은 claude-a의 core.ingest(load_bundle·to_dataset)가
하고, 여기서는 zip을 안전하게 풀고(경로 탈출·링크·폭탄·이상 파일 거부) 버전을 매긴다.
"""
import hashlib
import io
import json
import re
import sqlite3
import stat
import struct
import tempfile
import threading
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from core.domain.models import Dataset
from core.graph.memory_graph import MemoryGraph
from core.graph.sqlite_store import build_sqlite
from core.ingest.loader import FILE_SPECS

MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 64
_ALLOWED = set(FILE_SPECS) | {"manifest.json", "mapping.json"}
_JUNK_DIRS = {"__MACOSX"}
_JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}


class BundleArchiveError(ValueError):
    """zip 자체가 묶음으로 받을 수 없는 모양이다(검증 리포트 이전 단계)."""


@dataclass(frozen=True)
class DatasetInfo:
    dataset_id: str
    version: str                    # 내용 sha256 -- 캐시 키에 들어간다
    source: str                     # "fixture" | "upload"
    synthetic: bool | None          # manifest의 synthetic. fixture는 True(가상 데이터)
    people: int
    projects: int
    activated_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class ActiveDataset:
    """graph·SQLite·info 한 묶음. SQLite는 **메모리 DB**다(실데이터를 디스크에 두지 않는다).

    전환으로 물러난 데이터셋은 그것을 쓰는 요청이 모두 끝나는 즉시 연결을 닫는다
    (deps.get_dataset이 acquire/release). 쓰는 요청이 없으면 retire 때 바로 닫는다."""

    def __init__(self, graph: MemoryGraph, sqlite_conn: sqlite3.Connection, info: DatasetInfo):
        self.graph = graph
        self.sqlite_conn = sqlite_conn
        self.info = info
        self._lock = threading.Lock()       # whatif 등 동기 라우트는 스레드풀에서 돈다
        self._users = 0
        self._retired = False
        self.closed = False

    def acquire(self) -> None:
        """닫힌 데이터셋은 등록을 거부한다(닫힌 SQLite를 요청에 넘기지 않는다)."""
        with self._lock:
            if self.closed:
                raise RuntimeError("이미 닫힌 데이터셋이다")
            self._users += 1

    def release(self) -> None:
        with self._lock:
            self._users -= 1
            self._close_if_idle()

    def retire(self) -> None:
        with self._lock:
            self._retired = True
            self._close_if_idle()

    def _close_if_idle(self) -> None:
        if self._retired and self._users <= 0 and not self.closed:
            self.sqlite_conn.close()
            self.closed = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dir_version(root: Path, names: list[str] | None = None) -> str:
    """폴더 안 파일들의 (이름, 내용)으로 sha256을 만든다. 이름 순서로 고정."""
    h = hashlib.sha256()
    for p in sorted(root.iterdir(), key=lambda p: p.name):
        if not p.is_file() or (names is not None and p.name not in names):
            continue
        data = p.read_bytes()
        h.update(p.name.encode("utf-8") + b"\0" + str(len(data)).encode() + b"\0" + data)
    return h.hexdigest()


def bundle_version(root: Path) -> str:
    return dir_version(root, sorted(_ALLOWED))


def build_active(ds: Dataset, parsed: list, *, dataset_id: str, version: str,
                 source: str, synthetic: bool | None) -> ActiveDataset:
    graph = MemoryGraph.build(ds, parsed)
    # build_sqlite(공유 계약 core/graph)는 파일 경로를 받는다. 임시 파일에 만든 뒤
    # 메모리 DB로 복사하고 파일은 바로 지운다 -- 이름·리뷰가 든 DB가 디스크에 남지 않게.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    with tempfile.TemporaryDirectory(prefix="teamweaver-db-") as tmp:
        db_path = Path(tmp) / "build.db"
        build_sqlite(ds, parsed, db_path)
        src = sqlite3.connect(db_path)
        try:
            src.backup(conn)
        finally:
            src.close()
    info = DatasetInfo(dataset_id=dataset_id, version=version, source=source,
                       synthetic=synthetic, people=len(ds.people), projects=len(ds.projects),
                       activated_at=_now())
    return ActiveDataset(graph=graph, sqlite_conn=conn, info=info)


def _strip_common_folder(names: list[PurePosixPath]) -> list[PurePosixPath]:
    """모든 파일이 같은 최상위 폴더 하나 안에 있으면 그 폴더를 벗긴다(탐색기 '압축하기')."""
    if names and all(len(n.parts) == 2 for n in names) and len({n.parts[0] for n in names}) == 1:
        return [PurePosixPath(n.parts[1]) for n in names]
    return names


def _check_central_directory(data: bytes, limit: int) -> None:
    """zipfile이 중앙 디렉터리 전체를 ZipInfo로 만들기 *전에* 실제 레코드를 센다.

    끝 레코드(EOCD)의 항목 수는 위조할 수 있고 zipfile은 그 숫자가 아니라 중앙 디렉터리
    바이트 전체를 훑는다(Codex 2라운드 실측). 그래서 zipfile과 같은 범위
    [EOCD 위치 - 중앙 디렉터리 크기, EOCD 위치)를 레코드 단위로 걸으며 세고, 상한을
    넘는 순간 멈춘다 -- 빈 디렉터리 항목 수십만 개짜리 zip도 상한만큼만 읽는다.
    묶음은 작으므로 zip64는 받지 않는다."""
    tail_start = max(0, len(data) - (65535 + 22))
    pos = data.rfind(b"PK\x05\x06", tail_start)
    if pos < 0 or len(data) - pos < 22:
        raise BundleArchiveError("zip 파일이 아니다(끝 레코드가 없다)")
    total, cd_size, cd_offset = struct.unpack("<HII", data[pos + 10:pos + 20])
    if total == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        raise BundleArchiveError("zip64 형식은 받지 않는다(묶음은 작아야 한다)")
    start = pos - cd_size
    if start < 0:
        raise BundleArchiveError("zip 파일이 손상됐다(중앙 디렉터리 범위가 맞지 않는다)")
    cur, count = start, 0
    while cur < pos:
        if data[cur:cur + 4] != b"PK\x01\x02" or cur + 46 > pos:
            raise BundleArchiveError("zip 파일이 손상됐다(중앙 디렉터리 레코드가 깨졌다)")
        n, m, k = struct.unpack("<HHH", data[cur + 28:cur + 34])
        cur += 46 + n + m + k
        count += 1
        if count > limit:
            raise BundleArchiveError(f"zip 항목이 너무 많다(최대 {MAX_ENTRIES}개 파일)")
    if cur != pos:
        raise BundleArchiveError("zip 파일이 손상됐다(중앙 디렉터리 크기가 맞지 않는다)")


def extract_bundle_zip(data: bytes, dest: Path) -> Path:
    """zip을 dest에 풀고 묶음 루트(=dest)를 돌려준다. 문제가 있으면 BundleArchiveError.

    파일을 쓰기 전에 목록 전체를 먼저 검사한다 -- 절반만 풀린 폴더를 남기지 않는다."""
    # 디렉터리 항목도 센다(최상위 폴더 하나 + 잡폴더 몇 개 여유).
    _check_central_directory(data, MAX_ENTRIES * 2)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise BundleArchiveError(f"zip 파일이 아니다: {exc}") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise BundleArchiveError(f"zip 항목이 너무 많다({len(infos)}개, 최대 {MAX_ENTRIES})")
        kept: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
                raise BundleArchiveError(f"허용되지 않는 경로: {info.filename!r}")
            if path.parts[0] in _JUNK_DIRS or path.name in _JUNK_FILES:
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise BundleArchiveError(f"심볼릭 링크는 받을 수 없다: {info.filename!r}")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise BundleArchiveError(
                    f"풀면 {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)}MiB를 넘는다(압축 폭탄 의심)")
            kept.append((info, path))
        if not kept:
            raise BundleArchiveError("zip이 비어 있다(묶음 파일이 없다)")
        stripped = _strip_common_folder([p for _, p in kept])
        for (info, _), rel in zip(kept, stripped):
            if len(rel.parts) != 1:
                raise BundleArchiveError(
                    f"묶음 파일은 zip 루트나 최상위 폴더 하나 안에 있어야 한다: {info.filename!r}")
            if rel.name not in _ALLOWED:
                raise BundleArchiveError(
                    f"묶음에 없는 파일: {info.filename!r} (허용: {', '.join(sorted(_ALLOWED))})")
        if len({r.name for r in stripped}) != len(stripped):
            raise BundleArchiveError("같은 이름의 파일이 두 번 들어 있다")
        dest.mkdir(parents=True, exist_ok=True)
        written = 0
        for (info, _), rel in zip(kept, stripped):
            # 선언된 크기를 믿지 않고 실제로 읽은 양으로 다시 센다(헤더 조작 대비).
            with zf.open(info) as src:
                body = src.read(MAX_UNCOMPRESSED_BYTES - written + 1)
            written += len(body)
            if written > MAX_UNCOMPRESSED_BYTES:
                raise BundleArchiveError(
                    f"풀면 {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)}MiB를 넘는다(압축 폭탄 의심)")
            (dest / rel.name).write_bytes(body)
    return dest


class DatasetStore:
    """업로드한 묶음의 영속(K13, 사용자 지시로 K9의 '디스크에 두지 않음'을 바꿈).

    검증을 통과한 zip 원본을 `<data>/datasets/<version>.zip`(0600)에, 활성 포인터를
    `active.json`에 원자적으로 쓴다. 새로 저장하면 이전 zip은 지운다 -- 실제 인사 자료 사본을
    하나만 둔다. 부팅 때 api.main이 포인터를 읽어 같은 검증·변환을 다시 거쳐 복원한다."""

    def __init__(self, root: Path):
        self.dir = root / "datasets"

    @property
    def pointer(self) -> Path:
        return self.dir / "active.json"

    def save(self, data: bytes, info: DatasetInfo) -> None:
        from api.storage import atomic_write, ensure_private_dir
        ensure_private_dir(self.dir)
        zip_path = self.dir / f"{info.version}.zip"
        atomic_write(zip_path, data)
        try:
            atomic_write(self.pointer, json.dumps({
                "version": info.version, "dataset_id": info.dataset_id,
                "activated_at": info.activated_at}, ensure_ascii=False).encode("utf-8"))
        except OSError:
            # 포인터를 못 바꿨으면 새 사본은 고아다 -- 지우고 이전 상태를 그대로 둔다.
            zip_path.unlink(missing_ok=True)
            raise
        for old in self.dir.glob("*.zip"):
            if old != zip_path:
                old.unlink(missing_ok=True)

    def clear(self) -> None:
        self.pointer.unlink(missing_ok=True)
        for old in self.dir.glob("*.zip") if self.dir.exists() else []:
            old.unlink(missing_ok=True)

    def load(self) -> tuple[dict, bytes] | None:
        """(포인터, zip 바이트). 저장한 적 없으면 None. 깨졌으면 ValueError."""
        try:
            pointer = json.loads(self.pointer.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ValueError(f"활성 데이터셋 포인터를 읽지 못했다: {exc}") from exc
        version = str(pointer.get("version", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            raise ValueError("활성 데이터셋 포인터의 버전 형식이 올바르지 않다")
        try:
            return pointer, (self.dir / f"{version}.zip").read_bytes()
        except OSError as exc:
            raise ValueError(f"저장된 묶음 파일을 읽지 못했다: {exc}") from exc
