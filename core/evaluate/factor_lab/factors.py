"""Person x project factor matrices, each scaled to [0, 1].

S  required-skill coverage -- the current model's skill score (core.scoring.engine), kept as the baseline.
D  experience depth: months beyond each requirement's minimum, log-scaled (0 without the skill).
R  recency: exp(-months since the skill was last used / 24), averaged over the project's requirements
   (a missing skill counts as 0, so R also carries "has the skill"; D likewise does not tell "below the
   minimum" from "missing" -- both 0). Domain skills appear in requirements too, so S, M and K overlap.
M  domain match: experience in the project's sector domain (금융 / 그룹사 / 공공 업무), capped at 36 months.
G  grade fit: 1 if the project asks for the person's grade, else 0.3 (unlisted grades may still be picked).
K  continuity proxy: 1 if the person used the project's domain within the last 6 months, else 0. (Synthetic
   bundles have no work still running at the horizon, and the MILP plans every seat from scratch -- real
   continuity of people already on an execution project is a model gap, see docs/model-roadmap.md.)

Raw bundle tables are read for months and recency because the converted Dataset keeps only proxy levels. Rows and columns follow MemoryGraph.pid_index / project_index.
"""
import datetime as dt
import math
from collections import defaultdict

import numpy as np

from core.graph.memory_graph import MemoryGraph
from core.ingest.loader import Bundle
from core.ingest.skills import load_dictionary, person_skill_months
from core.scoring.engine import ScoringEngine

SECTOR_DOMAIN = {"대외금융": "금융 업무", "대내": "그룹사 업무", "대외공공": "공공 업무"}
FACTORS = ("S", "D", "R", "M", "G", "K")
DEPTH_SCALE_MONTHS = 60
RECENCY_HALF_LIFE = 24
DOMAIN_CAP_MONTHS = 36
CONTINUITY_MONTHS = 6


def unfilled_seats(unfilled: list[str]) -> int:
    """솔버의 미충원 문자열("J1:고급:3명 미충원")에서 빈자리 수의 합 -- 문자열 개수(사업·등급 수)를 세면 3석이 1로 보였다(Codex 감사 2026-10-12)."""
    import re
    return sum(int(m.group(1)) if (m := re.search(r":(\d+)명", u)) else 1 for u in unfilled)


def _month_index(d: dt.date) -> int:
    return d.year * 12 + d.month - 1


def compute_factors(graph: MemoryGraph, bundle: Bundle) -> dict[str, np.ndarray]:
    nP, nJ = len(graph.people), len(graph.projects)
    pidx, jidx = graph.pid_index, graph.project_index
    t = bundle.tables
    horizon0 = _month_index(bundle.horizon[0])
    # 기술 이름은 입력 단계(to_dataset)와 같은 사전으로 대표 이름에 모으고 하위 기술 부분 인정도 같은 함수로 -- 원래 이름 그대로
    # 쓰면 ReactJS 보유·React.js 요구가 S에선 같은 기술(1.0)인데 D·R은 0이었다(Codex 감사 2026-10-12)
    sd = load_dictionary()
    months, last = {}, {}
    by_person = defaultdict(list)
    for r in t["person_skills.csv"]:
        by_person[r["person_id"]].append(r)
    for pid, rows in by_person.items():
        sm = person_skill_months([(r["skill_name"], r["experience_months"]) for r in rows], sd)
        for name, m in sm.months.items():
            months[(pid, name)] = m
        for r in rows:
            lu = r.get("last_used_month")
            if isinstance(lu, dt.date):
                key = (pid, sd.canonical(r["skill_name"]))
                last[key] = max(last.get(key, -1), _month_index(lu))
        for name, imp in sm.implied.items():            # 인정분이 깊이를 정한 기술은 최근성도 그 출처 기술의 마지막 사용 시점(Codex 리뷰 2026-10-12)
            if imp["months"] > sm.own.get(name, 0):    # 출처에 날짜가 없으면 최근성도 없다(본인 옛 날짜를 남기지 않는다, 리뷰 2차)
                if (pid, imp["from"]) in last:
                    last[(pid, name)] = last[(pid, imp["from"])]
                else:
                    last.pop((pid, name), None)
    reqs = defaultdict(list)
    for r in t["project_skill_requirements.csv"]:
        reqs[r["project_id"]].append((sd.canonical(r["skill_name"]), r["min_experience_months"]))
    asked_grades = defaultdict(set)
    for r in t["project_grade_requirements.csv"]:
        if r["headcount"] > 0:
            asked_grades[r["project_id"]].add(r["career_grade"])
    ongoing_domains = defaultdict(set)
    for (pid, skill), lu in last.items():
        if skill in SECTOR_DOMAIN.values() and lu is not None and lu >= horizon0 - CONTINUITY_MONTHS:
            ongoing_domains[pid].add(skill)
    grade_of = {p.id: p.grade.value for p in graph.people}

    S = ScoringEngine(graph).skill_matrix({})
    D = np.zeros((nP, nJ)); R = np.zeros((nP, nJ)); M = np.zeros((nP, nJ))
    G = np.full((nP, nJ), 0.3); K = np.zeros((nP, nJ))
    for proj in graph.projects:
        j = jidx[proj.id]
        domain = SECTOR_DOMAIN.get(proj.sector.value)
        rq = reqs.get(proj.id, [])
        for p in graph.people:
            i = pidx[p.id]
            if rq:
                d_vals, r_vals = [], []
                for skill, need in rq:
                    have = months.get((p.id, skill), 0)
                    d_vals.append(min(math.log1p(max(0, have - need)) / math.log1p(DEPTH_SCALE_MONTHS), 1.0)
                                  if have else 0.0)
                    lu = last.get((p.id, skill))
                    r_vals.append(math.exp(-max(0, horizon0 - lu) / RECENCY_HALF_LIFE) if lu is not None else 0.0)
                D[i, j], R[i, j] = float(np.mean(d_vals)), float(np.mean(r_vals))
            if domain:
                M[i, j] = min(months.get((p.id, domain), 0) / DOMAIN_CAP_MONTHS, 1.0)
                K[i, j] = 1.0 if domain in ongoing_domains[p.id] else 0.0
            if grade_of[p.id] in asked_grades.get(proj.id, set()):
                G[i, j] = 1.0
    return {"S": S, "D": D, "R": R, "M": M, "G": G, "K": K}
