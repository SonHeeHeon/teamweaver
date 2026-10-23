"""상황 2(신규 사업 편성) 후보 세 갈래 · 상황 3(긴급 투입) API(2026-10-18, claude-b, 3가지 핵심 상황 설계).

계산은 claude-a의 보강 계산(`core.evaluate.staffing_sim.rank_candidates`·`best_additions`)을 그대로 부른다. 고정(자물쇠)은
현재 배치의 `locked`(사업·사람)로 넘긴다 -- 잠긴 배치의 사람은 "빼 오기" 후보에 나오지 않는다.

- GET  /api/pipeline/{id}/candidates  신규 사업 후보: 쉬는 인력 · 끝나 가는 인력 · 진행 사업에서 빼 오기(+ 이전 사업 팀원 표시)
- POST /api/pipeline/{id}/candidates  같은 후보 + "넣기"로 고른 사람을 팀원으로 친 다시 채점·고른 사람 전체 미리 계산(2026-10-20)
- POST /api/emergency/plan            긴급 투입: 대기 명단 · 최선 n명 조합 · 후보 순위 · 확정용 증원 값(apply_hint)
- GET  /api/emergency/standby         밤 배치가 뽑아 둔 진행 사업별 대기 명단(없으면 요청한 사업만 계산)

사업 효과는 NOT_CALIBRATED -- 점수 변화는 계산상 값이다.
"""
from __future__ import annotations

import math
import threading
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_openai_client_or_none
from api.settings import horizon_months_of
from core.domain.models import Grade

router = APIRouter()
NOT_CALIBRATED = "계산상 변화(같은 평가 기준) -- 사업 효과는 검증 전(NOT_CALIBRATED)"


def _light(app):
    sem = getattr(app.state, "scenario_light", None)
    if sem is None:
        sem = app.state.scenario_light = anyio.Semaphore(2)
    return sem


def _params(app, dataset):
    return app.state.settings_store.current().settings.to_milp_params(n_people=len(dataset.graph.people),
                                                                         months=horizon_months_of(dataset.graph))


def _entries(dataset):
    from core.optimize.types import AssignEntry
    return [AssignEntry(person_id=c.person_id, project_id=c.project_id, alloc=c.alloc) for c in dataset.current]


def split_unfilled(row: dict, penalty: float) -> dict:
    """점수 변화를 빈자리 몫과 나머지로 나눈다(사용자 2026-10-19 "점수 변화는 나눠 보이도록 하자").

    빈자리를 하나 메우면 delta_total에 빈자리 감점(자리당 slack_penalty, 설정 화면에 없음 -- 100)이 통째로 더해져 +101처럼 보였고
    사람끼리의 차이(1~3점)가 묻혔다. unfilled_seats_delta = 메운 자리 수(생기면 음수 -- 운영 중 보강 `routes/operating.py`와 같은
    이름·부호), delta_excl_unfilled = 그 밖의 변화(기술 적합·협업 시너지·익숙한 쌍 감점). delta_total과 순위는 그대로다."""
    d = row.get("delta") or {}
    unfilled = float(d.get("unfilled", 0.0))
    total = float(row["delta_total"] if "delta_total" in row else d.get("total", 0.0))
    return {**row, "unfilled_seats_delta": int(round(unfilled / penalty)) if penalty else 0,
            "delta_excl_unfilled": round(total - unfilled, 4)}


def _locked(dataset) -> set[tuple[str, str]]:
    return {(c.person_id, c.project_id) for c in dataset.current if c.locked}


def _names(dataset) -> dict[str, str]:
    from api.routes.justification import display_names
    return display_names(dataset)


def classify(dataset, project_id: str, rows: list[dict], *, predecessor: str | None = None,
             parent: str | None = None) -> dict[str, list[dict]]:
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
    # 추가 과제(2026-10-19): 상위 진행 사업의 팀원 -- 그 시스템을 아는 사람이라 맨 위에 보인다
    parent_team = {c.person_id for c in dataset.current if parent and c.project_id == parent}
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
                         "parent_team": pid in parent_team,
                         "current_projects": [{"project_id": p, "end_month_index": e} for p, e in mine]})
    for cat in out:
        # 등급 대체 후보(2026-10-21)는 같은 갈래에서 등급이 맞는 후보 뒤 -- 대체는 "어쩔 수 없을 때"
        out[cat].sort(key=lambda x: (not (x["predecessor_team"] or x["parent_team"]), bool(x.get("grade_swap")), -x["delta_total"]))
    return out


def _pair_rules(graph, C, params):
    """평가기(core.evaluate.plan_eval)와 같은 쌍 규칙: 보상 쌍(|C| 상위, `pruned_pairs`) · 익숙한 쌍(감점) · 감점 예외(지금 같은 사업에 함께 있는 쌍)."""
    from core.optimize.milp import _overfamiliar_pairs, familiar_exempt, pruned_pairs
    return (set(pruned_pairs(C, params.pair_keep_ratio, params.max_pairs)),
            _overfamiliar_pairs(graph, params.clique_threshold_months, getattr(params, "clique_window_months", None)),
            familiar_exempt(graph))


def pair_terms(graph, C, params, rules, a: str, b: str, project_id: str) -> list[dict]:
    """두 사람이 같은 사업에 있을 때 계산 점수에 들어가는 쌍 항(2026-10-20 사용자 "협업 점수는 인력간의 점수로 나와야하는거 아닌가?"):
    협업 시너지(보상 쌍만 lam·C) · 익숙한 쌍 감점(−mu, 지금 그 사업에 함께 있으면 예외). 둘 다 아니면 빈 목록 -- 계산에 안 들어가는 쌍이다."""
    reward, penalty, exempt = rules
    i, k = graph.pid_index[a], graph.pid_index[b]
    p, q = min(i, k), max(i, k)
    j = graph.project_index[project_id]
    out = []
    if (p, q) in reward and float(C[p, q]):
        out.append({"kind": "synergy", "value": round(params.lam * float(C[p, q]), 4), "c": round(float(C[p, q]), 4)})
    if (p, q) in penalty and (p, q, j) not in exempt and params.mu:
        out.append({"kind": "familiar", "value": round(-params.mu, 4)})
    return out


def _parts(ev) -> dict:
    o = ev.objective            # core.evaluate.staffing_sim._parts와 같은 항
    return {"total": o.total, "skill": o.skill, "synergy": o.synergy, "overfamiliarity": o.overfamiliarity, "unfilled": o.unfilled}


