"""POST /api/concurrency/candidates -- 빈자리를 프로젝트 동시 수행으로 채울 수 있는 사람(2026-10-19, claude-a -- 사용자 결정 "사람 단위로").

계산 결과(또는 손 편성 명단)의 빈자리마다 후보를 낸다(core/evaluate/concurrency). 다시 풀지 않는다 -- 부서장이 사유와 함께
허용(`POST /api/workspace/concurrency`)하면 화면이 다시 계산한다. 이름은 화면 표시용으로 붙인다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset
from api.schemas import MAX_PLAN_ENTRIES, EntryIn

router = APIRouter()


class CandidatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str = Field(min_length=1, max_length=128)
    entries: list[EntryIn] = Field(max_length=MAX_PLAN_ENTRIES)
    weights: dict[str, float] = Field(default={}, max_length=200)
    top: int = Field(default=3, ge=1, le=10)


@router.post("/api/concurrency/candidates")
def post_candidates(body: CandidatesIn, dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    check_dataset_version(body.dataset_version, dataset)
    from api.routes.justification import display_names
    from core.evaluate.concurrency import concurrency_candidates
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    graph = dataset.graph
    bad = sorted({e.person_id for e in body.entries if e.person_id not in graph.pid_index}
                 | {e.project_id for e in body.entries if e.project_id not in graph.project_index})
    if bad:
        raise HTTPException(status_code=422, detail=f"이 데이터에 없는 ID: {', '.join(bad[:5])}")
    S = ScoringEngine(graph).skill_matrix(body.weights)
    rows = concurrency_candidates(graph, S, [AssignEntry(**e.model_dump()) for e in body.entries], top=body.top)
    names = display_names(dataset)
    for r in rows:
        for c in r["candidates"]:
            c["name"] = names.get(c["person_id"], c["person_id"])
    return {"dataset_version": dataset.info.version, "shortfalls": rows}
