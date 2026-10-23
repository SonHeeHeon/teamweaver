"""사업 요구 기술별 충족 인원(2026-10-23 사용자 "신규 사업에 인력을 넣기 하면 … 해당 인력이 요구 기술 중 만족하는 게 있으면 요구 기술의 항목의 배경 색깔이 칠해진다던지").

판정은 점수(S)와 같은 기준이다: 그 기술의 보유 레벨(경력 개월을 레벨 구간으로 바꾼 값, 하위 기술 인정분 포함) ≥ 요구 레벨이면 충족.
검토 자료의 "요구 기술 X 충족 a→b명"(`core.kg.views.project_evidence` -- 같은 개월·같은 레벨 구간)과 같은 수가 나온다.
레벨이 모자란 사람은 따로 센다(below) -- 충족으로 치지 않는다.
"""
from __future__ import annotations


def skill_coverage(graph, project_id: str, entries) -> list[dict]:
    """[{skill, min_level, headcount, met, met_by[], below_by[]}] -- 요구 기술 순서 그대로. entries 중 이 사업 배치만 본다."""
    j = graph.projects[graph.project_index[project_id]]
    team = sorted({e.person_id for e in entries if e.project_id == project_id and e.person_id in graph.pid_index})
    people = [graph.people[graph.pid_index[pid]] for pid in team]
    out = []
    for r in j.requirements:
        met = [p.id for p in people if p.skills.get(r.skill, 0) >= r.min_level]
        below = [p.id for p in people if 0 < p.skills.get(r.skill, 0) < r.min_level]
        out.append({"skill": r.skill, "min_level": r.min_level, "headcount": r.headcount, "met": len(met),
                    "met_by": met, "below_by": below})
    return out
