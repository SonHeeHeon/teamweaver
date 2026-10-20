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
- GET    /api/workspace/cancel             확정 한 건 취소 미리 보기(2026-10-21) -- 돌릴 것·건너뛸 것·대기로 둘 사람
- POST   /api/workspace/cancel             확정 한 건 취소 → 새 판(그 뒤 변경은 그대로, git revert처럼)
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
from api.workspace import History, WorkspaceUnavailable, cancelled_keys, commit, current_files, undo
from api.workspace_edits import (CURRENT, PROJECTS, EditError, NewProject, add_project, apply_entries, apply_revert,
                                 concurrency_of, pipeline_of, project_form, read_manifest, read_table, remove_project,
                                 revert_ops, set_concurrency, set_lock, update_project)

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
    # 확정 취소(2026-10-21): 이력의 확정마다 취소됐는지·취소할 수 있는지(직전 판이 있어야 차이를 안다)
    gone = cancelled_keys(history)
    history = [{**e, "cancelled": (e.get("version"), e.get("at")) in gone,
                "cancellable": e.get("kind") in CANCELABLE and (e.get("version"), e.get("at")) not in gone and i < len(history) - 1,
                # 그 확정이 사람을 새로 넣은 사업 -- 신규 사업 화면의 "지금 팀" 옆 확정 취소가 이 사업의 마지막 확정을 찾는다(2026-10-21)
                "projects": sorted(_joined_of(e)), "joined": _joined_of(e)}
               for i, e in enumerate(history)]
    from api.unavailable import horizon_reason
    bench = [person(p, unavailable=horizon_reason(dataset, p)) for p in (m.get("bench") or []) if isinstance(p, str)]
    # 프로젝트 동시 수행 허용 인력(사람 단위, 사유와 함께) -- 화면 목록·해제
    concurrency = [person(e["person_id"], max_projects=e.get("max_projects"), reason=e.get("reason"),
                          memo=e.get("memo") or "", granted_at=e.get("granted_at") or "")
                   for e in concurrency_of(m)]
    return {**base, "editable": True, "horizon_start": horizon[0] if horizon else None,
            "horizon_last": _horizon_last(m), "pipeline": pipeline, "ongoing": ongoing, "bench": bench,
            "concurrency": concurrency, "history": history,
            # 지금 데이터에 미리 계산된 운영 중 비교의 K 목록(시연 미리 계산·밤 배치·편성 기록) -- 화면은 기본 "최대 K"를 이것에 맞춰
            # 예전 기록(K 0~3)을 열었을 때 바로 나오게 한다(2026-10-20 Opus 대체 리뷰 S2). 안내용이라 키 검사는 계산 요청 때 한다
            "operating_ks": sorted(int(k) for k in ((getattr(request.app.state, "precomputed_operating", None) or {}).get("ks") or []))}


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
        raise HTTPException(status_code=422, detail="달별 투입률이 있는 명단은 확정할 수 없다(기간 내내 같은 비율만)")
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
        # 확정으로 새로 생기거나 더 나빠진 위반이 있으면 켜지 않는다(기존 위반이 악화되는 것도 막는다 -- Codex 평가 지적).
        # 판정은 확정 전 미리 계산(routes/scenarios)과 같은 함수(api.violation_diff, 2026-10-20)
        from api.violation_diff import new_or_worse
        new = [asdict(v) for v in new_or_worse(old.values(), _violations(request.app, candidate, _entries_of(candidate)).values())]
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


def _joined_of(entry: dict) -> dict[str, list[str]]:
    """확정이 사업마다 새로 넣은 사람(summary.added "P@J") -- 화면이 "지금 팀"에 아직 남은 사람을 넣은 확정을 고른다(3차 리뷰 S1)."""
    if entry.get("kind") not in CANCELABLE:
        return {}
    out: dict[str, list[str]] = {}
    for a in (entry.get("summary") or {}).get("added") or []:
        if "@" in str(a):
            p, j = str(a).split("@", 1)
            out.setdefault(j, []).append(p)
    return {j: sorted(v) for j, v in sorted(out.items())}


# --- 확정 취소(2026-10-21 사용자 요청 3, 결정: 확정 한 건 통째 · 빼 온 사람은 원래 사업으로 · 긴급 투입 포함) ---------------
# 되돌리기(undo)는 마지막 판만 꺼낸다 -- 확정 뒤 고정·등록을 했으면 그것까지 사라졌다. 취소는 그 확정의 변경만 지금 판 위에서
# 거꾸로 적용한 **새 판**이다(api.workspace_edits.revert_ops: 확정 뒤 바뀐 칸은 건너뜀). 취소도 판이라 되돌리기로 무를 수 있다.

