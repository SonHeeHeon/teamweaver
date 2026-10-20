"""등급 대체(2026-10-21 사용자 결정: "위 아래 모두 허용하고, 사유는 전부 받고, 예산을 넘으면 증액을 함께 적자").

손 편성 확정(신규 사업 넣기·긴급 투입)에서 어떤 등급이 정원을 넘고 같은 사업에 다른 등급 빈자리가 있으면, 그 빈자리를
넘친 등급으로 **대체**한다: 데이터의 정원을 자리 등급 −n, 들어간 등급 +n으로 바꾼다. 계산 모델(MILP·평가기·독립 검증기)은
그대로다 -- 대체가 끝나면 데이터상 정원이 바뀐 것이라 모든 계산이 같은 규칙으로 맞다. 자동 편성은 대체하지 않는다(모자란 자리는
빈자리로 보인다 -- 부족 신호를 점수 속에 묻지 않는다, 설계 `.omc/plan/2026-10-21-grade-substitution.md`).

화면 미리 계산(`api/routes/scenarios`)과 확정 검증(`api/routes/workspace.post_apply`)이 이 함수 하나를 쓴다 -- 둘이 다르면
화면이 "대체 1건"이라 하고 확정이 거절한다."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from core.domain.models import Grade

ORDER: tuple[str, ...] = tuple(g.value for g in Grade)          # 특급 → 초급(위 → 아래)


def team_grades(entries: Iterable, project_id: str, grade_of: Mapping[str, str]) -> dict[str, int]:
    """그 사업 팀의 등급별 인원(같은 사람은 한 번)."""
    seen: set[str] = set()
    out: dict[str, int] = {}
    for e in entries:
        pid = e.person_id if hasattr(e, "person_id") else e["person_id"]
        jid = e.project_id if hasattr(e, "project_id") else e["project_id"]
        if jid != project_id or pid in seen:
            continue
        seen.add(pid)
        g = grade_of[pid]
        out[g] = out.get(g, 0) + 1
    return out


def plan_swaps(need: Mapping[str, int], before: Mapping[str, int], after: Mapping[str, int]) -> list[dict]:
    """이 확정으로 **새로 넘친** 등급을 확정 뒤 **모자란** 등급 자리에 짝짓는다 → [{seat, grade, count}] (seat = 원래 자리 등급,
    grade = 들어간 사람 등급). 정원 표에 없는 등급은 원래 자유라 넘침이 아니다. 확정 전부터 넘쳐 있던 만큼은 이 확정 탓이 아니라
    빼고 센다. 가까운 등급부터 짝짓고(중급 자리는 고급·초급이 특급보다 먼저), 거리가 같으면 위 등급 자리 먼저 -- 결정적이다."""
    over = {g: max(0, max(0, after.get(g, 0) - n) - max(0, before.get(g, 0) - n)) for g, n in need.items()}
    short = {g: max(0, n - after.get(g, 0)) for g, n in need.items()}
    pairs = sorted(((s, g) for s in ORDER for g in ORDER if s != g and s in need and g in need),
                   key=lambda sg: (abs(ORDER.index(sg[0]) - ORDER.index(sg[1])), ORDER.index(sg[0]), ORDER.index(sg[1])))
    out: list[dict] = []
    for seat, grade in pairs:
        k = min(short.get(seat, 0), over.get(grade, 0))
        if k > 0:
            out.append({"seat": seat, "grade": grade, "count": k})
            short[seat] -= k
            over[grade] -= k
    return out


def swapped_need(need: Mapping[str, int], swaps: Iterable[Mapping]) -> dict[str, int]:
    """대체를 적용한 정원(자리 등급 −n, 들어간 등급 +n)."""
    out = dict(need)
    for s in swaps:
        out[s["seat"]] = out.get(s["seat"], 0) - int(s["count"])
        out[s["grade"]] = out.get(s["grade"], 0) + int(s["count"])
        if out[s["seat"]] < 0:
            raise ValueError(f"{s['seat']} 정원보다 많이 대체할 수 없다")
    return out


def same_swaps(a: Iterable[Mapping], b: Iterable[Mapping]) -> bool:
    """사유를 뺀 (자리, 등급, 수) 목록이 같은가(순서 무관)."""
    key = lambda xs: sorted((x["seat"], x["grade"], int(x["count"])) for x in xs)   # noqa: E731
    return key(a) == key(b)


def graph_with_seats(graph, project_id: str, need: Mapping[str, int], budget_add: int = 0):
    """대상 사업의 등급 정원을 need로 바꾸고 월 예산을 더한 그래프(사람·기술·협업은 그대로). need의 등급만 정원식이 된다."""
    projects = []
    for p in graph.projects:
        if p.id == project_id:
            p = p.model_copy(update={"grade_headcount": {Grade(g): int(n) for g, n in need.items()},
                                     "monthly_budget": p.monthly_budget + int(budget_add)})
        projects.append(p)
    # 최근 협업 개월 캐시(cowork_within)는 사람·협업 이력만 보고 사업과 무관하다 -- 후보마다 새로 세지 않게 같이 쓴다
    return replace(graph, projects=projects, _within_cache=graph._within_cache)


def overrun(graph, project_id: str, entries: Iterable) -> int:
    """그 사업의 월 비용이 예산을 넘는 만큼(올림, 없으면 0). 전일 투입·투입률 방식 모두 alloc × 단가."""
    import math
    j = graph.projects[graph.project_index[project_id]]
    rate = {p.id: p.monthly_rate for p in graph.people}
    cost = 0.0
    for e in entries:
        jid = e.project_id if hasattr(e, "project_id") else e["project_id"]
        if jid == project_id:
            pid = e.person_id if hasattr(e, "person_id") else e["person_id"]
            cost += rate[pid] * float(e.alloc if hasattr(e, "alloc") else e["alloc"])
    return max(0, math.ceil(cost - j.monthly_budget - 1e-9))
