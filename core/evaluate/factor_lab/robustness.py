"""Unusual-project check (roadmap item 3): does the model treat some kinds of project systematically worse?

A plan is broken down by project traits -- number of required skills, seat count, duration, phase, budget
slack -- and each group gets its fill rate, the mean skill fit of the people placed, and its share of unfilled
seats. A group far below the others is a candidate distortion of the scoring (for example big projects taking
everyone, or one-skill projects scoring higher than five-skill ones for the same people).
"""
from collections import defaultdict

import numpy as np

from core.graph.memory_graph import MemoryGraph


def _bucket_reqs(n: int) -> str:
    return "요구기술 1~2" if n <= 2 else ("요구기술 3~4" if n <= 4 else "요구기술 5+")


def _bucket_seats(n: int) -> str:
    return "정원 1~3" if n <= 3 else ("정원 4~7" if n <= 7 else "정원 8+")


def _bucket_months(n: int) -> str:
    return "기간 1~2개월" if n <= 2 else ("기간 3~4개월" if n <= 4 else "기간 5~6개월")


def _bucket_budget(ratio: float) -> str:
    return "예산 빠듯(<1.0)" if ratio < 1.0 else ("예산 보통(1.0~1.1)" if ratio <= 1.1 else "예산 여유(>1.1)")


def project_traits(graph: MemoryGraph) -> dict[str, dict[str, str]]:
    base_rate = {}
    for p in graph.people:                       # cheapest observed rate per grade = the budget reference
        g = p.grade.value
        base_rate[g] = min(base_rate.get(g, p.monthly_rate), p.monthly_rate)
    out = {}
    for j in graph.projects:
        seats = sum(j.grade_headcount.values())
        need = sum(base_rate.get(g.value, 0) * k for g, k in j.grade_headcount.items()) or 1
        out[j.id] = {"reqs": _bucket_reqs(len(j.requirements)), "seats": _bucket_seats(seats),
                     "months": _bucket_months(j.end_month - j.start_month + 1), "phase": j.phase.value,
                     "budget": _bucket_budget(j.monthly_budget / need)}
    return out


def breakdown(graph: MemoryGraph, S: np.ndarray, entries, unfilled: list[str]) -> dict[str, list[dict]]:
    """trait -> rows {group, projects, seats, filled, fill_rate, mean_fit, unfilled_share}."""
    traits = project_traits(graph)
    pidx, jidx = graph.pid_index, graph.project_index
    placed = defaultdict(list)
    for e in entries:
        placed[e.project_id].append(S[pidx[e.person_id], jidx[e.project_id]])
    short = defaultdict(int)
    for u in unfilled:                            # "J001:고급:1명 미충원"
        pid, _, rest = u.split(":", 2)
        short[pid] += int(rest.split("명")[0])
    total_short = sum(short.values()) or 1
    out = {}
    for trait in ("reqs", "seats", "months", "phase", "budget"):
        acc = defaultdict(lambda: {"projects": 0, "seats": 0, "filled": 0, "fits": [], "short": 0})
        for j in graph.projects:
            g = traits[j.id][trait]
            seats = sum(j.grade_headcount.values()) or max(1, sum(r.headcount for r in j.requirements))
            a = acc[g]
            a["projects"] += 1
            a["seats"] += seats
            # listed seats: the plan's own unfilled count; projects without listed grades: people placed
            a["filled"] += max(0, seats - short[j.id]) if j.grade_headcount else min(len(placed[j.id]), seats)
            a["fits"] += placed[j.id]
            a["short"] += short[j.id]
        out[trait] = [{"group": g, "projects": a["projects"], "seats": a["seats"], "filled": a["filled"],
                       "fill_rate": round(a["filled"] / a["seats"], 3) if a["seats"] else None,
                       "mean_fit": round(float(np.mean(a["fits"])), 3) if a["fits"] else None,
                       "unfilled_share": round(a["short"] / total_short, 3)}
                      for g, a in sorted(acc.items())]
    return out
