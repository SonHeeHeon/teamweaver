"""상황 2(신규 사업 편성) 후보 세 갈래 · 상황 3(긴급 투입) API(2026-10-18, claude-b, 3가지 핵심 상황 설계).

계산은 claude-a의 보강 계산(`core.evaluate.staffing_sim.rank_candidates`·`best_additions`)을 그대로 부른다. 고정(자물쇠)은
현재 배치의 `locked`(사업·사람)로 넘긴다 -- 잠긴 배치의 사람은 "빼 오기" 후보에 나오지 않는다.

- GET  /api/pipeline/{id}/candidates  신규 사업 후보: 쉬는 인력 · 끝나 가는 인력 · 진행 사업에서 빼 오기(+ 이전 사업 팀원 표시)
- POST /api/emergency/plan            긴급 투입: 대기 명단 · 최선 n명 조합 · 후보 순위 · 확정용 증원 값(apply_hint)
- GET  /api/emergency/standby         밤 배치가 뽑아 둔 진행 사업별 대기 명단(없으면 요청한 사업만 계산)

사업 효과는 NOT_CALIBRATED -- 점수 변화는 계산상 값이다.
"""
from __future__ import annotations

import math
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset
from core.domain.models import Grade

router = APIRouter()
NOT_CALIBRATED = "계산상 변화(같은 평가 기준) -- 사업 효과는 검증 전(NOT_CALIBRATED)"


def _light(app):
    sem = getattr(app.state, "scenario_light", None)
    if sem is None:
        sem = app.state.scenario_light = anyio.Semaphore(2)
    return sem


def _params(app, dataset):
    return app.state.settings_store.current().settings.to_milp_params(n_people=len(dataset.graph.people))


def _entries(dataset):
    from core.optimize.types import AssignEntry
    return [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in dataset.current]


def _locked(dataset) -> set[tuple[str, str]]:
    return {(c.person_id, c.project_id) for c in dataset.current if c.locked}


def _names(dataset) -> dict[str, str]:
    from api.routes.justification import display_names
    return display_names(dataset)


def classify(dataset, project_id: str, rows: list[dict], *, predecessor: str | None = None) -> dict[str, list[dict]]:
    """후보 행을 세 갈래로: bench(지금 배치 없음) · ending(지금 사업이 모두 대상 사업 시작 전에 끝남 -- 끝나 가는 인력)
    · pull(그 밖: 진행 사업에 계속 있음). 이전 사업 팀원은 갈래마다 맨 위."""
    g = dataset.graph
    target = g.projects[g.project_index[project_id]]
    ends: dict[str, list[tuple[str, int]]] = {}
    for c in dataset.current:
        if c.project_id == project_id:
            continue
        j = g.projects[g.project_index[c.project_id]]
        ends.setdefault(c.person_id, []).append((c.project_id, j.end_month))
    pred_team = {c.person_id for c in dataset.current if predecessor and c.project_id == predecessor}
    names = _names(dataset)
    out: dict[str, list[dict]] = {"bench": [], "ending": [], "pull": []}
    for r in rows:
        pid = r["person_id"]
        mine = ends.get(pid, [])
        if not mine:
            cat = "bench"
        elif all(end < target.start_month for _, end in mine):
            # 신규 사업 시작 **전에** 지금 사업이 모두 끝나는 사람만 -- 겹치면 빼 와야 하므로 pull(Codex 리뷰 2026-10-19)
            cat = "ending"
        else:
            cat = "pull"
        out[cat].append({**r, "name": names.get(pid, pid), "category": cat, "predecessor_team": pid in pred_team,
                         "current_projects": [{"project_id": p, "end_month_index": e} for p, e in mine]})
    for cat in out:
        out[cat].sort(key=lambda x: (not x["predecessor_team"], -x["delta_total"]))
    return out


