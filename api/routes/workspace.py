"""작업 데이터 API -- 신규 사업 등록·단계·떨어짐·고정(사업·사람)·확정·되돌리기(2026-10-18, claude-b, 3가지 핵심 상황 설계).

- GET    /api/workspace                    지금 판·이력·파이프라인(신규 사업)·진행 사업(고정 여부)·대기 인력
- GET    /api/workspace/projects/{id}/form 사업 양식(고도화 과제의 '이전 사업 복사', 양식 고치기)
- POST   /api/workspace/projects           신규 사업 등록 → 새 판
- PATCH  /api/workspace/projects/{id}      단계 바꾸기·양식 고치기 → 새 판
- DELETE /api/workspace/projects/{id}      떨어짐·취소(팀원은 대기) → 새 판
- POST   /api/workspace/lock               고정/해제(사업 전체 또는 지정 사람) → 새 판
- POST   /api/workspace/concurrency        프로젝트 동시 수행 허용(사유 필수)/해제(사람 단위, 2026-10-19 claude-a) → 새 판
- POST   /api/workspace/apply              확정(자동 편성 행·손 편성·긴급 투입 결과) → 새 위반이 없을 때만 새 판
- POST   /api/workspace/undo               직전 판으로
- GET    /api/skills/dictionary            양식의 기술 검색(대표 이름·별칭)

판을 바꾸는 요청은 관리자만, 화면이 든 판(dataset_version)이 지금 판과 같을 때만(409) 받는다.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.admin import require_admin
from api.deps import check_dataset_version
from api.lifecycle import lifecycle_of
from api.settings import horizon_months_of
from api.schemas import MAX_PLAN_ENTRIES, EntryIn
from api.workspace import History, WorkspaceUnavailable, commit, current_files, undo
from api.workspace_edits import (CURRENT, PROJECTS, EditError, NewProject, add_project, apply_entries, concurrency_of,
                                 pipeline_of, project_form, read_manifest, read_table, remove_project, set_concurrency,
                                 set_lock, update_project)

router = APIRouter()
GRADE_ORDER = ("특급", "고급", "중급", "초급")


def _files(request: Request):
    try:
        return current_files(request.app, request.app.state.dataset)
    except WorkspaceUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _edit(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except EditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _names(dataset) -> dict[str, str]:
    from api.routes.justification import display_names
    return display_names(dataset)


def workspace_view(request: Request) -> dict:
    app = request.app
    dataset = app.state.dataset
    info = dataset.info
    base = {"dataset_version": info.version, "dataset_id": info.dataset_id, "synthetic": info.synthetic}
    try:
        files = current_files(app, dataset)
    except WorkspaceUnavailable as exc:
        return {**base, "editable": False, "reason": str(exc), "pipeline": [], "ongoing": [], "bench": [], "concurrency": [],
                "history": []}
    m = read_manifest(files)
    pipe = pipeline_of(m)
    lifecycle = lifecycle_of(m)                    # 단계·실제 시작·추가 과제 상위·외주(표시용, api/lifecycle)
    proposals = [p for p in (m.get("proposals") or []) if isinstance(p, str)]
    _, prow = read_table(files, PROJECTS)
    real = {r["project_id"]: r for r in prow}
    names = _names(dataset)
    graph = dataset.graph
    grade_of = {p.id: p.grade.value for p in graph.people}

    def person(pid: str, **extra) -> dict:
        return {"person_id": pid, "name": names.get(pid, pid), "grade": grade_of.get(pid), **extra}

    teams: dict[str, list[dict]] = {}
    for c in dataset.current:
        teams.setdefault(c.project_id, []).append(person(c.person_id, alloc=c.alloc, locked=bool(c.locked)))
    horizon = [h for h in (m.get("horizon_start"),) if h]
    pipeline, ongoing = [], []
    for j in graph.projects:
        row = real.get(j.id, {})
        team = sorted(teams.get(j.id, []), key=lambda t: (GRADE_ORDER.index(t["grade"]) if t["grade"] in GRADE_ORDER else 9,
                                                         t["person_id"]))
        need = {g.value: n for g, n in j.grade_headcount.items()}
        filled: dict[str, int] = {}
        for t in team:
            filled[t["grade"]] = filled.get(t["grade"], 0) + 1
        # 원문 달의 공백을 걷는다 -- 화면이 추가 과제 기간을 이 값과 비교한다(Codex 리뷰 2라운드 2026-10-19)
        item = {"project_id": j.id, "name": j.name, "start": (row.get("start_month") or "").strip() or None,
                "end": (row.get("end_month") or "").strip() or None,
                "monthly_budget": j.monthly_budget,
                "seats": {g: {"need": need.get(g, 0), "filled": filled.get(g, 0)} for g in GRADE_ORDER
                          if need.get(g, 0) or filled.get(g, 0)},
                "team": team, "lifecycle": lifecycle.get(j.id)}
        if j.id in proposals or j.id in pipe:
            stage = pipe.get(j.id, {}).get("stage") or ("수주" if j.phase.value == "실행" else "제안")
            pipeline.append({**item, "stage": stage, "predecessor": pipe.get(j.id, {}).get("predecessor"),
                             "parent": pipe.get(j.id, {}).get("parent"),
                             "external_headcount": pipe.get(j.id, {}).get("external_headcount") or 0,
                             "registered_at": pipe.get(j.id, {}).get("registered_at")})
        elif team:
            # 계획 창 끝보다 먼저 끝나는가(끝나 가는 사업) -- 실제 종료 달 기준. 창 끝까지 가는 사업은 아니다
            ongoing.append({**item, "addons": sorted(k for k, v in lifecycle.items() if v.get("parent") == j.id),
                            "locked_all": all(t["locked"] for t in team),
                            "locked_count": sum(1 for t in team if t["locked"]),
                            "ends_in_horizon": bool(item["end"]) and item["end"] < (_horizon_last(m) or "")})
    entries = History().entries()
    history = list(reversed(entries)) if entries and entries[-1].get("version") == info.version else []
    from api.unavailable import horizon_reason
    bench = [person(p, unavailable=horizon_reason(dataset, p)) for p in (m.get("bench") or []) if isinstance(p, str)]
    # 프로젝트 동시 수행 허용 인력(사람 단위, 사유와 함께) -- 화면 목록·해제
    concurrency = [person(e["person_id"], max_projects=e.get("max_projects"), reason=e.get("reason"),
                          memo=e.get("memo") or "", granted_at=e.get("granted_at") or "")
                   for e in concurrency_of(m)]
    return {**base, "editable": True, "horizon_start": horizon[0] if horizon else None,
            "horizon_last": _horizon_last(m), "pipeline": pipeline, "ongoing": ongoing, "bench": bench,
            "concurrency": concurrency, "history": history}


def _horizon_last(m: dict) -> str | None:
    start = m.get("horizon_start")
    if not isinstance(start, str) or len(start) != 7:
        return None
    from core.domain.models import HORIZON_CHOICES, HORIZON_MONTHS
    n = m.get("horizon_months")                     # 데이터마다 6 또는 12(2026-10-19 "계획기간 1년 확장")
    n = n if type(n) is int and n in HORIZON_CHOICES else HORIZON_MONTHS
    y, mo = int(start[:4]), int(start[5:])
    mo += n - 1
    y += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


@router.get("/api/workspace")
async def get_workspace(request: Request) -> dict:
    return await anyio.to_thread.run_sync(workspace_view, request)


@router.get("/api/workspace/projects/{project_id}/form")
def get_project_form(project_id: str, request: Request, as_: Literal["copy", "addon"] = Query("copy", alias="as"),
                     start: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")) -> dict:
    """copy = 그 사업 양식(고도화 과제의 이전 사업 복사·양식 고치기), addon = 그 진행 사업에 붙일 추가 과제 미리 채우기(2026-10-19)."""
    files = _files(request)
    if as_ == "addon":
        from api.workspace_edits import addon_form
        return _edit(addon_form, files, project_id, start).model_dump()
    return _edit(project_form, files, project_id).model_dump()


class _Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str = Field(min_length=1, max_length=128)


class AddProjectIn(_Versioned):
    project: NewProject


async def _done(request: Request, out: dict, **extra) -> dict:
    return {**out, **extra, "workspace": await anyio.to_thread.run_sync(workspace_view, request)}


@router.post("/api/workspace/projects", dependencies=[Depends(require_admin)])
async def post_project(body: AddProjectIn, request: Request) -> dict:
    check_dataset_version(body.dataset_version, request.app.state.dataset)
    before = _files(request)
    from api.workspace import _now
    files, pid = _edit(add_project, before, body.project, registered_at=_now())
    out = await commit(request, before, files, kind="add_project", note=f"신규 사업 등록: {pid} {body.project.name}",
                       summary={"project_id": pid, "stage": body.project.stage})
    return await _done(request, out, project_id=pid)


class PatchProjectIn(_Versioned):
    stage: Literal["예정", "제안", "수주"] | None = None
    project: NewProject | None = None


@router.patch("/api/workspace/projects/{project_id}", dependencies=[Depends(require_admin)])
async def patch_project(project_id: str, body: PatchProjectIn, request: Request) -> dict:
    check_dataset_version(body.dataset_version, request.app.state.dataset)
    if body.stage is None and body.project is None:
        raise HTTPException(status_code=422, detail="바꿀 것(stage 또는 project)이 없다")
    before = _files(request)
    files = _edit(update_project, before, project_id, stage=body.stage, form=body.project)
    note = f"{project_id} 단계: {body.stage}" if body.stage else f"{project_id} 양식 고침"
    out = await commit(request, before, files, kind="update_project", note=note,
                       summary={"project_id": project_id, "stage": body.stage})
    return await _done(request, out)


@router.delete("/api/workspace/projects/{project_id}", dependencies=[Depends(require_admin)])
async def delete_project(project_id: str, dataset_version: str, request: Request) -> dict:
    check_dataset_version(dataset_version, request.app.state.dataset)
    before = _files(request)
    files, freed = _edit(remove_project, before, project_id)
    out = await commit(request, before, files, kind="remove_project", note=f"{project_id} 떨어짐·취소(팀원 {len(freed)}명 대기)",
                       summary={"project_id": project_id, "freed": freed})
    return await _done(request, out, freed=freed)


class LockIn(_Versioned):
    project_id: str = Field(min_length=1, max_length=32)
    people: list[Annotated[str, Field(min_length=1, max_length=32)]] | None = Field(default=None, max_length=200)
    locked: bool = True


def _who(dataset, people: list[str]) -> str:
    """변경 이력에 남길 사람 목록: 표시 이름(가상 데이터만, 실데이터는 사번 -- K5) 다섯 명까지 + "외 N명"(2026-10-19 부서장 점검 f7)."""
    names, people = _names(dataset), list(dict.fromkeys(people))
    shown = ", ".join(names.get(p, p) for p in people[:5])
    return shown + (f" 외 {len(people) - 5}명" if len(people) > 5 else "")


@router.post("/api/workspace/lock", dependencies=[Depends(require_admin)])
async def post_lock(body: LockIn, request: Request) -> dict:
    check_dataset_version(body.dataset_version, request.app.state.dataset)
    before = _files(request)
    files = _edit(set_lock, before, body.project_id, body.people, body.locked)
    who = "사업 전체" if body.people is None else _who(request.app.state.dataset, body.people)
    out = await commit(request, before, files, kind="lock" if body.locked else "unlock",
                       note=f"{body.project_id} {'고정' if body.locked else '고정 해제'}: {who}",
                       summary={"project_id": body.project_id, "people": body.people, "locked": body.locked})
    return await _done(request, out)


ConcurrencyReason = Literal["인력 부족", "기술 지원", "긴급 지원", "기타"]     # core.ingest.concurrency.REASONS와 같다


class ConcurrencyIn(_Versioned):
    person_id: str = Field(min_length=1, max_length=32)
    allow: bool = True
    max_projects: Literal[2, 3] = 2
    reason: ConcurrencyReason | None = None
    memo: str = Field(default="", max_length=200)


@router.post("/api/workspace/concurrency", dependencies=[Depends(require_admin)])
async def post_concurrency(body: ConcurrencyIn, request: Request) -> dict:
    """프로젝트 동시 수행 허용·해제(2026-10-19 사용자 결정 "사람 단위로", 사유 필수). 허용한 사람만 전일 투입 계산에서 같은 달 여러 사업."""
    check_dataset_version(body.dataset_version, request.app.state.dataset)
    if body.allow and body.reason is None:
        raise HTTPException(status_code=422, detail="프로젝트 동시 수행을 허용하려면 사유를 골라야 한다")
    before = _files(request)
    from datetime import date
    files = _edit(set_concurrency, before, body.person_id, body.allow, max_projects=body.max_projects,
                  reason=body.reason, memo=body.memo, granted_at=date.today().isoformat())
    name = _names(request.app.state.dataset).get(body.person_id, body.person_id)
    out = await commit(request, before, files, kind="concurrency" if body.allow else "concurrency_off",
                       note=(f"{name} 프로젝트 동시 수행 허용({body.reason})" if body.allow
                             else f"{name} 프로젝트 동시 수행 해제"),
                       summary={"person_id": body.person_id, "allow": body.allow, "max_projects": body.max_projects,
                                "reason": body.reason, "memo": body.memo})
    return await _done(request, out)


class GrantIn(BaseModel):
    """확정과 함께 기록하는 동시 수행 허용(긴급 투입 '동시 수행으로 지원')."""
    model_config = ConfigDict(extra="forbid")
    person_id: str = Field(min_length=1, max_length=32)
    max_projects: Literal[2, 3] = 2
    reason: ConcurrencyReason = "긴급 지원"
    memo: str = Field(default="", max_length=200)


class SkillAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    min_months: int = Field(default=12, ge=0, le=360)
    headcount: int = Field(default=1, ge=1, le=50)


class ApplyIn(_Versioned):
    entries: list[EntryIn] = Field(max_length=MAX_PLAN_ENTRIES)
    note: str = Field(default="", max_length=300)
    # 긴급 투입 화면의 "무슨 일인지" 원문(2026-10-19 부서장 점검 f6): 이력 summary에 남겨 인사팀 검토 자료의 상황 메모 처음 값으로 쓴다.
    # note에 섞인 문장을 잘라 쓰지 않으려고 따로 받는다. AI에는 보내지 않는다(상황 메모와 같음).
    situation: str = Field(default="", max_length=300)
    source: Literal["auto", "manual", "emergency", "plan"] = "manual"
    headcount_add: dict[str, dict[Literal["특급", "고급", "중급", "초급"], Annotated[int, Field(ge=0, le=10)]]] = {}
    budget_add: dict[str, Annotated[int, Field(ge=0, le=10_000_000)]] = {}
    skills_add: dict[str, list[SkillAdd]] = Field(default={}, max_length=10)
    concurrency_add: list[GrantIn] = Field(default=[], max_length=20)


def _violations(app, dataset, entries) -> dict[tuple[str, str], object]:
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    params = dataset_params(app, dataset)
    eng = ScoringEngine(dataset.graph)
    ev = evaluate_plan(dataset.graph, eng.skill_matrix({}), eng.synergy_matrix(), params,
                       [e if isinstance(e, AssignEntry) else AssignEntry(**e) for e in entries])
    return {(v.code, v.location): v for v in ev.violations}


def dataset_params(app, dataset):
    return app.state.settings_store.current().settings.to_milp_params(n_people=len(dataset.graph.people),
                                                                         months=horizon_months_of(dataset.graph))


def _entries_of(dataset) -> list[dict]:
    return [{"person_id": c.person_id, "project_id": c.project_id, "alloc": c.alloc} for c in dataset.current]


@router.post("/api/workspace/apply", dependencies=[Depends(require_admin)])
async def post_apply(body: ApplyIn, request: Request) -> dict:
    dataset = request.app.state.dataset
    check_dataset_version(body.dataset_version, dataset)
    if any(e.monthly_alloc for e in body.entries):
        raise HTTPException(status_code=422, detail="달별 투입률이 있는 명단은 확정할 수 없다(기간 내내 한 비율만)")
    ids_p, ids_j = dataset.graph.pid_index, dataset.graph.project_index
    bad = sorted({e.person_id for e in body.entries if e.person_id not in ids_p}
                 | {e.project_id for e in body.entries if e.project_id not in ids_j}
                 | {p for p in list(body.headcount_add) + list(body.budget_add) + list(body.skills_add) if p not in ids_j}
                 | {g.person_id for g in body.concurrency_add if g.person_id not in ids_p})
    if bad:
        raise HTTPException(status_code=422, detail=f"이 데이터에 없는 ID: {', '.join(bad[:5])}")
    before = _files(request)
    files, changes = _edit(apply_entries, before, [e.model_dump(exclude={"monthly_alloc"}) for e in body.entries],
                           headcount_add={p: dict(v) for p, v in body.headcount_add.items()},
                           budget_add=dict(body.budget_add),
                           skills_add={p: [s.model_dump() for s in v] for p, v in body.skills_add.items()})
    if body.concurrency_add:
        # 동시 수행으로 지원하는 사람의 허용(사유)을 같은 판에 함께 적는다 -- 되돌리면 둘 다 사라진다(설계 3.5).
        # 이미 그만큼 허용된 사람은 기존 기록(사유·메모·시각)을 그대로 둔다(폴백 리뷰 P2: 기본값으로 덮어썼다)
        from datetime import date
        existing = {e["person_id"]: e for e in concurrency_of(read_manifest(files))}
        for g in body.concurrency_add:
            if existing.get(g.person_id, {}).get("max_projects", 1) >= g.max_projects:
                continue
            files = _edit(set_concurrency, files, g.person_id, True, max_projects=g.max_projects, reason=g.reason,
                          memo=g.memo, granted_at=date.today().isoformat())
    if not any(changes.values()) and not (body.headcount_add or body.budget_add or body.skills_add):
        raise HTTPException(status_code=422, detail="바뀐 배치가 없다")
    old = _violations(request.app, dataset, _entries_of(dataset))

    def check(candidate) -> None:
        # 확정으로 새로 생기거나 더 나빠진 위반이 있으면 켜지 않는다(기존 위반이 악화되는 것도 막는다 -- Codex 평가 지적)
        new = []
        for key, v in _violations(request.app, candidate, _entries_of(candidate)).items():
            o = old.get(key)
            # 위반 크기 = 한도와의 거리(상한 위반은 넘친 만큼, 하한 위반은 모자란 만큼) -- actual만 보면 최소 투입률처럼
            # 하한 위반은 방향이 반대라 악화를 통과시켰다(Codex 리뷰 2026-10-19)
            if o is None or abs(v.actual - v.limit) > abs(o.actual - o.limit) + 1e-9:
                new.append(asdict(v))
        if new:
            raise HTTPException(status_code=422, detail={"message": "확정하면 새 위반이 생긴다 -- 확정하지 않았다.",
                                                         "violations": new[:20]})
    note = body.note or {"auto": "자동 편성 확정", "manual": "손 편성 확정", "emergency": "긴급 투입 확정",
                         "plan": "연간 계획 확정"}[body.source]
    out = await commit(request, before, files, kind=f"apply_{body.source}", note=note,
                       summary={**changes, "headcount_add": body.headcount_add, "budget_add": body.budget_add,
                                "skills_add": {p: [s.name for s in v] for p, v in body.skills_add.items()},
                                "concurrency_add": [g.model_dump() for g in body.concurrency_add],
                                # 긴급 투입만 -- 다른 확정의 검토 자료에는 상황 메모 칸이 없어 보이지 않는 값으로 남는다(폴백 리뷰 S1)
                                "situation": body.situation.strip() if body.source == "emergency" else ""},
                       check=check)
    return await _done(request, out, changes=changes)


class UndoIn(_Versioned):
    pass


@router.post("/api/workspace/undo", dependencies=[Depends(require_admin)])
async def post_undo(body: UndoIn, request: Request) -> dict:
    check_dataset_version(body.dataset_version, request.app.state.dataset)
    out = await undo(request)
    return await _done(request, out)


@router.get("/api/skills/dictionary")
def skills_dictionary() -> list[dict]:
    from core.ingest.skills import load_dictionary
    d = load_dictionary()
    return [{"name": c.get("label"), "aliases": list(c.get("aliases") or []), "category": c.get("category")}
            for c in d.concepts.values()]


__all__ = ["router", "workspace_view", "CURRENT"]
