"""관리자 배치 설정(K8): 조직 공통 MILP 파라미터를 서버 파일에 영속한다.

설정은 서버에 두지만 /api/optimize·/api/whatif는 stateless로 남는다 -- 서버가
설정을 몰래 적용하지 않고, 웹이 GET /api/settings로 읽어 milp_params로 명시적으로
보낸다. 그래야 같은 요청은 설정 변경과 무관하게 같은 결과를 내고(캐시·재현성),
교체 검토(what-if)가 플랜을 계산한 *그때의* 기준으로 점수를 낼 수 있다.

MilpParams 기본값(min_alloc 0.2)은 바꾸지 않는다 -- 모델 기본값 변경은 Codex C6
소관이다. 실데이터 답변의 30%는 이 설정 계층의 기본값이다.
"""
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.storage import data_dir
from core.optimize.milp import MilpParams

log = logging.getLogger(__name__)

SETTINGS_PATH_ENV = "TEAMWEAVER_SETTINGS_PATH"


class PlacementSettings(BaseModel):
    """관리자가 바꿀 수 있는 배치 규칙. 범위는 화면 검사에도 그대로 쓰인다(bounds()).

    노출하지 않는 MilpParams 필드(pair_keep_ratio·max_pairs·slack_penalty)는 정책이
    아니라 계산 근사·미충원 big-M 같은 모델 내부값이라 모델 기본값을 그대로 쓴다."""
    model_config = ConfigDict(extra="forbid")

    min_alloc: float = Field(default=0.30, ge=0.05, le=1.0)
    clique_threshold_months: int = Field(default=6, ge=1, le=24)
    lam: float = Field(default=0.3, ge=0.0, le=1.0)
    mu: float = Field(default=0.2, ge=0.0, le=1.0)
    time_limit: int = Field(default=120, ge=5, le=600)
    gap: float = Field(default=0.05, ge=0.0, le=0.2)
    # 한 사람이 같은 달에 맡는 프로젝트 수 상한(C6, 사용자 답변: 최대 3개·보통 1개).
    max_concurrent_projects: int = Field(default=3, ge=1, le=6)

    def to_milp_params(self) -> MilpParams:
        return MilpParams(**self.model_dump())

    @classmethod
    def bounds(cls) -> dict[str, dict[str, float]]:
        """pydantic 필드 제약에서 {필드: {min, max}}를 만든다 -- 범위를 한 곳에만 둔다."""
        out = {}
        for name, field in cls.model_fields.items():
            b = {}
            for m in field.metadata:
                if getattr(m, "ge", None) is not None:
                    b["min"] = m.ge
                if getattr(m, "le", None) is not None:
                    b["max"] = m.le
            out[name] = b
        return out


class SettingsConflict(RuntimeError):
    """화면이 읽은 뒤 다른 사람이 먼저 저장했다."""


_UNCHECKED = object()


@dataclass(frozen=True)
class SettingsState:
    settings: PlacementSettings
    updated_at: str | None          # 마지막 저장 시각(UTC ISO). 저장한 적 없으면 None
    load_error: str | None          # 파일을 읽지 못해 기본값으로 동작 중이면 그 이유


def default_settings_path() -> Path:
    raw = os.environ.get(SETTINGS_PATH_ENV, "").strip()
    return Path(raw).expanduser() if raw else data_dir() / "settings.json"


class SettingsStore:
    """JSON 파일 하나에 설정을 둔다. DB가 없는 현 구조에서 가장 작은 영속 계층.

    파일이 깨졌거나 범위 밖이면 조용히 기본값으로 바꾸지 않는다 -- 기본값으로
    동작하되 load_error로 화면에 알린다(관리자가 정한 30%가 몰래 20%가 되면 안 된다).
    저장은 같은 폴더 임시 파일 + os.replace로 원자적이다."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()       # 동시 PUT이 파일과 메모리 값을 엇갈리게 하지 않게
        self._state = self._load()

    def _load(self) -> SettingsState:
        # exists()도 try 안에 둔다: 폴더 권한이 없으면 exists()가 PermissionError를
        # 던지는데, 그게 lifespan을 죽이면 서버가 아예 안 뜬다(리뷰 실측).
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            settings = PlacementSettings(**raw["settings"])
            updated_at = raw.get("updated_at")
            if updated_at is not None and not isinstance(updated_at, str):
                raise ValueError(f"updated_at이 문자열이 아니다: {updated_at!r}")
            return SettingsState(settings, updated_at, None)
        except FileNotFoundError:
            return SettingsState(PlacementSettings(), None, None)
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            msg = f"설정 파일 {self.path.name}을 읽지 못해 기본값으로 동작 중: {exc}"
            log.warning("%s (%s)", msg, self.path)
            return SettingsState(PlacementSettings(), None, msg)

    def current(self) -> SettingsState:
        return self._state

    def save(self, settings: PlacementSettings, based_on=_UNCHECKED) -> SettingsState:
        """based_on이 주어지면 현재 updated_at과 같아야 저장한다(아니면 SettingsConflict).
        OSError(권한·디스크)는 호출자에게 올린다 -- 라우트가 이유를 담아 500으로 알린다."""
        with self._lock:
            if based_on is not _UNCHECKED and based_on != self._state.updated_at:
                raise SettingsConflict(
                    f"다른 사용자가 먼저 설정을 저장했다(서버 {self._state.updated_at}, "
                    f"화면 {based_on}). 새 값을 불러와 다시 확인할 것.")
            return self._save_locked(settings)

    def _save_locked(self, settings: PlacementSettings) -> SettingsState:
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        body = json.dumps({"settings": settings.model_dump(), "updated_at": updated_at},
                          ensure_ascii=False, indent=2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._state.load_error and self.path.exists():
            # 읽지 못한 원본을 덮어쓰기 전에 남겨 둔다(관리자가 직접 고쳐 쓴 값일 수 있다).
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            os.replace(self.path, self.path.with_name(f"{self.path.name}.unreadable-{stamp}"))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._state = SettingsState(settings, updated_at, None)
        return self._state
