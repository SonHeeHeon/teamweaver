"""Outcome simulators: explicit, swappable assumptions about what makes a staffed project go well.

Each Scenario is a *stated assumption*, not a measurement. A plan's simulated outcome is the seat-weighted mean
over projects of

    fill_j * ( alloc-weighted mean contribution_ij  +  synergy_w * mean pair C  -  overfam_w * over-familiar share )
    * coverage_penalty_j * fragmentation_penalty * noise_j

contribution_ij = sum_k w_k * F_k[i, j] (optionally with diminishing returns); fill_j comes from the plan's own
unfilled seats, so the simulator and the plan agree. Noise is drawn per (scenario, seed) and shared by every
candidate (common random numbers).

**Circularity, stated plainly**: T1-T5 are built from the same factor matrices the candidate models mix, so a
candidate that uses factor X will tend to win the assumption that rewards X. They show trade-offs, not truth.
T6-T7 (HELD_OUT) use signals no candidate scores -- per-requirement coverage and splitting one person across
projects -- and are the only fair test of whether a mix generalises.
"""
from dataclasses import dataclass, field

import numpy as np

from core.graph.memory_graph import MemoryGraph


@dataclass(frozen=True)
class Scenario:
    name: str
    label: str                         # Korean description for reports
    weights: dict[str, float]          # factor -> weight in a member's contribution
    synergy_w: float = 0.0             # team mean collaboration score (C)
    overfam_w: float = 0.0             # penalty for pairs that worked together too long
    diminishing: bool = False          # contribution sqrt-scaled (more of a factor helps less)
    noise: float = 0.05                # sd of the per-project multiplicative noise
    coverage_w: float = 0.0            # T6: share of a project's required skills nobody on the team has -> penalty
    fragmentation_w: float = 0.0       # T7: people split over several projects work less efficiently


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("T1", "기술 중심: 요구 기술을 얼마나 채웠는가가 성과를 좌우", {"S": 1.0, "D": 0.3}),
    Scenario("T2", "경험·도메인 중심: 깊은 경력, 최근 사용, 같은 업종 경험", {"S": 0.4, "D": 0.6, "R": 0.4, "M": 0.6}),
    Scenario("T3", "팀 궁합 중심: 함께 잘 일해 본 사람들, 단 너무 오래 붙어 있으면 감점",
             {"S": 0.5, "K": 0.3}, synergy_w=0.8, overfam_w=0.4),
    Scenario("T4", "혼합: 기술·경험·도메인·궁합이 고르게 중요", {"S": 0.6, "D": 0.3, "R": 0.2, "M": 0.3, "G": 0.2},
             synergy_w=0.3, overfam_w=0.2),
    Scenario("T5", "경력 효용 체감: 기술·경험이 일정 수준을 넘으면 추가 효과가 작다", {"S": 0.7, "D": 0.5, "M": 0.3},
             diminishing=True),
    # held-out: signals no candidate model scores
    Scenario("T6", "[검증용] 필수 기술 공백: 요구 기술 중 하나라도 팀에 아는 사람이 없으면 성과가 크게 깎인다",
             {"S": 0.5}, coverage_w=1.0),
    Scenario("T7", "[검증용] 쪼개기 비효율: 한 사람이 여러 사업에 나뉘어 들어가면 효율이 떨어진다",
             {"S": 0.5}, fragmentation_w=0.15),
)
HELD_OUT = ("T6", "T7")


@dataclass
class PlanOutcome:
    total: float
    per_project: dict[str, float] = field(default_factory=dict)
    fill_rate: float = 0.0


def _seats(project) -> int:
    return sum(project.grade_headcount.values()) or max(1, sum(r.headcount for r in project.requirements))


def _short_by_project(unfilled: list[str]) -> dict[str, int]:
    out = {}
    for u in unfilled or []:                     # "J001:고급:1명 미충원"
        pid, _, rest = u.split(":", 2)
        out[pid] = out.get(pid, 0) + int(rest.split("명")[0])
    return out


def simulate(graph: MemoryGraph, F: dict[str, np.ndarray], C: np.ndarray, overfam: set[tuple[int, int]],
             entries: list, scenario: Scenario, seed: int, unfilled: list[str] | None = None) -> PlanOutcome:
    pidx, jidx = graph.pid_index, graph.project_index
    rng = np.random.default_rng([seed, sum(map(ord, scenario.name))])
    noise = rng.normal(1.0, scenario.noise, size=len(graph.projects))
    team = {p.id: [] for p in graph.projects}
    projects_of = {}
    for e in entries:
        team[e.project_id].append((pidx[e.person_id], e.alloc))
        projects_of[e.person_id] = projects_of.get(e.person_id, 0) + 1
    short = _short_by_project(unfilled)
    skills_of = {i: set(p.skills) for i, p in enumerate(graph.people)}
    per, total, w_sum, filled, seats_all = {}, 0.0, 0.0, 0, 0
    for proj in graph.projects:
        j = jidx[proj.id]
        seats = _seats(proj)
        members = team[proj.id]
        if unfilled is not None and proj.grade_headcount:
            got = max(0, seats - short.get(proj.id, 0))      # the plan's own count of filled listed seats
        else:
            got = min(len(members), seats)
        fill = got / seats
        filled += got
        seats_all += seats
        if members:
            idx = [i for i, _ in members]
            alloc = np.array([a for _, a in members])
            contrib = np.array([sum(w * F[k][i, j] for k, w in scenario.weights.items()) for i in idx])
            contrib = contrib / (sum(scenario.weights.values()) or 1.0)
            if scenario.diminishing:
                contrib = np.sqrt(np.clip(contrib, 0, None))
            value = float((contrib * alloc).sum() / alloc.sum())
            pairs = [(a, b) for k, a in enumerate(idx) for b in idx[k + 1:]]
            if pairs and (scenario.synergy_w or scenario.overfam_w):
                value += scenario.synergy_w * float(np.mean([C[a, b] for a, b in pairs]))
                value -= scenario.overfam_w * sum(tuple(sorted((a, b))) in overfam for a, b in pairs) / len(pairs)
            if scenario.coverage_w and proj.requirements:
                have = set().union(*(skills_of[i] for i in idx))
                missing = sum(r.skill not in have for r in proj.requirements) / len(proj.requirements)
                value *= max(0.0, 1.0 - scenario.coverage_w * missing)
            if scenario.fragmentation_w:
                extra = np.mean([projects_of[graph.people[i].id] - 1 for i in idx])
                value *= max(0.0, 1.0 - scenario.fragmentation_w * extra)
        else:
            value = 0.0
        score = fill * value * float(noise[j])
        per[proj.id] = score
        total += seats * score
        w_sum += seats
    return PlanOutcome(total=total / w_sum if w_sum else 0.0, per_project=per,
                       fill_rate=filled / seats_all if seats_all else 0.0)
