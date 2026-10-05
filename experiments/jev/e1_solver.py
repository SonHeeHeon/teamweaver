"""E1. Jev가 솔버를 대신할 수 있나 -- 빈 자리마다 "누가 좋은가"를 Jev에게 고르게 해 팀을 짠다.

공정 비교를 위해 모든 방법이 **같은 자리 채우기 절차**를 쓰고 "누구를 고르는가"만 다르다.
- 자리: 프로젝트를 시작 달 순서로, 등급 정원만큼.
- 후보: 그 등급이고, 아직 그 프로젝트에 없고, 프로젝트 기간 내내 남은 가용률 ≥ 최소 투입률, 같은 달
  동시 프로젝트 수가 상한 미만, 남은 예산으로 최소 투입률은 감당되는 사람. 이 산수는 **코드가 한다**
  (Jev 문서: "maths in code").
- 투입률: 남은 가용률·남은 예산 안에서 최대(소수 둘째 자리 내림) -- greedy 기준선과 같은 규칙.
- 고르는 방법: Jev(Choice) / 무작위 / 기술 점수 1등(S) -- 그리고 별도로 MILP Plan A(서비스 솔버).
채점은 전부 현행 평가기(목적 4항·위반·미충원).

Jev에게 주는 정보는 사람이 판단할 때 볼 만한 것이다: 프로젝트 요구 기술·최소 레벨, 후보의 등급·
요구 기술 레벨·월 단가, 이미 뽑힌 팀원과의 협업 개월. 우리 점수(S·C) 숫자는 주지 않는다 -- 주면
Jev가 우리 모델을 베끼는 것이지 판단하는 것이 아니다."""
from __future__ import annotations

import math
import random
import time
from typing import Callable

from core.optimize.alternatives import generate_plans
from core.optimize.types import AssignEntry
from experiments.jev.client import JevClient, choice
from experiments.jev.common import Instance, score_plan

Picker = Callable[..., int]          # (inst, j, team, cands, budget_left) -> 고른 사람 인덱스


def _floor2(v: float) -> float:
    return math.floor(v * 100 + 1e-9) / 100


def fill_slots(inst: Instance, pick: Picker) -> tuple[list[AssignEntry], dict]:
    g, p = inst.graph, inst.params
    people, projects = g.people, g.projects
    remaining = {i: list(pe.availability) for i, pe in enumerate(people)}
    concurrent = {i: [0] * len(pe.availability) for i, pe in enumerate(people)}
    entries: list[AssignEntry] = []
    stats = {"slots": 0, "picked_by_model": 0, "forced_single": 0, "no_candidate": 0}
    for j, proj in sorted(enumerate(projects), key=lambda t: (t[1].start_month, t[1].id)):
        team: list[int] = []
        budget_left = float(proj.monthly_budget)
        for grade, need in sorted(proj.grade_headcount.items(), key=lambda t: t[0].value):
            for _ in range(need):
                stats["slots"] += 1
                cands = [i for i, pe in enumerate(people)
                         if pe.grade == grade and i not in team
                         and min(remaining[i][m] for m in proj.months) >= p.min_alloc - 1e-9
                         and all(concurrent[i][m] < getattr(p, "max_concurrent_projects", 99) for m in proj.months)
                         and pe.monthly_rate * p.min_alloc <= budget_left + 1e-9]
                if not cands:
                    stats["no_candidate"] += 1
                    continue
                if len(cands) == 1:
                    i = cands[0]
                    stats["forced_single"] += 1
                else:
                    i = pick(inst, j, team, cands, budget_left)
                    stats["picked_by_model"] += 1
                free = min(remaining[i][m] for m in proj.months)
                alloc = _floor2(min(1.0, free, budget_left / people[i].monthly_rate))
                if alloc < p.min_alloc - 1e-9:
                    stats["no_candidate"] += 1
                    continue
                for m in proj.months:
                    remaining[i][m] -= alloc
                    concurrent[i][m] += 1
                budget_left -= people[i].monthly_rate * alloc
                team.append(i)
                entries.append(AssignEntry(person_id=people[i].id, project_id=proj.id, alloc=alloc))
    return entries, stats


def pick_skill_best(inst: Instance, j: int, team: list[int], cands: list[int], budget_left: float = 0.0) -> int:
    """우리 S 점수를 직접 쓰는 규칙 -- 채점 기준과 같은 항을 보므로 상한에 가까운 기준선이다."""
    return max(cands, key=lambda i: (inst.S[i, j], -i))


