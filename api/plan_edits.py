"""적용한 교체의 영속(K13). 원 플랜 서명(plan_token)을 키로, 원 플랜과 교체 id 목록을 저장한다.

서명이 데이터셋 버전·라벨·명단·가중치·파라미터에 묶여 있으므로, 같은 계산을 다시 하면(재기동 후
캐시·재계산) 같은 키가 나와 저장분을 찾을 수 있고, 다른 데이터·규칙의 결과에는 붙지 않는다.
수치는 저장하지 않는다 -- 불러올 때 서버가 원 플랜에서 다시 적용해 계산한다(PDF와 같은 원칙).

- 레코드마다 파일 하나(`plan_edits/<token>.json`): 요청마다 전체를 읽고 쓰지 않는다.
- `revision`: 화면이 보낸 단조 증가 번호. 저장된 것보다 작거나 같으면 무시한다 -- 늦게 도착한
  옛 요청이 나중 상태(취소 등)를 덮지 못한다. 취소도 빈 목록 레코드로 남겨 순서를 지킨다.
- 서버 전체 공유다: 같은 데이터·설정·가중치로 계산한 플랜이면 사용자가 달라도 같은 키다.
"""
import json
import re
import threading
from pathlib import Path

from api.storage import atomic_write, ensure_private_dir

MAX_SAVED_EDITS = 200
TOKEN_RE = re.compile(r"[0-9a-f]{64}")


class PlanEditStore:
    def __init__(self, root: Path):
        self.dir = root / "plan_edits"
        self._lock = threading.Lock()

    def _path(self, token: str) -> Path:
        if not TOKEN_RE.fullmatch(token):           # 경로 조작 방지: 서명은 sha256 hex다
            raise ValueError("plan_token 형식이 아니다")
        return self.dir / f"{token}.json"

    def _read(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None

    def get(self, token: str) -> dict | None:
        with self._lock:
            return self._read(self._path(token))

    def put(self, token: str, record: dict, revision: int) -> bool:
        """revision이 저장된 것보다 크면 저장하고 True. 아니면 무시하고 False."""
        path = self._path(token)
        with self._lock:
            old = self._read(path)
            try:
                stored = int(old.get("revision", -1)) if old is not None else -1
            except (TypeError, ValueError):
                stored = -1                          # 손상된 레코드는 덮어쓸 수 있게
            if stored >= revision:
                return False
            ensure_private_dir(self.dir)
            atomic_write(path, json.dumps({**record, "revision": revision},
                                          ensure_ascii=False).encode("utf-8"))
            files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for stale in files[:max(0, len(files) - MAX_SAVED_EDITS)]:
                stale.unlink(missing_ok=True)
            return True

    def prune(self, keep_dataset_version: str | None) -> None:
        """다른 데이터셋의 레코드를 지운다(되돌리기·새 업로드 때). 업로드 데이터의 사번·명단이
        교체 기록 형태로 남지 않게 한다. None이면 전부 지운다."""
        with self._lock:
            for path in self.dir.glob("*.json") if self.dir.exists() else []:
                rec = self._read(path)
                if rec is None or rec.get("dataset_version") != keep_dataset_version:
                    path.unlink(missing_ok=True)