def _seat_gaps(graph, ev) -> dict[str, int]:
    """평가 결과의 사업별 빈자리 수(등급 합) -- 평가기 shortfalls."""
    out: dict[str, int] = {}
    for s in ev.shortfalls:
        out[s.project_id] = out.get(s.project_id, 0) + int(s.missing)
    return out


def seat_effects(graph, before: dict[str, int], after: dict[str, int]) -> list[dict]:
    """사업별 정원 변화(2026-10-20 사용자 "'1자리 메움','1자리 생김'이 어떤 의미인지 잘 모를거같으니"): change > 0 = 그 사업 빈자리를 채움,
    < 0 = 그 사업에 빈자리가 생김(빼 온 사람의 원래 사업). 합은 unfilled_seats_delta와 같다."""
    out = []
    for pid in sorted(set(before) | set(after)):
        change = before.get(pid, 0) - after.get(pid, 0)
        if change:
            out.append({"project_id": pid, "name": graph.projects[graph.project_index[pid]].name, "change": change})
    return out


def rescore_unchanged(graph, S, C, params, entries, project_id: str, rows: list[dict], *, names: dict[str, str] | None = None
                      ) -> list[dict]:
    """보강 계산(rank_candidates)은 대상 사업에 자리·예산을 하나 늘려 채점한다. 신규 사업은 등록한 정원·예산의 **빈자리를 채우는**
    것이므로 바꾸지 않은 사업으로 다시 채점한다 -- 빈자리 감점이 줄어드는 효과가 보이고, 예산을 넘는 후보는 새 위반으로 보인다
    (Codex 리뷰 2026-10-19). 위반이 없는 후보가 앞에 온다.
    2026-10-20: 사업별 정원 변화(`seat_effects`), 기술·협업 나눔(`skill_delta`·`collab_delta` -- 협업 = 협업 시너지 + 익숙한 쌍 감점, 모든 사업),
    이 사업에서 함께하게 될 사람과의 쌍(`partners`)을 붙인다. entries가 "넣기로 고른 사람"을 포함하면 그 사람들도 팀원으로 친다."""
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import AssignEntry
    names = names or {}
    rules = _pair_rules(graph, C, params)
    team = [e.person_id for e in entries if e.project_id == project_id]
    # 평가는 후보마다 한 번(전은 한 번만) -- staffing_sim.simulate와 같은 넣기·빼기·변화 규칙(simulate는 부를 때마다 전을 다시 채점한다)
    from api.violation_diff import new_or_worse
    base = evaluate_plan(graph, S, C, params, entries)
    base_parts, base_gaps = _parts(base), _seat_gaps(graph, base)
    out = []
    graphs: dict = {}                  # 같은 대체·예산이면 그래프 하나(같은 등급 후보는 대체가 같다)
    for r in rows:
        drop = {(r["person_id"], src) for src in r.get("pulled_from", [])}
        new = [e for e in entries if (e.person_id, e.project_id) not in drop] + [
            AssignEntry(person_id=r["person_id"], project_id=project_id, alloc=r["alloc"])]
        # 등급 대체(2026-10-21): 다른 등급 빈자리를 메우는 후보는 위반이 아니라 대체 -- 확정 뒤 정원·예산으로 채점한다
        g_r, swaps, budget_add = swap_graph(graph, project_id, entries, new, cache=graphs)
        after = evaluate_plan(g_r, S, C, params, new)
        a = _parts(after)
        d = {k: a[k] - base_parts[k] for k in a}
        after_gaps = _seat_gaps(g_r, after)
        # 새로 생기거나 커진 위반 -- 확정(routes/workspace.apply)과 같은 판정(api.violation_diff)
        sim = {"new_violations": sorted({(v.code, v.location) for v in new_or_worse(base.violations, after.violations)})}
        partners = [{"person_id": t, "name": names.get(t, t), **term}
                    for t in team if t != r["person_id"] for term in pair_terms(graph, C, params, rules, r["person_id"], t, project_id)]
        out.append({**r, "delta_total": round(d["total"], 4),
                    "delta": {k: round(v, 4) for k, v in d.items()},
                    "skill_delta": round(d.get("skill", 0.0), 4),
                    "collab_delta": round(d.get("synergy", 0.0) + d.get("overfamiliarity", 0.0), 4),
                    "seat_effects": seat_effects(graph, base_gaps, after_gaps), "partners": partners,
                    "new_violations": [list(v) for v in sim["new_violations"]], "budget_added": budget_add,
                    **({"grade_swap": swaps} if swaps else {})})
    # 위반 없는 후보 → 그중 등급이 맞는 후보 먼저(대체는 "어쩔 수 없을 때" -- 사용자 2026-10-21) → 점수
    out.sort(key=lambda x: (bool(x["new_violations"]), bool(x.get("grade_swap")), -x["delta_total"], x["person_id"]))
    return out


def swap_graph(graph, project_id: str, before, after, *, cache: dict | None = None):
    """손 편성(before → after)의 등급 대체를 적용한 그래프·대체 목록·월 예산 증액(대체가 있을 때 예산을 넘는 만큼 -- 사용자 결정
    "예산을 넘으면 증액을 함께 적자"). 대체가 없으면 (graph, [], 0). 확정(`routes/workspace.post_apply`)이 같은 규칙으로 맞춰 본다."""
    from core.evaluate.grade_swap import graph_with_seats, overrun, plan_swaps, swapped_need, team_grades
    j = graph.projects[graph.project_index[project_id]]
    grade_of = {p.id: p.grade.value for p in graph.people}
    need = {g.value: c for g, c in j.grade_headcount.items()}
    swaps = plan_swaps(need, team_grades(before, project_id, grade_of), team_grades(after, project_id, grade_of))
    if not swaps:
        return graph, [], 0
    budget_add = overrun(graph, project_id, after)
    key = (tuple((s["seat"], s["grade"], s["count"]) for s in swaps), budget_add)
    if cache is not None and key in cache:
        return cache[key], swaps, budget_add
    g = graph_with_seats(graph, project_id, swapped_need(need, swaps), budget_add)
    if cache is not None:
        cache[key] = g
    return g, swaps, budget_add


# 사람 단위 위반(사업 정원·예산은 긴급 투입 확정이 apply_hint로 늘려 메운다 -- 그 둘은 보강 계산의 판정을 그대로 둔다)
PERSON_CODES = ("concurrent_projects", "availability", "alloc_range")