def make_random_picker(seed: int) -> Picker:
    rng = random.Random(seed)
    return lambda inst, j, team, cands, budget_left=0.0: rng.choice(cands)


def _describe_slot(inst: Instance, j: int, team: list[int], cands: list[int],
                   budget_left: float | None = None) -> tuple[str, dict]:
    g = inst.graph
    proj = g.projects[j]
    req = ", ".join(f"{r.skill} 레벨{r.min_level} 이상 {r.headcount}명" for r in proj.requirements)
    lines = [f"프로젝트 {proj.id}: {proj.sector}, 단계 {proj.phase.value}, {proj.start_month}~{proj.end_month}월.",
             f"요구 기술: {req}."]
    if budget_left is not None:
        lines.append(f"월 예산 {proj.monthly_budget} 중 남은 예산 {budget_left:.0f}"
                     f"(아직 채울 자리가 더 있을 수 있다).")
    if team:
        lines.append("이미 뽑힌 팀원: " + ", ".join(g.people[t].id for t in team) + ".")
    options = {}
    for i in cands:
        pe = g.people[i]
        skills = ", ".join(f"{r.skill} {pe.skills.get(r.skill, 0)}" for r in proj.requirements)
        cowork = []
        for t in team:
            months = int(g.cowork_months[i, t])
            if months:
                cowork.append(f"{g.people[t].id}와 {months}개월")
        desc = f"{pe.grade.value}, 월 단가 {pe.monthly_rate}, 요구 기술 레벨({skills})"
        if cowork:
            desc += ", 협업 이력 " + "·".join(cowork)
        options[pe.id] = desc
    return "\n".join(lines), options


def make_jev_picker(client: JevClient, log: list) -> Picker:
    def pick(inst: Instance, j: int, team: list[int], cands: list[int], budget_left: float = 0.0) -> int:
        state, options = _describe_slot(inst, j, team, cands, budget_left)
        ans = client.ask(state, {"pick": choice(
            "이 프로젝트의 빈 자리에 가장 적합한 사람. 요구 기술 레벨을 충족하는 정도, 팀원과의 협업 경험,"
            " 남은 예산으로 남은 자리를 채울 수 있는지를 고려하라.",
            options)})
        chosen = ans.answers["pick"]["choice"]
        log.append({"project": inst.graph.projects[j].id, "options": len(options), "choice": chosen,
                    "confidence": ans.answers["pick"].get("confidence"), "latency_s": ans.latency_s,
                    "input_tokens": ans.input_tokens, "replayed": ans.replayed})
        idx = inst.graph.pid_index.get(chosen)
        if idx is None or idx not in cands:
            raise ValueError(f"Jev가 후보 밖의 값을 골랐다: {chosen!r}")
        return idx
    return pick


def run(inst: Instance, client: JevClient | None, seeds=(1, 2, 3)) -> dict:
    out = {"instance": inst.name, "methods": {}}
    t = time.monotonic()
    plan_a = generate_plans(inst.graph, inst.S, inst.C, inst.params, 0)[0]
    out["methods"]["milp_plan_a"] = {**score_plan(inst, plan_a.entries), "seconds": time.monotonic() - t}
    t = time.monotonic()
    e, st = fill_slots(inst, pick_skill_best)
    out["methods"]["skill_best"] = {**score_plan(inst, e), **st, "seconds": time.monotonic() - t}
    rnd = []
    for s in seeds:
        e, st = fill_slots(inst, make_random_picker(s))
        rnd.append(score_plan(inst, e))
    out["methods"]["random"] = {"objective": sum(r["objective"] for r in rnd) / len(rnd),
                                "objective_runs": [r["objective"] for r in rnd],
                                "unfilled_people": sum(r["unfilled_people"] for r in rnd) / len(rnd),
                                "violations": max(r["violations"] for r in rnd)}
    if client is not None:
        log: list = []
        t = time.monotonic()
        e, st = fill_slots(inst, make_jev_picker(client, log))
        wall = time.monotonic() - t
        out["methods"]["jev"] = {**score_plan(inst, e), **st, "seconds_wall": wall,
                                 "jev_latency_s": sum(x["latency_s"] for x in log),
                                 "input_tokens": sum(x["input_tokens"] for x in log),
                                 "calls": len(log), "mean_options": (sum(x["options"] for x in log) / len(log)) if log else 0,
                                 "picks": log}
    return out
