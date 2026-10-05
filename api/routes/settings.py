"""GET/PUT /api/settings -- 관리자 배치 설정(K8). 설계: api/settings.py docstring.

시스템 전체 인증은 없다(로드맵 6단계). PUT은 TEAMWEAVER_ADMIN_TOKEN이 설정돼 있으면
관리자 토큰을 요구한다(api/admin.py). 본격적인 권한 체계는 사용자 결정 사항이다."""
import anyio
from fastapi import APIRouter, Depends, HTTPException, Request

from api.admin import require_admin
from api.deps import get_settings_store
from api.schemas import SettingsBody, SettingsUpdate
from api.settings import PlacementSettings, SettingsConflict, SettingsState, SettingsStore
from api.review_judge import KEY_ENV, api_key
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
            # 지금 설정으로 실제로 쓸 계산 시간(자동이면 인원·방식 기준 권장값, 아니면 수동값). 화면은 이 값을
            # time_limit으로 보낸다 -- 서명·캐시·PDF가 숫자 하나로 같게 묶인다.
            "effective_time_limit": state.settings.to_milp_params(n_people=n).time_limit,
            "recommended_time": rec("fixed"),
            # 월별 투입률은 더 오래 걸린다(실측 2~4배) -- 화면이 고른 방식에 맞춰 보여 준다.
            "recommended_time_monthly": rec("monthly"),
            "jev_available": api_key() is not None}


@router.get("/api/settings", response_model=SettingsBody)
def get_settings(request: Request, store: SettingsStore = Depends(get_settings_store)) -> dict:
    return _body(store.current(), request)


@router.put("/api/settings", response_model=SettingsBody, dependencies=[Depends(require_admin)])
async def put_settings(body: SettingsUpdate, request: Request,
                       store: SettingsStore = Depends(get_settings_store)) -> dict:
    # 필드 전체를 요구한다: 일부만 보내면 나머지가 조용히 기본값으로 되돌아간다.
    # review_judge는 이 칸이 생기기 전의 화면이 보내지 않으므로 빠지면 지금 값을 유지한다(이전 화면 호환).
    missing = set(PlacementSettings.model_fields) - {"review_judge"} - body.settings.model_fields_set
    if missing:
        raise HTTPException(status_code=422,
                            detail=f"모든 설정 필드를 보내야 한다. 빠진 필드: {sorted(missing)}")
    settings = body.settings
    if "review_judge" not in settings.model_fields_set:
        settings = settings.model_copy(update={"review_judge": store.current().settings.review_judge})
    stored_judge = store.current().settings.review_judge
    # 키 검사는 Jev로 *바꿀* 때만 한다 -- 키가 사라진 뒤에도 다른 설정은 저장할 수 있어야 한다(리뷰 S3).
    if settings.review_judge == "jev" and stored_judge != "jev" and api_key() is None:
        raise HTTPException(status_code=422,
                            detail=f"Jev 판정을 쓰려면 서버에 {KEY_ENV}가 설정돼 있어야 한다.")
    state = request.app.state
    # 데이터셋을 다시 만드는 경우는 둘이다: 판정 방식을 바꿨거나, Jev 판정이 실패해 규칙 기반으로 만들어진
    # 상태에서 "판정 다시 시도"(retry_judge)를 눌렀다. 다른 칸만 저장할 때는 다시 만들지 않는다(리뷰 2라운드 S2).
    data_judge = state.dataset.info.review_judge
    rebuild = settings.review_judge != data_judge and (settings.review_judge != stored_judge or body.retry_judge)
    if not rebuild:
        return await anyio.to_thread.run_sync(_save, store, settings, body.based_on, request)
    # 판정 방식이 바뀌면 활성 데이터셋을 다시 만든다 -- 업로드 전환과 같은 표시·잠금을 쓴다.
    # 다시 만들기(Jev면 외부 호출 수십 초) 전에 저장 충돌부터 거른다.
    if body.based_on != store.current().updated_at:
        raise HTTPException(status_code=409, detail="그사이 다른 사람이 설정을 저장했다. 다시 불러온 뒤 저장할 것.")
    if state.dataset_switching:
        raise HTTPException(status_code=409, detail="다른 데이터셋 작업을 처리하는 중이다.")
    state.dataset_switching = True
    try:
        async with state.dataset_lock:
            from api.routes.datasets import rebuild_with_judge
            new, err = await anyio.to_thread.run_sync(rebuild_with_judge, request.app, settings.review_judge)
            if new is None:
                raise HTTPException(status_code=409, detail=err)
            try:
                await anyio.to_thread.run_sync(_save, store, settings, body.based_on, request)
            except HTTPException:
                new.retire()                    # 저장이 실패하면 데이터셋은 그대로 두고 새로 만든 것은 닫는다
                raise
            old = state.dataset
            state.dataset = new
            old.retire()
            return {**_body(store.current(), request), "dataset": new.info.to_dict()}
    finally:
        state.dataset_switching = False


def _save(store: SettingsStore, settings: PlacementSettings, based_on, request: Request) -> dict:
    try:
        return _body(store.save(settings, based_on=based_on), request)
    except SettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"설정 파일을 저장하지 못했다({store.path}): {exc}") from exc