CANCELABLE = ("apply_auto", "apply_manual", "apply_emergency")
MAX_DROP_ROUNDS = 5


def _cancel_target(dataset, version: str, at: str) -> tuple[list[dict], int]:
    entries = History().entries()
    if not entries or entries[-1].get("version") != dataset.info.version:
        raise HTTPException(status_code=409, detail="지금 데이터의 변경 이력이 없다(다른 데이터로 바꿨다).")
    i = next((k for k, e in enumerate(entries) if e.get("version") == version and e.get("at") == at), None)
    if i is None or i == 0:
        raise HTTPException(status_code=404, detail="이 확정은 지금 이력(최근 20판)에 없다.")
    if entries[i].get("kind") not in CANCELABLE:
        raise HTTPException(status_code=422, detail="확정(자동 편성·손 편성·긴급 투입)만 취소할 수 있다.")
    if (version, at) in cancelled_keys(entries):
        raise HTTPException(status_code=409, detail="이미 취소한 확정이다.")
    return entries, i


def _graph_of(files):
    """원천 묶음으로 계산 그래프만 만든다(위반 판정용 -- 평가 글 판정은 위반에 쓰이지 않아 부르지 않는다)."""
    import tempfile
    from pathlib import Path
    from api.datasets import extract_bundle_zip
    from api.workspace import zip_files
    from core.graph.memory_graph import MemoryGraph
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    with tempfile.TemporaryDirectory(prefix="teamweaver-cancel-") as tmp:
        root = extract_bundle_zip(zip_files(files), Path(tmp) / "bundle")
        bundle, report = load_bundle(root)
        try:
            ds, parsed = to_dataset(bundle, report)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"취소한 데이터가 검증을 통과하지 못한다: {exc}") from exc
        return MemoryGraph.build(ds, parsed)


def _violations_of(app, graph, files) -> list:
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import AssignEntry
    from core.scoring.engine import ScoringEngine
    from api.workspace_edits import current_table
    _, rows = current_table(files)
    entries = [AssignEntry(person_id=r["person_id"], project_id=r["project_id"], alloc=float(r["alloc"])) for r in rows
               if r["person_id"] in graph.pid_index and r["project_id"] in graph.project_index]
    params = app.state.settings_store.current().settings.to_milp_params(n_people=len(graph.people),
                                                                        months=horizon_months_of(graph))
    eng = ScoringEngine(graph)
    return list(evaluate_plan(graph, eng.skill_matrix({}), eng.synergy_matrix(), params, entries).violations)


def _implicates(v, o: dict, grade_of: dict[str, str]) -> bool:
    """이 위반을 이 되돌림 작업이 만들었을 수 있는가 -- 위반 종류마다 좁게(폴백 리뷰 S2: 사업 ID만 맞춰 보면 J007 특급 초과 하나로
    J007에 돌아가는 모든 등급의 사람·예산 되돌림이 함께 빠졌다). 확정이 넣은 배치를 빼는 작업(to None)은 위반을 만들지 않는다."""
    loc = v.location.split(":")
    if o["op"] == "assign":
        if o["to"] is None:
            return False
        pid, jid = o["key"].split("@", 1)
        if v.code == "grade_over":
            return loc[0] == jid and grade_of.get(pid) == loc[1]
        if v.code == "budget":
            return loc[0] == jid
        if v.code == "alloc_range":
            return loc[:2] == [pid, jid]
        return loc[0] == pid                    # availability · concurrent_projects 등 사람 위반
    if o["op"] == "grade":
        return v.code == "grade_over" and ":".join(loc[:2]) == o["key"].replace("@", ":")
    if o["op"] == "budget":
        return v.code == "budget" and loc[0] == o["key"]
    if o["op"] == "grant":
        return v.code == "concurrent_projects" and loc[0] == o["key"]
    return False