def rescore_unchanged(graph, S, C, params, entries, project_id: str, rows: list[dict]) -> list[dict]:
    """보강 계산(rank_candidates)은 대상 사업에 자리·예산을 하나 늘려 채점한다. 신규 사업은 등록한 정원·예산의 **빈자리를 채우는**
    것이므로 바꾸지 않은 사업으로 다시 채점한다 -- 빈자리 감점이 줄어드는 효과가 보이고, 예산을 넘는 후보는 새 위반으로 보인다
    (Codex 리뷰 2026-10-19). 위반이 없는 후보가 앞에 온다."""
    from core.evaluate.staffing_sim import simulate
    from core.optimize.types import AssignEntry
    out = []
    for r in rows:
        sim = simulate(graph, S, C, params, entries,
                       adds=[AssignEntry(person_id=r["person_id"], project_id=project_id, alloc=r["alloc"])],
                       removes=[(r["person_id"], src) for src in r.get("pulled_from", [])])
        out.append({**r, "delta_total": round(sim["delta"]["total"], 4),
                    "delta": {k: round(v, 4) for k, v in sim["delta"].items()},
                    "new_violations": [list(v) for v in sim["new_violations"]], "budget_added": 0})
    out.sort(key=lambda x: (bool(x["new_violations"]), -x["delta_total"], x["person_id"]))
    return out


@router.get("/api/pipeline/{project_id}/candidates")
async def pipeline_candidates(project_id: str, request: Request, dataset_version: str | None = None, top: int = 30,
                              dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    check_dataset_version(dataset_version, dataset)
    if project_id not in dataset.graph.project_index:
        raise HTTPException(status_code=404, detail=f"없는 사업 ID: {project_id}")
    top = max(1, min(int(top), 60))
    from core.evaluate.staffing_sim import rank_candidates
    from core.scoring.engine import ScoringEngine
    params = _params(request.app, dataset)
    predecessor = None
    try:
        from api.workspace import current_files
        from api.workspace_edits import pipeline_of, read_manifest
        predecessor = pipeline_of(read_manifest(current_files(request.app, dataset))).get(project_id, {}).get("predecessor")
    except Exception:                                   # noqa: BLE001 -- 원천 묶음이 없는 데이터(fixture)면 이전 사업 표시만 없다
        predecessor = None

    def go():
        eng = ScoringEngine(dataset.graph)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        entries = _entries(dataset)
        rows = rank_candidates(dataset.graph, S, C, params, entries, project_id, include_pull=True, top=top,
                               locked=_locked(dataset))
        return classify(dataset, project_id, rescore_unchanged(dataset.graph, S, C, params, entries, project_id, rows),
                        predecessor=predecessor)
    async with _light(request.app):
        groups = await anyio.to_thread.run_sync(go)
    return {"dataset_version": dataset.info.version, "project_id": project_id, "predecessor": predecessor,
            **groups, "note": NOT_CALIBRATED}


# --- 긴급 투입 ----------------------------------------------------------------------------------------------------------

class EmergencyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str | None = None
    project_id: str = Field(min_length=1, max_length=32)
    n: int = Field(ge=1, le=5)
    grade: Grade | None = None
    skills: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default=[], max_length=10)
    pull_budget: int = Field(default=0, ge=0, le=5)
    note: str = Field(default="", max_length=300)


def _extra_skills(dataset, project_id: str, skills: list[str]) -> tuple[list[str], list[dict]]:
    """(가중치에 쓸 대표 이름, 사업 요구에 새로 더할 기술). 사전에 있으면 대표 이름으로 바꾼다."""
    from core.ingest.skills import load_dictionary
    d = load_dictionary()
    names = []
    for s in skills:
        c = d.lookup(s)
        names.append(c["label"] if c else s.strip())
    j = dataset.graph.projects[dataset.graph.project_index[project_id]]
    required = {r.skill.strip().lower() for r in j.requirements}
    extra = [{"name": n, "min_months": 12} for n in dict.fromkeys(names) if n.strip().lower() not in required]
    return list(dict.fromkeys(names)), extra


