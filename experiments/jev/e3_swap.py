"""E3. Jev가 사람의 판단을 보조할 수 있나 -- "이 사람이 빠지면 후보 k명 중 누구로 바꿀까".

교체 검토 화면에서 담당자가 하는 판단이다. Plan A(서비스 솔버 결과)에서 배치 하나를 고르고, 같은
등급·그 프로젝트에 없으면서 **교체해도 규칙 위반이 생기지 않는** 사람 k명을 무작위(시드 고정)로 후보로
둔다(숫자 조건은 코드가 거른다). 정답은 현행 평가기로 교체 후 명단
전체를 다시 평가했을 때 목적값이 가장 높은(위반 없는) 후보다 -- 즉 "우리 모델 기준의 최선"이다.

Jev에게는 E1과 같은 종류의 정보(요구 기술, 후보의 레벨·단가, 남는 팀원과의 협업 개월, 빠지는 사람의
레벨)를 준다. 우리 점수 숫자는 주지 않는다.
비교: 무작위(기대 적중률 1/k), 기술 점수 1등(S), Jev.
지표: 1등 적중률(동점 1등 인정, Wilson 95% 구간), 고른 후보의 평균 순위, 최선 대비 목적값 손실(후회)."""
from __future__ import annotations

import random

from core.evaluate.plan_eval import evaluate_plan
from core.optimize.alternatives import generate_plans
from core.optimize.types import AssignEntry
from experiments.jev.client import JevClient, choice
from experiments.jev.common import Instance


def build_cases(inst: Instance, n_cases: int, k: int, seed: int) -> tuple[list[AssignEntry], list[dict]]:
    plan = generate_plans(inst.graph, inst.S, inst.C, inst.params, 0)[0]
    entries = plan.entries
    rng = random.Random(seed)
    g = inst.graph
    cases = []
    for idx in rng.sample(range(len(entries)), min(n_cases, len(entries))):
        e = entries[idx]
        out_i = g.pid_index[e.person_id]
        on_project = {x.person_id for x in entries if x.project_id == e.project_id}
        pool = [p.id for p in g.people if p.grade == g.people[out_i].grade and p.id not in on_project]
        # 숫자 조건(가용률·예산·동시 프로젝트)은 코드가 먼저 거른다 -- 교체해도 위반이 생기지 않는 사람만
        # 보기로 준다. 실제 화면도 들어갈 수 없는 사람을 후보로 권하지 않는다(E1과 같은 원칙).
        feasible = []
        for c in rng.sample(pool, len(pool)):
            swapped = [x if x is not e else AssignEntry(person_id=c, project_id=e.project_id, alloc=e.alloc)
                       for x in entries]
            ev = evaluate_plan(g, inst.S, inst.C, inst.params, swapped)
            if not ev.violations and _concurrency_ok(g, inst.params, swapped):
                feasible.append({"id": c, "objective": ev.objective.total, "violations": 0})
            if len(feasible) == k:
                break
        if len(feasible) < k:
            continue
        cases.append({"project": e.project_id, "out": e.person_id, "alloc": e.alloc, "candidates": feasible})
    return entries, cases


def _concurrency_ok(g, params, entries) -> bool:
    months = {p.id: p.months for p in g.projects}
    count: dict = {}
    for x in entries:
        for m in months[x.project_id]:
            count[(x.person_id, m)] = count.get((x.person_id, m), 0) + 1
    return max(count.values(), default=0) <= params.max_concurrent_projects


def _ranked(case) -> list[str]:
    """정답 순위: 위반 없는 후보가 먼저, 그 안에서 목적값 높은 순."""
    return [c["id"] for c in sorted(case["candidates"], key=lambda c: (c["violations"] > 0, -c["objective"]))]