def cancel_plan(request: Request, version: str, at: str) -> dict:
    """취소 계획: 돌릴 작업·건너뛸 작업·위반 때문에 대기로 둘 작업·남는 새 위반. 미리 보기와 확정이 같은 함수를 쓴다."""
    from api.violation_diff import new_or_worse
    from api.workspace import _read_zip
    app, dataset = request.app, request.app.state.dataset
    entries, i = _cancel_target(dataset, version, at)
    hist = History()
    blobs = [hist.load_zip(str(entries[k].get("content_version"))) for k in (i - 1, i)]
    if any(b is None for b in blobs):
        raise HTTPException(status_code=409, detail="이 확정의 보관 판이 남아 있지 않다(최근 20판만 보관한다).")
    before, after = (_read_zip(b) for b in blobs)
    now = _files(request)
    ops, skipped = _edit(revert_ops, before, after, now)
    if not ops:
        raise HTTPException(status_code=422, detail="되돌릴 것이 없다 -- 이 확정의 변경은 모두 그 뒤 다른 변경으로 바뀌었다.")
    old = _violations_of(app, dataset.graph, now)
    grade_of = {p.id: p.grade.value for p in dataset.graph.people}
    dropped: list[dict] = []
    files, new = now, []
    for round_ in range(MAX_DROP_ROUNDS + 1):
        if not ops:
            break
        files = _edit(apply_revert, before, now, ops)
        new = new_or_worse(old, _violations_of(app, _graph_of(files), files))
        if not new or round_ == MAX_DROP_ROUNDS:
            break
        # 원래 사업 복귀·정원·예산 줄이기·허용 해제 중 그 위반을 만든 것만 하지 않는다(그 사람은 대기로)
        drop = [o for o in ops if any(_implicates(v, o, grade_of) for v in new)]
        if not drop:
            break
        dropped += [{**o, "why": "violation", "violations": [v.message for v in new if _implicates(v, o, grade_of)][:2]}
                    for o in drop]
        ops = [o for o in ops if o not in drop]
    if not ops:                                         # 모두 빠졌으면 바뀌는 것도, 생기는 위반도 없다(2차 리뷰 NIT)
        files, new = now, []
    from api.workspace_edits import current_table
    _, rows = current_table(files)
    assigned = {r["person_id"] for r in rows}
    for d in dropped:                                   # "대기로 남는다"는 정말 배치가 없을 때만(폴백 리뷰 M1)
        d["bench_after"] = d["op"] == "assign" and d["key"].split("@", 1)[0] not in assigned
    return {"entries": entries, "index": i, "before": before, "now": now, "ops": ops, "skipped": skipped,
            "dropped": dropped, "files": files, "new_violations": [asdict(v) for v in new or []],
            # 위반·고정으로 되돌릴 작업이 모두 빠지면 바뀌는 것이 없다 -- 그래도 커밋하면 확정이 "취소됨"으로 잠겼다(폴백 리뷰 M1)
            "nothing": not ops, "version": dataset.info.version}


_DIGIT_RO = {"0": "으로", "1": "로", "2": "로", "3": "으로", "4": "로", "5": "로", "6": "으로", "7": "로", "8": "로", "9": "로"}


def _ro(code: str) -> str:
    """사업 코드 뒤 '(으)로' -- 끝 숫자의 읽는 소리 받침으로 고른다(J015 → J015로, J010 → J010으로)."""
    return code + _DIGIT_RO.get(code[-1:], "(으)로")


def _revert_lines(ops: list[dict], names: dict[str, str]) -> list[str]:
    """돌릴 작업을 사람마다 한 줄로: 확정이 넣은 곳에서 빼고 원래 사업으로 돌아가면 "박시우: J020 → J015로 돌아간다"."""
    people: dict[str, dict[str, list]] = {}
    rest = []
    for o in ops:
        if o["op"] != "assign" or (o["from"] is not None and o["to"] is not None):
            rest.append(_op_text(o, names))
            continue
        pid, jid = o["key"].split("@", 1)
        people.setdefault(pid, {"out": [], "back": []})["out" if o["to"] is None else "back"].append(jid)
    lines = []
    for pid, d in sorted(people.items(), key=lambda kv: names.get(kv[0], kv[0])):
        who = names.get(pid, pid)
        if d["back"]:
            src = f"{', '.join(d['out'])} → " if d["out"] else ""
            lines.append(f"{who}: {src}{_ro(', '.join(d['back']))} 돌아간다(원래 사업)")
        else:
            lines.append(f"{who}: {', '.join(d['out'])}에서 뺀다")
    return lines + rest


def _op_text(o: dict, names: dict[str, str]) -> str:
    n = lambda pid: names.get(pid, pid)                         # noqa: E731
    k = o["op"]
    if k == "assign":
        pid, jid = o["key"].split("@", 1)
        if o["to"] is None:
            return f"{n(pid)}: {jid}에서 뺀다"
        if o["from"] is None:
            return f"{n(pid)}: {_ro(jid)} 돌아간다"
        return f"{n(pid)}: {jid} 투입률 {o['from']:.0%} → {o['to']:.0%}"
    if k == "grade":
        jid, g = o["key"].split("@", 1)
        return f"{jid} {g} 정원 {o['from'] or 0}명 → {o['to'] or 0}명"
    if k == "budget":
        return f"{o['key']} 월 예산 {o['from']:,} → {o['to']:,}"
    if k == "skill":
        jid, _ = o["key"].split("@", 1)
        name = (o["confirmed"] or o["to"] or ("?",))[0]
        return f"{jid} 요구 기술 {name} {'뺀다' if o['to'] is None else '되살린다'}"
    if k == "grant":
        return (f"{n(o['key'])}: 프로젝트 동시 수행 허용 해제" if o["to"] is None
                else f"{n(o['key'])}: 프로젝트 동시 수행 최대 {o['to']}개로")
    return o["key"]