def with_worse(graph, S, C, params, entries, project_id: str, rows: list[dict]) -> list[dict]:
    """보강 계산 행(staffing_sim.rank_candidates -- (종류, 위치)만 비교)에 **커진 기존 사람 단위 위반**을 더한다. 긴급 투입 확정도
    `/api/workspace/apply`의 같은 판정(api.violation_diff.new_or_worse)으로 거절하므로, 화면이 "위반 없음"이라 하고 확정이 422가 되지 않게
    (2026-10-20 Opus 대체 리뷰 S1 -- 신규 사업 후보의 Codex 지적과 같은 문제)."""
    from api.violation_diff import new_or_worse
    from core.evaluate.plan_eval import evaluate_plan
    from core.optimize.types import AssignEntry
    before = [v for v in evaluate_plan(graph, S, C, params, entries).violations if v.code in PERSON_CODES]
    out = []
    for r in rows:
        drop = {(r["person_id"], src) for src in r.get("pulled_from", [])}
        new = [e for e in entries if (e.person_id, e.project_id) not in drop] + [
            AssignEntry(person_id=r["person_id"], project_id=project_id, alloc=r["alloc"])]
        after = [v for v in evaluate_plan(graph, S, C, params, new).violations if v.code in PERSON_CODES]
        # 동시 수행 지원 행(needs_grant)은 확정 때 그 사람의 허용을 함께 적는다 -- 그 사람의 동시 수행 상한은 보강 계산(허용을 친 채점)을 따른다
        worse = {(v.code, v.location) for v in new_or_worse(before, after)
                 if not (r.get("needs_grant") and v.code == "concurrent_projects" and v.location.startswith(f"{r['person_id']}:"))}
        have = {tuple(x) for x in r.get("new_violations", [])}
        out.append({**r, "new_violations": sorted(have | worse)})
    return out


def pair_view(graph, C, params, entries, project_id: str, rows: list[dict], names: dict[str, str]) -> list[dict]:
    """보강 계산 행(rank_candidates)에 기술·협업 몫과 함께하게 될 사람과의 쌍을 붙인다(긴급 투입 후보 -- 신규 사업 후보와 같은 칸,
    2026-10-20 사용자 "협업 점수는 인력간의 점수로 나와야하는거 아닌가?"). 점수 자체는 그대로다."""
    rules = _pair_rules(graph, C, params)
    team = [e.person_id for e in entries if e.project_id == project_id]
    out = []
    for r in rows:
        d = r.get("delta") or {}
        out.append({**r, "skill_delta": round(d.get("skill", 0.0), 4),
                    "collab_delta": round(d.get("synergy", 0.0) + d.get("overfamiliarity", 0.0), 4),
                    "partners": [{"person_id": t, "name": names.get(t, t), **term} for t in team if t != r["person_id"]
                                 for term in pair_terms(graph, C, params, rules, r["person_id"], t, project_id)]})
    return out


class PickIn(BaseModel):
    """"넣기"로 고른 사람(확정 전) -- 후보 행의 값 그대로(투입률·빼 오는 사업)."""
    model_config = ConfigDict(extra="forbid")
    person_id: str = Field(min_length=1, max_length=32)
    alloc: float = Field(gt=0.0, le=1.0)
    pulled_from: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(default=[], max_length=6)


class FillIn(BaseModel):
    """빼 온 자리를 메울 사람(2026-10-21) -- 빼 온 사업(고른 사람의 pulled_from)에 넣는다. 미리 계산의 fill_options에서 고른다."""
    model_config = ConfigDict(extra="forbid")
    person_id: str = Field(min_length=1, max_length=32)
    project_id: str = Field(min_length=1, max_length=32)
    alloc: float = Field(default=1.0, gt=0.0, le=1.0)


class CandidatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str | None = None
    picks: list[PickIn] = Field(default=[], max_length=30)
    fills: list[FillIn] = Field(default=[], max_length=20)
    top: int = Field(default=30, ge=1, le=60)


def _check_picks(dataset, project_id: str, picks: list[PickIn]) -> None:
    """고른 사람이 데이터에 있고, 이 사업에 아직 없고, 빼 오는 사업이 그 사람의 지금 배치이며 잠기지 않았는지(화면이 오래된 경우 422)."""
    g = dataset.graph
    seen: set[str] = set()
    on: dict[str, set[str]] = {}
    for c in dataset.current:
        on.setdefault(c.person_id, set()).add(c.project_id)
    locked = _locked(dataset)
    for pk in picks:
        if pk.person_id not in g.pid_index:
            raise HTTPException(status_code=422, detail=f"없는 사람 ID: {pk.person_id}")
        if pk.person_id in seen:
            raise HTTPException(status_code=422, detail=f"같은 사람을 두 번 골랐다: {pk.person_id}")
        seen.add(pk.person_id)
        if project_id in on.get(pk.person_id, set()):
            raise HTTPException(status_code=422, detail=f"{pk.person_id}은(는) 이미 이 사업에 있다")
        for src in pk.pulled_from:
            if src not in on.get(pk.person_id, set()):
                raise HTTPException(status_code=422, detail=f"{pk.person_id}은(는) {src}에 배치돼 있지 않다 -- 후보를 다시 불러온다")
            if (pk.person_id, src) in locked:
                raise HTTPException(status_code=422, detail=f"{pk.person_id}의 {src} 배치는 고정돼 빼 올 수 없다")


def _check_fills(dataset, project_id: str, picks: list[PickIn], fills: list[FillIn]) -> None:
    """메울 사람이 데이터에 있고, 고른 사람과 겹치지 않고, 빼 온 사업에 넣으며, 그 사업에 아직 없는지(화면이 오래된 경우 422)."""
    g = dataset.graph
    sources = {src for pk in picks for src in pk.pulled_from}
    picked = {pk.person_id for pk in picks}
    on = {(c.person_id, c.project_id) for c in dataset.current}
    seen: set[str] = set()
    for f in fills:
        if f.person_id not in g.pid_index or f.project_id not in g.project_index:
            raise HTTPException(status_code=422, detail=f"없는 ID: {f.person_id}@{f.project_id}")
        if f.person_id in picked or f.person_id in seen:
            raise HTTPException(status_code=422, detail=f"{f.person_id}은(는) 이미 고른 사람이다")
        seen.add(f.person_id)
        if f.project_id == project_id or f.project_id not in sources:
            raise HTTPException(status_code=422, detail=f"{f.project_id}은(는) 사람을 빼 온 사업이 아니다 -- 메울 자리가 없다")
        if (f.person_id, f.project_id) in on:
            raise HTTPException(status_code=422, detail=f"{f.person_id}은(는) 이미 {f.project_id}에 있다")


