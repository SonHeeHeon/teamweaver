"""GET/PUT /api/settings -- 관리자 배치 설정(K8). 설계: api/settings.py docstring.

시스템 전체 인증은 없다(로드맵 6단계). PUT은 TEAMWEAVER_ADMIN_TOKEN이 설정돼 있으면
관리자 토큰을 요구한다(api/admin.py). 본격적인 권한 체계는 사용자 결정 사항이다."""
from fastapi import APIRouter, Depends, HTTPException, Request

from api.admin import require_admin
from api.deps import get_settings_store
from api.schemas import SettingsBody, SettingsUpdate
from api.settings import PlacementSettings, SettingsConflict, SettingsState, SettingsStore
from core.optimize.time_budget import recommend

router = APIRouter()


def _body(state: SettingsState, request: Request) -> dict:
    # 인원 수만 읽는다 -- 데이터셋을 잡지(acquire) 않는다. 설정 저장이 데이터셋 전환과 얽히면 안 된다.
    n = len(request.app.state.dataset.graph.people)

    def rec(mode: str) -> dict:
        tb = recommend(n, allocation_mode=mode)
        return {"n_people": n, "per_solve_s": tb.per_solve_s, "worst_case_total_s": tb.worst_case_total_s,
                "measured": tb.measured, "basis": tb.basis}
    return {"settings": state.settings.model_dump(),
            "defaults": PlacementSettings().model_dump(),
            "bounds": PlacementSettings.bounds(),
            "updated_at": state.updated_at,
            "load_error": state.load_error,
            # 지금 데이터 규모의 권장 계산 시간(claude-a 리허설 측정, core/optimize/time_budget). 화면 안내용.
            "recommended_time": rec("fixed"),
            # 월별 투입률은 더 오래 걸린다(실측 2~4배) -- 화면이 고른 방식에 맞춰 보여 준다.
            "recommended_time_monthly": rec("monthly")}


@router.get("/api/settings", response_model=SettingsBody)
def get_settings(request: Request, store: SettingsStore = Depends(get_settings_store)) -> dict:
    return _body(store.current(), request)


@router.put("/api/settings", response_model=SettingsBody, dependencies=[Depends(require_admin)])
def put_settings(body: SettingsUpdate, request: Request,
                 store: SettingsStore = Depends(get_settings_store)) -> dict:
    # 필드 전체를 요구한다: 일부만 보내면 나머지가 조용히 기본값으로 되돌아간다.
    missing = set(PlacementSettings.model_fields) - body.settings.model_fields_set
    if missing:
        raise HTTPException(status_code=422,
                            detail=f"모든 설정 필드를 보내야 한다. 빠진 필드: {sorted(missing)}")
    try:
        return _body(store.save(body.settings, based_on=body.based_on), request)
    except SettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"설정 파일을 저장하지 못했다({store.path}): {exc}") from exc
