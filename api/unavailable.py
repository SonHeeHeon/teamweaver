"""투입할 수 없는 인력(2026-10-19 사용자 지시): 특정 달에 육아휴직 등으로 가용률이 0이면 그 달이 걸린 사업에는 넣을 수 없다.
화면은 이런 사람을 목록에서 빼지 않고 **보이되 고를 수 없게** 하고 사유를 빨간 글씨로 보인다(교체 검토·신규 사업 후보·긴급 투입·쉬는 인력).

계산 쪽(MILP·평가기·보강 계산)은 이미 달마다 가용률을 지킨다 -- 여기서는 화면에 보일 사유만 만든다. 사유 종류는 manifest `leave`
(생성기 휴직·휴가 가정)에서, 없으면 "가용률 0%"로 적는다. 가용률을 깎지 않는 한 달 휴가(사업이 자체 감당)는 막지 않는다.
"""
from __future__ import annotations


def leave_of(manifest: dict | None) -> dict[str, dict]:
    """manifest `leave.people` → {사람: {kind, start, months}}. 형식이 다르면 버린다."""
    people = ((manifest or {}).get("leave") or {}).get("people") if isinstance((manifest or {}).get("leave"), dict) else None
    out: dict[str, dict] = {}
    for x in people if isinstance(people, list) else []:
        if (isinstance(x, dict) and isinstance(x.get("person_id"), str) and isinstance(x.get("kind"), str)
                and isinstance(x.get("start"), str) and type(x.get("months")) is int):
            out[x["person_id"]] = {"kind": x["kind"], "start": x["start"], "months": x["months"]}
    return out


def month_name(index: int, horizon_start: str | None) -> str:
    """계획 달 순번 → "27.1월"(시작 달을 모르면 "4번째 달"). 화면 allocFormat.monthLabel과 같은 모양."""
    if isinstance(horizon_start, str) and len(horizon_start) == 7:
        y, m = int(horizon_start[:4]), int(horizon_start[5:]) - 1 + index
        return f"{(y + m // 12) % 100}.{m % 12 + 1}월"
    return f"{index + 1}번째 달"


def _span(months: list[int], horizon_start: str | None) -> str:
    first, last = month_name(months[0], horizon_start), month_name(months[-1], horizon_start)
    return first if first == last else f"{first[:-1]}~{last}"


def blocked_months(availability: list[float], months) -> list[int]:
    return [m for m in months if 0 <= m < len(availability) and availability[m] <= 0.0]


def reason(person_id: str, months: list[int], leave: dict[str, dict], horizon_start: str | None) -> str:
    kind = (leave.get(person_id) or {}).get("kind")
    what = kind if kind and kind != "한 달 휴가" else "가용률 0%"
    return f"{what} — {_span(months, horizon_start)} 투입 불가"


def unavailable_for(dataset, project_id: str, *, exclude: set[str] = frozenset()) -> list[dict]:
    """그 사업 기간 중 가용률 0인 달이 있는 사람(이미 그 사업에 있는 사람·exclude 제외). 사유는 겹치는 달만 적는다."""
    g = dataset.graph
    j = g.projects[g.project_index[project_id]]
    on = {c.person_id for c in dataset.current if c.project_id == project_id} | set(exclude)
    leave = dataset.scenario.get("leave") or {}
    start = dataset.scenario.get("horizon_start")
    from api.routes.justification import display_names
    names = display_names(dataset)
    out = []
    for p in g.people:
        if p.id in on:
            continue
        months = blocked_months(p.availability, j.months)
        if months:
            out.append({"person_id": p.id, "name": names.get(p.id, p.id), "grade": p.grade.value,
                        "months": months, "reason": reason(p.id, months, leave, start)})
    return out


def horizon_reason(dataset, person_id: str) -> str | None:
    """계획 기간 중 투입할 수 없는 달이 있으면 그 사유(쉬는 인력 목록 표시용)."""
    g = dataset.graph
    if person_id not in g.pid_index:
        return None
    p = g.people[g.pid_index[person_id]]
    months = blocked_months(p.availability, range(len(p.availability)))
    return reason(person_id, months, dataset.scenario.get("leave") or {}, dataset.scenario.get("horizon_start")) if months else None