def _after(base, project_id: str, picks: list[PickIn], fills: list[FillIn] = ()):
    """지금 배치 − 빼 오는 배치 + 고른 사람(대상 사업) + 메울 사람(빼 온 사업)."""
    from core.optimize.types import AssignEntry
    pulled = {(pk.person_id, src) for pk in picks for src in pk.pulled_from}
    return [e for e in base if (e.person_id, e.project_id) not in pulled] + [
        AssignEntry(person_id=pk.person_id, project_id=project_id, alloc=pk.alloc) for pk in picks] + [
        AssignEntry(person_id=f.person_id, project_id=f.project_id, alloc=f.alloc) for f in fills]


def hole_view(graph_before, graph_after, S, C, params, base, after, *, skip: str, locked, names: dict[str, str],
              staffing: set[str] = frozenset()) -> dict:
    """빼 온 자리 결정 재료(2026-10-21): 새 빈자리(확정 검사와 같은 함수 -- 편성 중인 신규 사업 staffing은 빼고)·메울 후보·같은 확정에서 메운 사람."""
    from api.seat_fill import fill_options
    from core.evaluate.seat_holes import fills_of, new_holes
    holes = new_holes(graph_before, graph_after, base, after, staffing)
    return {"holes": holes,
            "fill_options": fill_options(graph_after, S, C, params, after, holes, locked=locked, names=names),
            "fills": [{**f, "name": names.get(f["person_id"], f["person_id"])}
                      for f in fills_of(graph_before, base, after, skip=skip)]}


def draft_summary(graph, S, C, params, base, project_id: str, picks: list[PickIn], names: dict[str, str],
                  fills: list[FillIn] = (), locked=frozenset(), staffing: set[str] = frozenset()) -> dict:
    """고른 사람 전체를 넣었을 때(2026-10-20 사용자 "확정 전에도 '넣기'한 인력에 대해서 자리가 얼마나 충당됐는지 계산"): 이 사업의 등급별 자리,
    사업별 정원 변화, 점수 변화 나눔(기술·협업·빈자리), 새 위반 문장, 이 사업 팀 안에서 계산에 들어가는 쌍(고른 사람이 낀 것만)."""
    from api.routes.justification import with_names
    from api.violation_diff import new_or_worse
    from core.evaluate.grade_swap import budget_raises, graph_with_budgets, substitution
    from core.evaluate.plan_eval import evaluate_plan
    entries_after = _after(base, project_id, picks, fills)
    # 등급 대체(2026-10-21): 고른 사람이 다른 등급 빈자리를 메우면 확정 뒤 정원(대체 적용)·예산(넘친 만큼 증액)으로 판정한다.
    # 빼 온 사업을 다른 등급으로 메운 대체도 함께(side_*) -- 확정 검사(substitution)가 모든 사업을 본다
    g2, swaps_by, budget_by = substitution(graph, base, entries_after)
    # 대체 없이도 새로 넘친 예산은 사유와 함께 증액(2026-10-22 P3-2) -- 확정 검사(routes/workspace._check_budget_raises)와 같은 함수
    raises = budget_raises(graph, g2, base, entries_after)
    g2 = graph_with_budgets(g2, raises)
    swaps, budget_add = swaps_by.get(project_id, []), budget_by.get(project_id, 0)
    before = evaluate_plan(graph, S, C, params, base)
    after = evaluate_plan(g2, S, C, params, entries_after)
    sim = {"entries": entries_after, "delta": {k: v - _parts(before)[k] for k, v in _parts(after).items()}}
    j = g2.projects[g2.project_index[project_id]]
    grade_of = {p.id: p.grade.value for p in graph.people}
    need = {g.value: n for g, n in j.grade_headcount.items()}
    filled: dict[str, int] = {}
    for e in sim["entries"]:
        if e.project_id == project_id:
            filled[grade_of[e.person_id]] = filled.get(grade_of[e.person_id], 0) + 1
    picked = {pk.person_id for pk in picks}
    team = sorted({e.person_id for e in sim["entries"] if e.project_id == project_id})
    rules = _pair_rules(graph, C, params)
    pairs = [{"a": a, "a_name": names.get(a, a), "b": b, "b_name": names.get(b, b), **term}
             for x, a in enumerate(team) for b in team[x + 1:] if a in picked or b in picked
             for term in pair_terms(graph, C, params, rules, a, b, project_id)]
    d = sim["delta"]
    unfilled = float(d.get("unfilled", 0.0))
    return {"seats": {g: {"need": need.get(g, 0), "filled": filled.get(g, 0)} for g in [x.value for x in Grade]
                      if need.get(g, 0) or filled.get(g, 0)},
            # 요구 기술별 충족 인원(고른 사람까지 친 팀, 2026-10-23 사용자 "요구 기술 항목의 배경 색깔이 칠해진다던지")
            "coverage": coverage_view(graph, project_id, sim["entries"], names),
            "seat_effects": seat_effects(graph, _seat_gaps(graph, before), _seat_gaps(g2, after)),
            "delta_total": round(d["total"], 4), "skill_delta": round(d.get("skill", 0.0), 4),
            "collab_delta": round(d.get("synergy", 0.0) + d.get("overfamiliarity", 0.0), 4),
            "unfilled_seats_delta": int(round(unfilled / params.slack_penalty)) if params.slack_penalty else 0,
            # 새로 생기거나 커진 위반(확정과 같은 판정) -- 확정하면 거절될 이유를 미리 보인다
            "new_violations": [with_names(v.message, names) for v in new_or_worse(before.violations, after.violations)],
            "pairs": pairs, "grade_swaps": swaps, "budget_add": budget_add,
            "side_grade_swaps": {j: v for j, v in swaps_by.items() if j != project_id},
            "side_budget_add": {j: v for j, v in budget_by.items() if j != project_id},
            "budget_raises": raises,
            **hole_view(graph, g2, S, C, params, base, entries_after, skip=project_id, locked=locked, names=names,
                        staffing=staffing)}


def coverage_view(graph, project_id: str, entries, names: dict[str, str]) -> list[dict]:
    """요구 기술별 충족 인원(`core.evaluate.skill_coverage` -- 점수와 같은 레벨 기준) + 충족·레벨 부족 사람 이름."""
    from core.evaluate.skill_coverage import skill_coverage
    return [{**r, "met_by": [names.get(x, x) for x in r["met_by"]], "below_by": [names.get(x, x) for x in r["below_by"]]}
            for r in skill_coverage(graph, project_id, entries)]