_SKIP_WHY = {"changed": "확정 뒤 다른 변경으로 바뀌어 그대로 둔다", "locked": "확정 뒤 고정해 그대로 둔다(고정을 풀면 취소된다)",
             "gone": "그 사람이나 사업이 지금 데이터에 없어 그대로 둔다"}


def _plan_view(plan: dict, names: dict[str, str]) -> dict:
    t = plan["entries"][plan["index"]]
    dropped = []
    for o in plan["dropped"]:
        msg = "; ".join(o.get("violations") or [])
        dropped.append(f"{_op_text(o, names)} -- 하지 않는다(규칙 위반{': ' + msg if msg else ''})"
                       + (" · 대기 인력으로 남는다" if o.get("bench_after") else ""))
    return {"target": {k: t.get(k) for k in ("version", "kind", "note", "at")},
            "revert": _revert_lines(plan["ops"], names),
            "skipped": [f"{_op_text({**o, 'from': o['confirmed']}, names)} -- {_SKIP_WHY[o['why']]}" for o in plan["skipped"]],
            "dropped": dropped,
            "new_violations": [v["message"] for v in plan["new_violations"]][:10],
            "blocked": ("되돌릴 수 있는 것이 없다 -- 남은 변경은 모두 그 뒤 고정했거나 되돌리면 규칙 위반이다." if plan["nothing"]
                        else "취소하면 새 위반이 생긴다." if plan["new_violations"] else None),
            "ok": not plan["new_violations"] and not plan["nothing"]}


@router.get("/api/workspace/cancel")
async def get_cancel_preview(request: Request, dataset_version: str = Query(...), version: str = Query(...),
                             at: str = Query(...)) -> dict:
    check_dataset_version(dataset_version, request.app.state.dataset)
    plan = await anyio.to_thread.run_sync(cancel_plan, request, version, at)
    return _plan_view(plan, _names(request.app.state.dataset))


class CancelIn(_Versioned):
    target_version: str = Field(min_length=1, max_length=200)
    target_at: str = Field(min_length=1, max_length=64)


@router.post("/api/workspace/cancel", dependencies=[Depends(require_admin)])
async def post_cancel(body: CancelIn, request: Request) -> dict:
    dataset = request.app.state.dataset
    check_dataset_version(body.dataset_version, dataset)
    plan = await anyio.to_thread.run_sync(cancel_plan, request, body.target_version, body.target_at)
    # 계산하는 몇 초 사이에 다른 변경(고정·등록·밤 인사 자료 받기·데이터 전환)이 켜졌으면 옛 판 위의 계획이다 -- 덮어쓰지 않는다
    # (폴백 리뷰 M2). 여기부터 commit의 _begin까지 await가 없다.
    check_dataset_version(body.dataset_version, request.app.state.dataset)
    if plan["version"] != request.app.state.dataset.info.version:
        raise HTTPException(status_code=409, detail="취소를 계산하는 동안 데이터가 바뀌었다 -- 다시 미리 본다.")
    dataset = request.app.state.dataset
    view = _plan_view(plan, _names(dataset))
    if not view["ok"]:
        raise HTTPException(status_code=422, detail={"message": f"{view['blocked']} 취소하지 않았다.",
                                                     "violations": plan["new_violations"][:20]})
    from api.violation_diff import new_or_worse
    old = _violations(request.app, dataset, _entries_of(dataset))

    def check(candidate) -> None:
        # 켜기 전 마지막 확인은 확정과 같은 판정(새로 생기거나 커진 위반이면 켜지 않는다)
        new = [asdict(v) for v in new_or_worse(old.values(), _violations(request.app, candidate, _entries_of(candidate)).values())]
        if new:
            raise HTTPException(status_code=422, detail={"message": "취소하면 새 위반이 생긴다 -- 취소하지 않았다.",
                                                         "violations": new[:20]})
    t = view["target"]
    out = await commit(request, plan["now"], plan["files"], kind="cancel", note=f"확정 취소: {t['note'] or t['kind']}",
                       summary={"target_version": t["version"], "target_at": t["at"], "target_kind": t["kind"], "target_note": t["note"],
                                "revert": view["revert"], "skipped": view["skipped"], "dropped": view["dropped"]},
                       check=check)
    # 취소를 이력 밖에서도 기억한다 -- 20판 밖으로 밀리면 검토 자료가 다시 열려 내보낼 수 있었다(폴백 리뷰 S3). 되돌리기가 지운다
    from api.review_docs import record_cancelled
    record_cancelled(t["version"], t["at"], True)
    return await _done(request, out, cancel=view)