def _describe(inst: Instance, entries, case) -> tuple[str, dict]:
    g = inst.graph
    j = g.project_index[case["project"]]
    proj = g.projects[j]
    req = ", ".join(f"{r.skill} 레벨{r.min_level} 이상 {r.headcount}명" for r in proj.requirements)
    team = [g.pid_index[x.person_id] for x in entries if x.project_id == case["project"] and x.person_id != case["out"]]
    out_p = g.people[g.pid_index[case["out"]]]
    out_sk = ", ".join(f"{r.skill} {out_p.skills.get(r.skill, 0)}" for r in proj.requirements)
    state = (f"프로젝트 {proj.id}: {proj.sector}, {proj.start_month}~{proj.end_month}월, 월 예산 {proj.monthly_budget}.\n"
             f"요구 기술: {req}.\n"
             f"빠지는 사람 {out_p.id}: {out_p.grade.value}, 월 단가 {out_p.monthly_rate}, 투입률 {case['alloc']}, "
             f"요구 기술 레벨({out_sk}).\n남는 팀원: {', '.join(g.people[t].id for t in team)}.")
    options = {}
    for c in case["candidates"]:
        i = g.pid_index[c["id"]]
        pe = g.people[i]
        sk = ", ".join(f"{r.skill} {pe.skills.get(r.skill, 0)}" for r in proj.requirements)
        co = [f"{g.people[t].id}와 {int(g.cowork_months[i, t])}개월" for t in team if g.cowork_months[i, t]]
        options[pe.id] = f"{pe.grade.value}, 월 단가 {pe.monthly_rate}, 요구 기술 레벨({sk})" + (
            ", 협업 이력 " + "·".join(co) if co else "")
    return state, options


TIE = 1e-9


def _tied_best(case) -> set[str]:
    best = max(c["objective"] for c in case["candidates"])
    return {c["id"] for c in case["candidates"] if c["objective"] >= best - TIE * max(1.0, abs(best))}


def _rank(case, chosen: str) -> int:
    """동점은 같은 순위(1 + 고른 후보보다 확실히 나은 후보 수)."""
    got = next(c for c in case["candidates"] if c["id"] == chosen)["objective"]
    return 1 + sum(c["objective"] > got + TIE * max(1.0, abs(got)) for c in case["candidates"])


def _wilson(p: float, n: int, z: float = 1.96) -> list[float]:
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _summary(picks: list[tuple[dict, str]]) -> dict:
    if not picks:
        return {"n": 0, "top1": None, "top1_ci95": None, "mean_rank": None, "mean_regret": None}
    hits = sum(chosen in _tied_best(case) for case, chosen in picks)
    ranks = [_rank(case, chosen) for case, chosen in picks]
    regrets = [max(c["objective"] for c in case["candidates"])
               - next(c for c in case["candidates"] if c["id"] == chosen)["objective"] for case, chosen in picks]
    n = len(picks)
    return {"n": n, "top1": hits / n, "top1_ci95": _wilson(hits / n, n), "mean_rank": sum(ranks) / n,
            "mean_regret": sum(regrets) / n}


def _random_expected(cases, k: int) -> dict:
    """무작위로 고를 때의 기댓값(사례마다 동점 1등 수 / k, 순위는 후보 순위의 평균)."""
    if not cases:
        return {"top1": None, "mean_rank": None}
    top1 = sum(len(_tied_best(c)) / k for c in cases) / len(cases)
    rank = sum(sum(_rank(c, x["id"]) for x in c["candidates"]) / k for c in cases) / len(cases)
    return {"top1": top1, "mean_rank": rank}


def run(inst: Instance, client: JevClient | None, n_cases: int = 1000, k: int = 4, seed: int = 7) -> dict:
    """후보 수가 k에 못 미치는 배치는 건너뛴다(그래서 실제 사례 수가 n_cases보다 적을 수 있다)."""
    entries, cases = build_cases(inst, n_cases, k, seed)
    g = inst.graph
    out = {"instance": inst.name, "k": k, "n_cases": len(cases), "methods": {}}
    out["methods"]["random_expected"] = _random_expected(cases, k)
    out["tied_best_cases"] = sum(len(_tied_best(c)) > 1 for c in cases)
    s_best = [(c, max(c["candidates"], key=lambda x: inst.S[g.pid_index[x["id"]], g.project_index[c["project"]]])["id"])
              for c in cases]
    out["methods"]["skill_best"] = _summary(s_best)
    if client is not None:
        picks, lat, toks = [], [], 0
        for c in cases:
            state, options = _describe(inst, entries, c)
            ans = client.ask(state, {"replacement": choice(
                "빠지는 사람을 대신할 가장 적합한 후보. 요구 기술 충족, 남는 팀원과의 협업, 예산을 고려하라.", options)})
            chosen = ans.answers["replacement"]["choice"]
            if chosen not in options:
                raise ValueError(f"Jev가 후보 밖의 값을 골랐다: {chosen!r}")
            picks.append((c, chosen))
            lat.append(ans.latency_s)
            toks += ans.input_tokens
        out["methods"]["jev"] = {**_summary(picks), "mean_latency_s": (sum(lat) / len(lat)) if lat else None,
                                 "input_tokens": toks}
    out["cases"] = cases
    return out
