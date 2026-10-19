"""야간 배치 API(2026-10-18, claude-b -- api/nightly.py).

- GET  /api/nightly        예약(켜짐·시간대·다음 실행), 실행 중 단계, 부팅 때 넣은 밤 결과, 마지막 보고서(점검·풀이·소명 요약).
                           아침 알림 화면이 이것을 읽는다. 개인정보는 없다(데이터 id·인원 수·점검 문구·계산 요약).
- POST /api/nightly/run    관리자: 지금 한 번 돌린다(minutes 동안, 기본 60분). 이미 돌고 있으면 409.
- POST /api/nightly/stop   관리자: 돌고 있는 실행을 멈춘다(풀던 계산은 그때까지의 해로 끝내고 남은 단계는 하지 않는다).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.admin import require_admin

router = APIRouter()


def _nightly(request: Request):
    n = getattr(request.app.state, "nightly", None)
    if n is None:
        raise HTTPException(status_code=503, detail="야간 배치가 준비되지 않았다.")
    return n


@router.get("/api/nightly")
def nightly_status(request: Request) -> dict:
    return _nightly(request).status()


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(default=60, ge=2, le=300)


@router.post("/api/nightly/run", dependencies=[Depends(require_admin)])
def nightly_run(req: RunRequest, request: Request) -> dict:
    n = _nightly(request)
    if not n.start_run("manual", time.time() + req.minutes * 60):
        raise HTTPException(status_code=409, detail="야간 배치가 이미 돌고 있다.")
    return {"started": True, "minutes": req.minutes}


@router.post("/api/nightly/stop", dependencies=[Depends(require_admin)])
def nightly_stop(request: Request) -> dict:
    return {"stopping": _nightly(request).request_stop("finish")}