async def _candidates(request: Request, dataset: ActiveDataset, project_id: str, picks: list[PickIn], top: int,
                      fills: list[FillIn] = ()) -> dict:
    if project_id not in dataset.graph.project_index:
        raise HTTPException(status_code=404, detail=f"없는 사업 ID: {project_id}")
    top = max(1, min(int(top), 60))
    _check_picks(dataset, project_id, picks)
    _check_fills(dataset, project_id, picks, fills)
    from core.evaluate.staffing_sim import rank_candidates
    from core.scoring.engine import ScoringEngine
    params = _params(request.app, dataset)
    predecessor = parent = None
    try:
        from api.workspace import current_files
        from api.workspace_edits import pipeline_of, read_manifest
        entry = pipeline_of(read_manifest(current_files(request.app, dataset))).get(project_id, {})
        predecessor, parent = entry.get("predecessor"), entry.get("parent")
    except Exception:                                   # noqa: BLE001 -- 원천 묶음이 없는 데이터(fixture)면 이전·상위 사업 표시만 없다
        predecessor = parent = None
    names = _names(dataset)
    from api.seat_fill import staffing_ids
    staffing = staffing_ids(request.app, dataset)

    def go():
        eng = ScoringEngine(dataset.graph)
        S, C = eng.skill_matrix({}), eng.synergy_matrix()
        base = _entries(dataset)
        # 고른 사람을 이 사업 팀원으로 넣고(빼 오는 사람은 원래 사업에서 뺀 채) 나머지 후보를 채점한다 -- 협업 쌍·남은 자리가 고른 사람을 따른다
        entries = _after(base, project_id, picks, fills)
        # 고른 사람의 등급 대체를 먼저 적용한 정원·예산에서 나머지 후보를 채점한다(2026-10-21 -- 대체한 자리를 또 빈자리로 세지 않게)
        gp = swap_graph(dataset.graph, project_id, base, entries)[0] if picks else dataset.graph
        rows = rank_candidates(gp, S, C, params, entries, project_id, include_pull=True, top=top,
                               locked=_locked(dataset))
        rows = [split_unfilled(r, params.slack_penalty)
                for r in rescore_unchanged(gp, S, C, params, entries, project_id, rows, names=names)]
        groups = classify(dataset, project_id, rows, predecessor=predecessor, parent=parent)
        draft = (draft_summary(dataset.graph, S, C, params, base, project_id, picks, names, fills, _locked(dataset),
                               staffing) if picks else None)
        return groups, draft, coverage_view(dataset.graph, project_id, base, names)
    async with _light(request.app):
        groups, draft, coverage = await anyio.to_thread.run_sync(go)
    from api.unavailable import unavailable_for
    # 그 사업 기간에 휴직 등으로 넣을 수 없는 사람 -- 후보 계산은 이미 빼므로 화면이 사유와 함께 비활성으로 보인다(2026-10-19)
    return {"dataset_version": dataset.info.version, "project_id": project_id, "predecessor": predecessor, "parent": parent,
            **groups, "draft": draft, "coverage": coverage, "unavailable": unavailable_for(dataset, project_id), "note": NOT_CALIBRATED}


