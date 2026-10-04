"""GET/PUT /api/settings -- 관리자 배치 설정(K8). 설계: api/settings.py docstring.

인증이 없다: 현 시스템 전체에 인증·권한이 없고(로드맵 6단계), 이 엔드포인트만
막아도 의미가 없다. 접근 통제는 배포 단계에서 함께 정한다(사용자 결정 사항)."""
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_settings_store
from api.schemas import SettingsBody, SettingsUpdate
from api.settings import PlacementSettings, SettingsConflict, SettingsState, SettingsStore

router = APIRouter()


def _body(state: SettingsState) -> dict:
    return {"settings": state.settings.model_dump(),
            "defaults": PlacementSettings().model_dump(),
            "bounds": PlacementSettings.bounds(),
            "updated_at": state.updated_at,
            "load_error": state.load_error}


@router.get("/api/settings", response_model=SettingsBody)
def get_settings(store: SettingsStore = Depends(get_settings_store)) -> dict:
    return _body(store.current())


@router.put("/api/settings", response_model=SettingsBody)
def put_settings(body: SettingsUpdate,
                 store: SettingsStore = Depends(get_settings_store)) -> dict:
    # 필드 전체를 요구한다: 일부만 보내면 나머지가 조용히 기본값으로 되돌아간다.
    missing = set(PlacementSettings.model_fields) - body.settings.model_fields_set
    if missing:
        raise HTTPException(status_code=422,
                            detail=f"모든 설정 필드를 보내야 한다. 빠진 필드: {sorted(missing)}")
    try:
        return _body(store.save(body.settings, based_on=body.based_on))
    except SettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"설정 파일을 저장하지 못했다({store.path}): {exc}") from exc