def _augmented(request: Request, dataset, project_id: str, extra: list[dict], n: int):
    """요구에 없는 기술을 사업 요구에 더한 후보 데이터셋(확정 때 같은 요구가 데이터에 들어간다). 없으면 None."""
    if not extra:
        return None
    from api.routes.datasets import validate_and_build
    from api.workspace import current_files, zip_files
    from api.workspace_edits import SKILL_REQS, read_table, rehash, write_table
    files = current_files(request.app, dataset)
    h, rows = read_table(files, SKILL_REQS)
    rows += [{"project_id": project_id, "skill_name": s["name"], "min_experience_months": str(s["min_months"]),
              "headcount": str(n)} for s in extra]
    write_table(files, SKILL_REQS, h, rows)
    rehash(files)
    active, _report, err = validate_and_build(zip_files(files))
    if active is None:
        raise HTTPException(status_code=422, detail=f"기술을 요구에 더한 데이터가 검증을 통과하지 못했다: {err or '검증 오류'}")
    return active


def _apply_hint(graph, project_id: str, entries_before, entries_after, extra: list[dict], n: int) -> dict:
    """확정할 때 데이터에 적을 증원(등급 정원·월 예산)과 더할 기술 -- 적지 않으면 평가기가 정원·예산 초과로 본다."""
    j = graph.projects[graph.project_index[project_id]]
    grade_of = {p.id: p.grade.value for p in graph.people}
    rate = {p.id: p.monthly_rate for p in graph.people}
    team: dict[str, int] = {}
    for e in entries_after:
        if e.project_id == project_id:
            team[grade_of[e.person_id]] = team.get(grade_of[e.person_id], 0) + 1
    need = {g.value: c for g, c in j.grade_headcount.items()}
    headcount_add = {g: c - need.get(g, 0) for g, c in team.items() if c > need.get(g, 0)}
    cost = sum(rate[e.person_id] * e.alloc for e in entries_after if e.project_id == project_id)
    budget_add = max(0, math.ceil(cost - j.monthly_budget - 1e-9))
    return {"headcount_add": {project_id: headcount_add} if headcount_add else {},
            "budget_add": {project_id: budget_add} if budget_add else {},
            "skills_add": {project_id: [{**s, "headcount": n} for s in extra]} if extra else {}}


