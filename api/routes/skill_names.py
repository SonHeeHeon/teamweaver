"""낯선 기술 이름 정리(2026-10-22 사용자 "4,5번은 개발해보자", claude-a -- core/ingest/skill_mapping, 설계 `.omc/plan/2026-10-22-ai-value.md`).

- GET  /api/skills/unknown            사전(이 데이터의 넓힌 사전)으로 못 맞춘 이름 + 맥락 + 문자열 후보(AI 없음)
- POST /api/skills/unknown/suggest    AI 제안(사전 id 안에서만, 실데이터는 설명 글과 같은 외부 전송 정책 -- 사람 정보는 이름·수만 간다)
- POST /api/skills/names              관리자가 승인한 결정을 새 판으로(manifest skill_names) -- 되돌리기로 무른다
"""
from __future__ import annotations

from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.admin import require_admin
from api.deps import check_dataset_version, get_openai_client_or_none

router = APIRouter()
MAX_SUGGEST = 100


def _tables(request: Request):
    from api.routes.workspace import _files
    from api.workspace_edits import read_manifest, read_table
    files = _files(request)
    tables = {}
    for name in ("person_skills.csv", "project_skill_requirements.csv", "work_skills.csv"):
        if name in files:
            tables[name] = read_table(files, name)[1]
    return files, tables, read_manifest(files)


def _queue(request: Request) -> tuple[list[dict], dict]:
    from core.ingest.skill_mapping import decisions_of, dictionary_for, unknown_names
    from core.ingest.skills import norm_key
    _, tables, manifest = _tables(request)
    decided = decisions_of(manifest)
    sd = dictionary_for(manifest)
    return unknown_names(tables, sd, {norm_key(d["name"]) for d in decided}), {"decided": decided, "sd": sd}


@router.get("/api/skills/unknown")
def get_unknown(request: Request, dataset_version: str | None = None) -> dict:
    from api.workspace import WorkspaceUnavailable, current_files
    from core.ingest.skill_mapping import string_candidates
    check_dataset_version(dataset_version, request.app.state.dataset)
    try:
        current_files(request.app, request.app.state.dataset)
    except WorkspaceUnavailable as exc:
        # 원천 묶음을 고칠 수 없는 데이터(고정 데모·mapping.json 묶음)는 읽기 화면이라 빈 목록과 까닭으로 -- 409는 브라우저 콘솔 오류로 남았다(느린 E2E)
        return {"dataset_version": request.app.state.dataset.info.version, "dictionary_version": None, "items": [], "decided": [],
                "unavailable": str(exc)}
    items, ctx = _queue(request)
    return {"dataset_version": request.app.state.dataset.info.version, "dictionary_version": ctx["sd"].version,
            "items": [{**it, "candidates": string_candidates(ctx["sd"], it["name"], 3)} for it in items],
            "decided": ctx["decided"]}


class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str | None = None
    names: list[str] | None = Field(default=None, max_length=MAX_SUGGEST)


@router.post("/api/skills/unknown/suggest")
async def post_suggest(body: SuggestIn, request: Request, client=Depends(get_openai_client_or_none)) -> dict:
    from api.deps import llm_client_for
    from api.rag.justification import justification_settings
    from api.routes.justification import ai_model
    from core.config import load_pricing
    from core.ingest.skill_mapping import suggest
    dataset = request.app.state.dataset
    check_dataset_version(body.dataset_version, dataset)
    items, ctx = _queue(request)
    if body.names is not None:
        want = set(body.names)
        items = [it for it in items if it["name"] in want]
    items = items[:MAX_SUGGEST]
    use, why = llm_client_for(dataset, client)
    model = ai_model()

    def go():
        c = use.with_options(timeout=120.0, max_retries=0) if use is not None else None
        return suggest(ctx["sd"], items, client=c, model=model, reasoning_effort=justification_settings(model)["reasoning_effort"],
                       price=load_pricing().get("models", {}).get(model, {}))
    out = await anyio.to_thread.run_sync(go)
    if use is None:
        out["fallback_reason"] = why
    return {**out, "dataset_version": dataset.info.version, "ai_available": use is not None}


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    decision: Literal["alias", "narrower", "new", "not_skill"]
    concept: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=200)
    method: Literal["ai", "string", "manual"] = "manual"


class NamesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str | None = None
    decisions: list[DecisionIn] = Field(min_length=1, max_length=MAX_SUGGEST)


@router.post("/api/skills/names", dependencies=[Depends(require_admin)])
async def post_names(body: NamesIn, request: Request) -> dict:
    """승인한 결정을 새 판으로 켠다(업로드와 같은 검사·켜기). 정리 목록에 없는 이름(이미 사전에 있음·데이터에 없음)은 거절."""
    from api.routes.workspace import _done, _edit, _files
    from api.workspace import _now, commit
    from api.workspace_edits import record_skill_names
    dataset = request.app.state.dataset
    check_dataset_version(body.dataset_version, dataset)
    items, _ = _queue(request)
    pending = {it["name"] for it in items}
    stray = [d.name for d in body.decisions if d.name not in pending]
    if stray:
        raise HTTPException(status_code=422, detail=f"정리할 이름이 아니다(이미 사전에 있거나 데이터에 없다): {', '.join(stray[:5])}")
    before = _files(request)
    files = _edit(record_skill_names, before, [d.model_dump() for d in body.decisions], at=_now())
    counts: dict[str, int] = {}
    for d in body.decisions:
        counts[d.decision] = counts.get(d.decision, 0) + 1
    note = "기술 이름 정리: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    out = await commit(request, before, files, kind="skill_names", note=note,
                       summary={"decisions": [d.model_dump() for d in body.decisions]})
    return await _done(request, out)
