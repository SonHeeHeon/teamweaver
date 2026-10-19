"""데이터 원천 API(2026-10-19, claude-a -- api/hr_source).

- GET  /api/hr-source                      모드(API/CSV)·표별 원천·마지막 받기·진행 중 사업 기술 경력 갱신 주체
- POST /api/hr-source/sync                 지금 받기(관리자) -- 받는 중이면 409, 설정이 없으면 409
- POST /api/hr-source/skill-months-owner   갱신 주체 바꾸기(관리자, 화면이 든 판이 지금 판일 때만) → 새 판
"""
from __future__ import annotations

from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api import hr_source
from api.admin import require_admin
from api.deps import check_dataset_version

router = APIRouter()


@router.get("/api/hr-source")
async def get_status(request: Request) -> dict:
    return await anyio.to_thread.run_sync(hr_source.status, request.app)


@router.post("/api/hr-source/sync", dependencies=[Depends(require_admin)])
async def post_sync(request: Request) -> dict:
    try:
        result = await hr_source.sync(request.app, trigger="manual")
    except hr_source.SyncBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if result["status"] == "off":
        raise HTTPException(status_code=409, detail=result["detail"])
    return {"result": result, "dataset": request.app.state.dataset.info.to_dict(),
            "status": await anyio.to_thread.run_sync(hr_source.status, request.app)}


class OwnerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str = Field(min_length=1, max_length=128)
    owner: Literal["service", "source"]


@router.post("/api/hr-source/skill-months-owner", dependencies=[Depends(require_admin)])
async def post_owner(body: OwnerIn, request: Request) -> dict:
    check_dataset_version(body.dataset_version, request.app.state.dataset)
    out = await hr_source.set_owner(request, body.owner)
    return {**out, "status": await anyio.to_thread.run_sync(hr_source.status, request.app)}