@router.post("/api/emergency/plan")
async def emergency_plan(body: EmergencyIn, request: Request, dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    check_dataset_version(body.dataset_version, dataset)
    if body.project_id not in dataset.graph.project_index:
        raise HTTPException(status_code=404, detail=f"없는 사업 ID: {body.project_id}")
    if not any(c.project_id == body.project_id for c in dataset.current):
        raise HTTPException(status_code=422, detail="진행 중인 사업(현재 배치가 있는 사업)만 긴급 투입할 수 있다")
    from core.evaluate.staffing_sim import best_additions, rank_candidates
    from core.scoring.engine import ScoringEngine
    params = _params(request.app, dataset)
    names, extra = _extra_skills(dataset, body.project_id, body.skills)

    def go():
        cand = _augmented(request, dataset, body.project_id, extra, body.n)
        try:
            graph = cand.graph if cand is not None else dataset.graph
            eng = ScoringEngine(graph)
            weights = {n: 5 for n in names}
            S, C = eng.skill_matrix(weights), eng.synergy_matrix()
            entries, locked = _entries(dataset), _locked(dataset)
            best = best_additions(graph, S, C, params, entries, body.project_id, body.n, grade=body.grade,
                                  pull_budget=body.pull_budget, locked=locked)
            rows = rank_candidates(graph, S, C, params, entries, body.project_id, include_pull=body.pull_budget > 0,
                                   top=20, locked=locked)
            standby = rank_candidates(graph, S, C, params, entries, body.project_id, include_pull=False, top=5,
                                      locked=locked)
            hint = (_apply_hint(graph, body.project_id, entries, best["entries"], extra, body.n)
                    if best.get("accepted") else None)
            return best, rows, standby, hint
        finally:
            if cand is not None:
                cand.retire()
    async with _light(request.app):
        best, rows, standby, hint = await anyio.to_thread.run_sync(go)
    nm = _names(dataset)
    groups = classify(dataset, body.project_id, rows)
    if best.get("accepted"):
        members_before = {c.person_id for c in dataset.current if c.project_id == body.project_id}
        added = sorted({e.person_id for e in best["entries"] if e.project_id == body.project_id} - members_before)
        best = {**best, "entries": [e.model_dump() for e in best["entries"]],
                "added": [{"person_id": p, "name": nm.get(p, p)} for p in added],
                "new_violations": [list(v) for v in best["new_violations"]]}
    return {"dataset_version": dataset.info.version, "project_id": body.project_id, "skills": names,
            "added_requirements": extra, "best": best,
            "candidates": {"bench": groups["bench"], "spare": [r for r in groups["ending"] + groups["pull"]
                                                               if r["source"] != "pull"],
                           "pull": [r for r in groups["ending"] + groups["pull"] if r["source"] == "pull"]},
            "standby": [{**r, "name": nm.get(r["person_id"], r["person_id"])} for r in standby],
            "apply_hint": hint, "note": NOT_CALIBRATED}


@router.get("/api/emergency/standby")
async def emergency_standby(request: Request, dataset_version: str | None = None, project_id: str | None = None,
                            dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    check_dataset_version(dataset_version, dataset)
    nightly = getattr(request.app.state, "nightly", None)
    saved = nightly.store.read("standby.json") if nightly is not None else None
    if (isinstance(saved, dict) and saved.get("dataset_version") == dataset.info.version
            and saved.get("params") == params_fingerprint(request.app, dataset)):
        projects = saved.get("projects") or {}
        if project_id is None or project_id in projects:
            return {"dataset_version": dataset.info.version, "computed_at": saved.get("computed_at"), "source": "nightly",
                    "projects": projects if project_id is None else {project_id: projects[project_id]}}
    if project_id is None:
        return {"dataset_version": dataset.info.version, "computed_at": None, "source": None, "projects": {}}
    if project_id not in dataset.graph.project_index:
        raise HTTPException(status_code=404, detail=f"없는 사업 ID: {project_id}")
    async with _light(request.app):
        rows = await anyio.to_thread.run_sync(lambda: standby_for(request.app, dataset, project_id))
    return {"dataset_version": dataset.info.version, "computed_at": None, "source": "live", "projects": {project_id: rows}}


def standby_for(app, dataset, project_id: str, top: int = 5) -> list[dict]:
    """진행 사업 하나의 대기 명단: 빼 오지 않고 바로 넣을 수 있는 상위 top명(쉬는 인력·여유 있는 인력)."""
    from core.evaluate.staffing_sim import rank_candidates
    from core.scoring.engine import ScoringEngine
    eng = ScoringEngine(dataset.graph)
    rows = rank_candidates(dataset.graph, eng.skill_matrix({}), eng.synergy_matrix(), _params(app, dataset),
                           _entries(dataset), project_id, include_pull=False, top=top * 3, locked=_locked(dataset))
    nm = _names(dataset)
    # 넣으면 위반이 생기는 사람(동시 사업 수 상한 등)은 "바로 넣을 수 있는 사람"이 아니다(Codex 리뷰 2026-10-19)
    return [{"person_id": r["person_id"], "name": nm.get(r["person_id"], r["person_id"]), "grade": r["grade"],
             "alloc": r["alloc"], "source": r["source"], "delta_total": r["delta_total"]}
            for r in rows if not r.get("new_violations")][:top]


def params_fingerprint(app, dataset) -> str:
    """대기 명단을 만든 설정의 지문 -- 설정이 바뀌면(데이터 버전은 그대로) 밤 결과를 쓰지 않는다(Codex 리뷰 2026-10-19)."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(_params(app, dataset).model_dump(), sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:16]
