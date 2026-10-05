"""서버 영속 저장 위치와 원자적 쓰기(K13).

설정(K8)·업로드 데이터(K9)·적용 교체(K10)·플랜 서명키가 모두 한 폴더에 산다:
TEAMWEAVER_DATA_DIR(기본 ~/.teamweaver). 업로드 데이터에는 실제 인사 정보가 들어가므로
폴더는 0700, 파일은 0600으로 만든다(같은 서버의 다른 계정이 읽지 못하게).
"""
import os
import tempfile
from pathlib import Path

DATA_DIR_ENV = "TEAMWEAVER_DATA_DIR"


def data_dir() -> Path:
    raw = os.environ.get(DATA_DIR_ENV, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".teamweaver"


def ensure_private_dir(path: Path) -> Path:
    """없으면 0700으로 만든다. 이미 있는 폴더(사용자가 지정한 홈·공유 폴더 등)의 권한은 바꾸지 않는다."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def atomic_write(path: Path, data: bytes) -> None:
    """같은 폴더 임시 파일(0600) → fsync → os.replace. 중간에 죽어도 반쯤 쓴 파일이 남지 않는다."""
    ensure_private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_or_create_secret(path: Path, *, attempts: int = 20, wait: float = 0.05) -> bytes:
    """32바이트 이상 비밀 키를 파일에서 읽거나 처음 한 번 만든다(여러 워커가 동시에 불러도 같은 키).

    완전히 쓰고 fsync한 0600 임시 파일을 os.link로 게시한다 -- 링크는 원자적이고 대상이 있으면
    실패하므로, 빈·반쯤 쓴 파일이 공개되는 순간이 없고 경쟁에서 진 쪽은 이긴 쪽 파일을 읽는다.
    읽은 값이 짧으면(외부 도구가 쓰는 중 등) 잠깐 재시도한 뒤 ValueError."""
    import secrets
    import time
    ensure_private_dir(path.parent)
    if not path.exists():
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(secrets.token_bytes(32).hex())
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
        finally:
            Path(tmp).unlink(missing_ok=True)
    for _ in range(attempts):
        try:
            value = bytes.fromhex(path.read_text(encoding="ascii").strip())
            if len(value) >= 32:
                return value
        except ValueError:
            pass
        time.sleep(wait)
    raise ValueError(f"{path.name}에서 올바른 키를 읽지 못했다")