@router.get("/api/pipeline/{project_id}/candidates")
async def pipeline_candidates(project_id: str, request: Request, dataset_version: str | None = None, top: int = 30,
                              dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    check_dataset_version(dataset_version, dataset)
    return await _candidates(request, dataset, project_id, [], top)


@router.post("/api/pipeline/{project_id}/candidates")
async def pipeline_candidates_with_picks(project_id: str, body: CandidatesIn, request: Request,
                                         dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    """"넣기"로 고른 사람을 팀원으로 친 후보 + 고른 사람 전체의 미리 계산(`draft`) -- 확정 전, 데이터를 바꾸지 않는다(2026-10-20)."""
    check_dataset_version(body.dataset_version, dataset)
    return await _candidates(request, dataset, project_id, body.picks, body.top, body.fills)


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
    from core.ingest.skill_mapping import dictionary_of_active
    d = dictionary_of_active(dataset)                 # 이 데이터에서 승인한 기술 이름까지(2026-10-22)
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
    """확정할 때 데이터에 적을 증원(등급 정원·월 예산)과 더할 기술 -- 적지 않으면 평가기가 정원·예산 초과로 본다.
    2026-10-21 등급 대체: 다른 등급 빈자리가 있으면 그 자리를 넘친 등급으로 먼저 메우고(`grade_swaps`, 확정 때 사유를 받는다 --
    예전엔 넘친 등급 정원만 늘려 원래 빈자리가 남았다), 남은 넘침만 증원한다. 확정(`routes/workspace.post_apply`)이 같은 함수로 맞춰 본다."""
    from core.evaluate.grade_swap import plan_swaps, swapped_need, team_grades
    j = graph.projects[graph.project_index[project_id]]
    grade_of = {p.id: p.grade.value for p in graph.people}
    rate = {p.id: p.monthly_rate for p in graph.people}
    team = team_grades(entries_after, project_id, grade_of)
    need0 = {g.value: c for g, c in j.grade_headcount.items()}
    swaps = plan_swaps(need0, team_grades(entries_before, project_id, grade_of), team)
    need = swapped_need(need0, swaps)
    headcount_add = {g: c - need.get(g, 0) for g, c in team.items() if c > need.get(g, 0)}
    cost = sum(rate[e.person_id] * e.alloc for e in entries_after if e.project_id == project_id)
    budget_add = max(0, math.ceil(cost - j.monthly_budget - 1e-9))
    # 빼 온 사업을 다른 등급으로 메운 등급 대체·그로 넘는 예산(2026-10-21) -- 확정 검사(substitution)는 모든 사업을 본다.
    # 예전엔 대상 사업만 적어, 최선 조합이 다른 등급으로 메우면 확정이 "대체가 미리 계산과 다르다"로 거절됐다
    from core.evaluate.grade_swap import substitution
    _, side_swaps, side_budget = substitution(graph, entries_before, entries_after)
    side_swaps = {jid: v for jid, v in side_swaps.items() if jid != project_id}
    side_budget = {jid: v for jid, v in side_budget.items() if jid != project_id}
    all_swaps = {**({project_id: swaps} if swaps else {}), **side_swaps}
    return {"headcount_add": {project_id: headcount_add} if headcount_add else {},
            "budget_add": {**({project_id: budget_add} if budget_add else {}), **side_budget},
            "skills_add": {project_id: [{**s, "headcount": n} for s in extra]} if extra else {},
            **({"grade_swaps": all_swaps} if all_swaps else {})}


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
    nm = _names(dataset)
    from api.seat_fill import staffing_ids
    staffing = staffing_ids(request.app, dataset)

    def go():
        cand = _augmented(request, dataset, body.project_id, extra, body.n)
        try:
            graph = cand.graph if cand is not None else dataset.graph
            eng = ScoringEngine(graph)
            weights = {n: 5 for n in names}
            S, C = eng.skill_matrix(weights), eng.synergy_matrix()
            entries, locked = _entries(dataset), _locked(dataset)
            # 최선 조합은 갭 0 풀이라 운영 중 보강·밤 배치처럼 600초에서 자른다 -- 12개월 자동 한도(최대 900초, 2026-10-19 여유 2배)를
            # 그대로 쓰면 요청 하나가 가벼운 계산 슬롯(_light)을 15분까지 잡는다(폴백 리뷰 SHOULD)
            from api.routes.operating import OPERATING_TIME_LIMIT_MAX
            capped = params.model_copy(update={"time_limit": min(params.time_limit, OPERATING_TIME_LIMIT_MAX)})
            best = best_additions(graph, S, C, capped, entries, body.project_id, body.n, grade=body.grade,
                                  pull_budget=body.pull_budget, locked=locked)
            rows = rank_candidates(graph, S, C, params, entries, body.project_id, include_pull=body.pull_budget > 0,
                                   top=20, locked=locked)
            # 전일 투입이면 아직 허용되지 않은 사람의 "동시 수행으로 지원(원래 사업 유지)" 후보를 따로(needs_grant -- 확정 때 허용을 함께 적는다).
            # 일반 후보와 한 목록에 섞지 않는다(폴백 리뷰 P2: 빼 오기·대기 후보가 밀려났다)
            concurrent = (rank_candidates(graph, S, C, params, entries, body.project_id, top=10, locked=locked,
                                          offer_concurrency=True) if params.allocation_mode == "full" else [])
            # 행마다 확정할 증원(넘친 만큼의 정원·예산 + 더할 요구 기술) -- 화면이 직접 정하면 빈자리가 남거나 예산이 이중으로 늘었다(폴백 리뷰 P2).
            # 이미 허용된 사람의 동시 수행 행(일반 후보 rows의 source="concurrent")에도 붙인다 -- 없으면 화면이 지원 버튼을 못 띄웠다(Codex 리뷰 2026-10-19)
            from core.optimize.types import AssignEntry
            for r in [x for x in rows if x.get("source") == "concurrent"] + concurrent:
                after = entries + [AssignEntry(person_id=r["person_id"], project_id=body.project_id, alloc=r["alloc"])]
                r["apply_hint"] = _apply_hint(graph, body.project_id, entries, after, extra, 1)
            standby = [r for r in with_worse(graph, S, C, params, entries, body.project_id,
                                             rank_candidates(graph, S, C, params, entries, body.project_id, include_pull=False,
                                                             top=10, locked=locked)) if not r["new_violations"]][:5]
            hint = (_apply_hint(graph, body.project_id, entries, best["entries"], extra, body.n)
                    if best.get("accepted") else None)
            rows = [split_unfilled(r, params.slack_penalty) for r in pair_view(graph, C, params, entries, body.project_id,
                                                                                 with_worse(graph, S, C, params, entries,
                                                                                            body.project_id, rows + concurrent), nm)]
            if best.get("accepted"):                 # 최선 조합도 커진 사람 단위 위반을 더한다(확정과 같은 판정)
                from api.violation_diff import new_or_worse
                from core.evaluate.plan_eval import evaluate_plan
                pv = lambda es: [v for v in evaluate_plan(graph, S, C, params, es).violations if v.code in PERSON_CODES]  # noqa: E731
                worse = {(v.code, v.location) for v in new_or_worse(pv(entries), pv(best["entries"]))}
                best = {**best, "new_violations": sorted({tuple(x) for x in best["new_violations"]} | worse)}
            standby = [split_unfilled(r, params.slack_penalty) for r in standby]
            if best.get("accepted"):
                best = split_unfilled(best, params.slack_penalty)
                # 빼 온 자리: 같은 조합에서 누가 메웠나·못 메운 자리(2026-10-21 -- 예전엔 검토 자료에만 나왔다). 못 메운 자리는 확정 때
                # "메우지 않음"과 사유를 받는다(메울 사람을 고르려면 손 편성 -- 최선 조합은 다시 풀지 않는다)
                from core.evaluate.seat_holes import fills_of, graph_for_hint, new_holes
                g_after = graph_for_hint(graph, hint)
                best = {**best, "holes": new_holes(graph, g_after, entries, best["entries"], staffing),
                        "fills": [{**f, "name": nm.get(f["person_id"], f["person_id"])}
                                  for f in fills_of(graph, entries, best["entries"], skip=body.project_id)]}
            return best, rows, standby, hint
        finally:
            if cand is not None:
                cand.retire()
    from api.unavailable import unavailable_for
    async with _light(request.app):
        best, rows, standby, hint = await anyio.to_thread.run_sync(go)
    groups = classify(dataset, body.project_id, rows)
    if best.get("accepted"):
        members_before = {c.person_id for c in dataset.current if c.project_id == body.project_id}
        added = sorted({e.person_id for e in best["entries"] if e.project_id == body.project_id} - members_before)
        best = {**best, "entries": [e.model_dump() for e in best["entries"]],
                "added": [{"person_id": p, "name": nm.get(p, p)} for p in added],
                # 빼 오는 사람 표시 이름(부서장 점검 f7 -- 화면이 사번을 보였다). 실데이터는 사번 그대로(K5)
                **({"diff": {**best["diff"], "moved": [{**m, "name": nm.get(m["person_id"], m["person_id"])}
                                                       for m in best["diff"].get("moved", [])]}}
                   if isinstance(best.get("diff"), dict) else {}),
                "new_violations": [list(v) for v in best["new_violations"]]}
    return {"dataset_version": dataset.info.version, "project_id": body.project_id, "skills": names,
            "added_requirements": extra, "best": best,
            "candidates": {"bench": groups["bench"], "spare": [r for r in groups["ending"] + groups["pull"]
                                                               if r["source"] != "pull"],
                           "pull": [r for r in groups["ending"] + groups["pull"] if r["source"] == "pull"]},
            "standby": [{**r, "name": nm.get(r["person_id"], r["person_id"])} for r in standby],
            "unavailable": unavailable_for(dataset, body.project_id),
            "apply_hint": hint, "note": NOT_CALIBRATED}


class EmergencyPickIn(PickIn):
    """긴급 투입에서 "넣기"로 고른 사람. grant = 원래 사업을 그대로 두고 들어오는 동시 수행 지원(확정 때 허용을 함께 적는다)."""
    grant: bool = False


class EmergencyPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str | None = None
    project_id: str = Field(min_length=1, max_length=32)
    skills: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default=[], max_length=10)
    picks: list[EmergencyPickIn] = Field(min_length=1, max_length=10)
    # "후보 찾기"의 빼 오기 허용 -- 고른 사람을 팀원으로 친 후보 다시 매기기에 쓴다(빼 오기 후보를 낼지)
    pull_budget: int = Field(default=0, ge=0, le=5)
    # 빼 온 자리를 메울 사람(2026-10-21) -- 미리 계산의 fill_options에서 고른다
    fills: list[FillIn] = Field(default=[], max_length=20)


class ParseIn(BaseModel):
    """긴급 투입 "상황을 말로"(2026-10-22) -- 상황 글을 요구(기술·인원·등급)로 읽는다. 사람은 AI에 보내지 않는다."""
    model_config = ConfigDict(extra="forbid")
    dataset_version: str | None = None
    text: str = Field(min_length=1, max_length=300)


@router.post("/api/emergency/parse")
async def emergency_parse(body: ParseIn, request: Request, dataset: ActiveDataset = Depends(get_dataset),
                          client=Depends(get_openai_client_or_none)) -> dict:
    """상황 글 → 요구 초안(`api.rag.request_parse`). AI가 없거나(키 없음·실데이터 외부 차단) 실패하면 사전 별칭 찾기 + 숫자·등급 규칙으로.
    부서장이 확인한 뒤 화면이 그 값으로 후보 찾기(`/api/emergency/plan`)를 부른다 -- 추천은 기존 계산이 한다."""
    check_dataset_version(body.dataset_version, dataset)
    from api.deps import llm_client_for
    from api.rag.justification import justification_settings
    from api.rag.request_parse import parse_request
    from api.routes.justification import ai_model, ai_timeout_s
    from core.config import load_pricing
    from core.ingest.skill_mapping import dictionary_of_active
    use, why = llm_client_for(dataset, client)
    model = ai_model()
    sd = dictionary_of_active(dataset)

    def go():
        c = use.with_options(timeout=ai_timeout_s(model), max_retries=0) if use is not None else None
        return parse_request(body.text, sd, client=c, model=model,
                             reasoning_effort=justification_settings(model)["reasoning_effort"],
                             price=load_pricing().get("models", {}).get(model, {}))
    out = await anyio.to_thread.run_sync(go)
    if use is None:
        out["fallback_reason"] = why
    return {**out, "dataset_version": dataset.info.version, "ai_available": use is not None}


_AUG_LOCK = threading.Lock()
_AUG_KEEP = 4


def _augmented_graph(request: Request, dataset, project_id: str, extra: list[dict], n: int):
    """요구 기술을 더한 그래프(긴급 투입 미리 보기용) -- 고를 때마다 데이터 묶음을 다시 만들지 않게 (판·사업·기술·인원) 키로 몇 개만 둔다
    (대체 리뷰 SHOULD 2026-10-20: 매번 zip 풀기·로드·SQLite까지 다시 돌아 가벼운 계산 자리를 잡았다). 그래프는 메모리 값이라 데이터 묶음은 바로 닫는다."""
    if not extra:
        return dataset.graph
    key = (dataset.info.version, project_id, tuple(sorted((s["name"], int(s["min_months"])) for s in extra)), n)
    cache = getattr(request.app.state, "emergency_aug_graphs", None)
    if cache is None:
        cache = request.app.state.emergency_aug_graphs = {}
    with _AUG_LOCK:
        if key in cache:
            cache[key] = cache.pop(key)                 # 최근 사용을 뒤로
            return cache[key]
    cand = _augmented(request, dataset, project_id, extra, n)
    try:
        graph = cand.graph
    finally:
        cand.retire()
    with _AUG_LOCK:
        cache[key] = graph
        while len(cache) > _AUG_KEEP:
            cache.pop(next(iter(cache)))
    return graph


def emergency_draft(graph, S, C, params, base, project_id: str, picks: list[EmergencyPickIn], extra: list[dict],
                    names: dict[str, str], fills: list[FillIn] = (), locked=frozenset(),
                    staffing: set[str] = frozenset()) -> dict:
    """고른 사람을 넣고 확정 때 적을 증원(`_apply_hint`)을 적용한 상태를 지금과 비교한다(2026-10-20 사용자 "긴급 투입에도 미리 보기").
    확정(`/api/workspace/apply`)과 같은 판정: 증원을 적은 데이터로 평가하고, 새로 생기거나 커진 위반(`new_or_worse`). 동시 수행 지원(grant)은
    그 사람만 같은 달 2곳까지 친다(확정이 허용을 함께 적는 것과 같은 조건)."""
    from dataclasses import replace as dc_replace

    from api.routes.justification import with_names
    from api.violation_diff import new_or_worse
    from core.evaluate.plan_eval import evaluate_plan
    from core.evaluate.seat_holes import graph_for_hint
    after_entries = _after(base, project_id, picks, fills)
    hint = _apply_hint(graph, project_id, base, after_entries, extra, len(picks))
    # 확정 뒤 정원·예산 = 등급 대체(빼 온 사업 포함) + 증원(정원 표에 있는 등급만 -- 예전 graph_with_extra_seats와 같은 규칙)
    g2 = graph_for_hint(graph, hint)
    grants = {pk.person_id for pk in picks if pk.grant}
    if grants:
        g2 = dc_replace(g2, people=[p.model_copy(update={"max_concurrent": max(2, p.max_concurrent)}) if p.id in grants else p
                                    for p in g2.people], _within_cache={})
    before, after = evaluate_plan(graph, S, C, params, base), evaluate_plan(g2, S, C, params, after_entries)
    bp, ap = _parts(before), _parts(after)
    d = {k: ap[k] - bp[k] for k in ap}
    j = g2.projects[g2.project_index[project_id]]
    grade_of = {p.id: p.grade.value for p in graph.people}
    need = {g.value: n for g, n in j.grade_headcount.items()}
    filled: dict[str, int] = {}
    for e in after_entries:
        if e.project_id == project_id:
            filled[grade_of[e.person_id]] = filled.get(grade_of[e.person_id], 0) + 1
    picked = {pk.person_id for pk in picks}
    team = sorted({e.person_id for e in after_entries if e.project_id == project_id})
    rules = _pair_rules(graph, C, params)
    pairs = [{"a": a, "a_name": names.get(a, a), "b": b, "b_name": names.get(b, b), **term}
             for x, a in enumerate(team) for b in team[x + 1:] if a in picked or b in picked
             for term in pair_terms(graph, C, params, rules, a, b, project_id)]
    unfilled = float(d.get("unfilled", 0.0))
    return {"seats": {g: {"need": need.get(g, 0), "filled": filled.get(g, 0)} for g in [x.value for x in Grade]
                      if need.get(g, 0) or filled.get(g, 0)},
            "seat_effects": seat_effects(graph, _seat_gaps(graph, before), _seat_gaps(g2, after)),
            "delta_total": round(d["total"], 4), "skill_delta": round(d.get("skill", 0.0), 4),
            "collab_delta": round(d.get("synergy", 0.0) + d.get("overfamiliarity", 0.0), 4),
            "unfilled_seats_delta": int(round(unfilled / params.slack_penalty)) if params.slack_penalty else 0,
            "new_violations": [with_names(v.message, names) for v in new_or_worse(before.violations, after.violations)],
            "pairs": pairs, "apply_hint": hint, "entries": [e.model_dump() for e in after_entries],
            **hole_view(graph, g2, S, C, params, base, after_entries, skip=project_id, locked=locked, names=names,
                        staffing=staffing),
            "_graph": g2}


@router.post("/api/emergency/preview")
async def emergency_preview(body: EmergencyPreviewIn, request: Request, dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    """긴급 투입 손 편성 미리 계산(확정 전, 데이터를 바꾸지 않는다) -- 고른 사람·증원·위반·쌍과 확정에 쓸 apply_hint."""
    check_dataset_version(body.dataset_version, dataset)
    if body.project_id not in dataset.graph.project_index:
        raise HTTPException(status_code=404, detail=f"없는 사업 ID: {body.project_id}")
    if not any(c.project_id == body.project_id for c in dataset.current):
        raise HTTPException(status_code=422, detail="진행 중인 사업(현재 배치가 있는 사업)만 긴급 투입할 수 있다")
    _check_picks(dataset, body.project_id, body.picks)
    _check_fills(dataset, body.project_id, body.picks, body.fills)
    for pk in body.picks:
        if pk.grant and pk.pulled_from:
            raise HTTPException(status_code=422, detail=f"{pk.person_id}: 동시 수행 지원은 원래 사업에서 빼 오지 않는다")
    from core.scoring.engine import ScoringEngine
    params = _params(request.app, dataset)
    skill_names, extra = _extra_skills(dataset, body.project_id, body.skills)
    nm = _names(dataset)
    from api.seat_fill import staffing_ids
    staffing = staffing_ids(request.app, dataset)

    def go():
        from core.evaluate.staffing_sim import rank_candidates
        graph = _augmented_graph(request, dataset, body.project_id, extra, len(body.picks))
        eng = ScoringEngine(graph)
        S, C = eng.skill_matrix({n: 5 for n in skill_names}), eng.synergy_matrix()
        draft = emergency_draft(graph, S, C, params, _entries(dataset), body.project_id, body.picks, extra, nm,
                                body.fills, _locked(dataset), staffing)
        g2 = draft.pop("_graph")                 # 증원을 적은 그래프 -- 다시 매기기도 확정 뒤 상태를 기준으로(2라운드 리뷰 NIT)
        # 고른 사람을 팀원으로 친 후보 다시 매기기(신규 사업 후보와 같은 방식, 대체 리뷰 SHOULD) -- 최선 조합은 다시 풀지 않는다
        from core.optimize.types import AssignEntry
        after = [AssignEntry(**e) for e in draft["entries"]]
        locked = _locked(dataset)
        rows = rank_candidates(g2, S, C, params, after, body.project_id, include_pull=body.pull_budget > 0, top=20, locked=locked)
        concurrent = (rank_candidates(g2, S, C, params, after, body.project_id, top=10, locked=locked, offer_concurrency=True)
                      if params.allocation_mode == "full" else [])
        rows = [split_unfilled(r, params.slack_penalty)
                for r in pair_view(g2, C, params, after, body.project_id,
                                   with_worse(g2, S, C, params, after, body.project_id, rows + concurrent), nm)]
        return draft, rows
    async with _light(request.app):
        draft, rows = await anyio.to_thread.run_sync(go)
    groups = classify(dataset, body.project_id, rows)
    return {"dataset_version": dataset.info.version, "project_id": body.project_id, "draft": draft,
            "candidates": {"bench": groups["bench"],
                           "spare": [r for r in groups["ending"] + groups["pull"] if r["source"] != "pull"],
                           "pull": [r for r in groups["ending"] + groups["pull"] if r["source"] == "pull"]},
            "note": NOT_CALIBRATED}


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
    params = _params(app, dataset)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    rows = with_worse(dataset.graph, S, C, params, _entries(dataset), project_id,
                      rank_candidates(dataset.graph, S, C, params, _entries(dataset), project_id, include_pull=False,
                                      top=top * 3, locked=_locked(dataset)))
    nm = _names(dataset)
    penalty = params.slack_penalty
    # 넣으면 위반이 생기는 사람(동시 사업 수 상한 등)은 "바로 넣을 수 있는 사람"이 아니다(Codex 리뷰 2026-10-19)
    return [{"person_id": r["person_id"], "name": nm.get(r["person_id"], r["person_id"]), "grade": r["grade"],
             "alloc": r["alloc"], "source": r["source"], "delta_total": r["delta_total"],
             "unfilled_seats_delta": r["unfilled_seats_delta"], "delta_excl_unfilled": r["delta_excl_unfilled"]}
            for r in (split_unfilled(x, penalty) for x in rows) if not r.get("new_violations")][:top]


def params_fingerprint(app, dataset) -> str:
    """대기 명단을 만든 설정의 지문 -- 설정이 바뀌면(데이터 버전은 그대로) 밤 결과를 쓰지 않는다(Codex 리뷰 2026-10-19)."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(_params(app, dataset).model_dump(), sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:16]
