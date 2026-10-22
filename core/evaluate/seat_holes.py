"""빼 온 자리 결정(2026-10-21 사용자 "빼간 자리를 매꿔야한다는게 있어야할거같네. 못메꾼다면 메꾸지 않는다라는 표기를 통해
더이상 그건 컨트롤 안해도 되는걸로 처리를 해야될거같은데" -- 추천대로: 모든 확정 경로, 예산 그대로, 정원은 사업 전체 기간 단위).

확정으로 **새로 생긴 빈자리**(사업 × 등급)는 같은 확정에서 메우거나(그 사업에 사람을 넣음 -- 다른 등급이면 등급 대체가 그대로 걸린다)
"메우지 않음"(사유)으로 그 등급 정원을 줄인다. 줄인 뒤에는 빈자리가 아니라 모든 계산(MILP·평가기·밤 배치)이 더 메우려 들지 않는다.
계산 모델은 그대로다 -- 등급 대체(`grade_swap`)처럼 데이터 정원이 바뀔 뿐이다.

화면 미리 계산(`api/routes/workspace` preview·`api/routes/scenarios`)과 확정 검사(`routes/workspace._check_seat_holes`)가 이 함수들을 쓴다."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from core.evaluate.grade_swap import ORDER, _teams, graph_with_seats, substitution, team_grades


def new_holes(graph_before, graph_after, current: Iterable, entries: Iterable,
              skip: Iterable[str] = ()) -> dict[str, list[dict]]:
    """지금 배치(current, 지금 정원 graph_before) → entries(확정 뒤 정원 graph_after -- 등급 대체·증원을 적용한 정원)에서 새로 생긴 빈자리
    {사업: [{grade, count}]}. 팀이나 정원이 바뀐 사업만, 등급마다 max(0, 뒤 정원 − 뒤 인원) − max(0, 지금 정원 − 지금 인원)가 양수인 것 --
    확정 전부터 비어 있던 자리는 이 확정 탓이 아니라 빼고 센다. 정원 표에 없는 등급은 빈자리가 없다.
    skip: 아직 편성 중인 신규 사업(manifest pipeline·proposals) -- 시작 전 빈자리는 정상이라 "메우지 않음"(정원 줄이기)을 받지 않는다."""
    current, entries = list(current), list(entries)
    grade_of = {p.id: p.grade.value for p in graph_before.people}
    tb, ta = _teams(current), _teams(entries)
    skip = set(skip)

    def need(graph, jid):
        return {g.value: n for g, n in graph.projects[graph.project_index[jid]].grade_headcount.items()}
    # 정원만 바뀐 사업도 본다 -- 확정 취소가 "메우지 않음" 정원만 되살리면 팀은 그대로인데 빈자리가 생긴다
    changed = {j for j in set(tb) | set(ta) if tb.get(j) != ta.get(j)} | {
        p.id for p in graph_after.projects if p.id in graph_before.project_index and need(graph_after, p.id) != need(graph_before, p.id)}
    out: dict[str, list[dict]] = {}
    for jid in sorted(j for j in changed if j in graph_before.project_index and j in graph_after.project_index and j not in skip):
        n0, n1 = need(graph_before, jid), need(graph_after, jid)
        before, after = team_grades(current, jid, grade_of), team_grades(entries, jid, grade_of)
        holes = [{"grade": g, "count": k} for g in ORDER
                 if (k := max(0, n1.get(g, 0) - after.get(g, 0)) - max(0, n0.get(g, 0) - before.get(g, 0))) > 0]
        if holes:
            out[jid] = holes
    return out


def with_headcount(graph, headcount_add: Mapping[str, Mapping[str, int]] | None):
    """긴급 투입 증원(사업 → 등급 → +n)을 적용한 그래프 -- 확정이 정원 표에 쓰는 것과 같다(없던 등급은 새 행)."""
    for jid, adds in (headcount_add or {}).items():
        if jid not in graph.project_index or not any(n > 0 for n in adds.values()):
            continue
        pj = graph.projects[graph.project_index[jid]]
        need = {g.value: n for g, n in pj.grade_headcount.items()}
        for g, n in adds.items():
            if n > 0:
                need[g] = need.get(g, 0) + int(n)
        graph = graph_with_seats(graph, jid, need)
    return graph


def holes_after(graph, current: Iterable, entries: Iterable,
                headcount_add: Mapping[str, Mapping[str, int]] | None = None, skip: Iterable[str] = ()) -> dict[str, list[dict]]:
    """확정할 때와 같은 순서로 정원을 바꾼 뒤(등급 대체 → 증원) 새로 생긴 빈자리."""
    current, entries = list(current), list(entries)
    g2 = with_headcount(substitution(graph, current, entries)[0], headcount_add)
    return new_holes(graph, g2, current, entries, skip)


def waived_need(need: Mapping[str, int], waivers: Iterable[Mapping]) -> dict[str, int]:
    """"메우지 않음"을 적용한 정원(그 등급 −n). 정원보다 많이 줄이면 ValueError."""
    out = dict(need)
    for w in waivers:
        out[w["grade"]] = out.get(w["grade"], 0) - int(w["count"])
        if out[w["grade"]] < 0:
            raise ValueError(f"{w['grade']} 정원보다 많이 줄일 수 없다")
    return out


def same_holes(a: Iterable[Mapping], b: Iterable[Mapping]) -> bool:
    """사유를 뺀 (등급, 수) 목록이 같은가(순서 무관)."""
    key = lambda xs: sorted((x["grade"], int(x["count"])) for x in xs)   # noqa: E731
    return key(a) == key(b)


def graph_for_hint(graph, hint: Mapping):
    """확정이 데이터에 적을 정원·예산(hint: grade_swaps·headcount_add·budget_add, 사업별)을 적용한 그래프 -- 미리 계산이 확정 뒤 상태로
    판정하게. 증원은 정원 표에 있는 등급만 센다(정원 밖 등급은 원래 자유 -- 평가가 같다)."""
    from core.evaluate.grade_swap import swapped_need
    swaps, adds, budget = hint.get("grade_swaps") or {}, hint.get("headcount_add") or {}, hint.get("budget_add") or {}
    for jid in sorted(set(swaps) | set(adds) | set(budget)):
        if jid not in graph.project_index:
            continue
        pj = graph.projects[graph.project_index[jid]]
        need = swapped_need({g.value: n for g, n in pj.grade_headcount.items()}, swaps.get(jid, []))
        for g, n in (adds.get(jid) or {}).items():
            if g in need:
                need[g] += int(n)
        graph = graph_with_seats(graph, jid, need, int(budget.get(jid, 0)))
    return graph


def fills_of(graph, current: Iterable, entries: Iterable, *, skip: str | None = None) -> list[dict]:
    """같은 확정에서 사람이 빠진 사업(skip 제외)에 새로 들어간 사람 -- "J014 자리 ← 전다은"(화면이 누가 메우는지 보인다).
    from_bench = 지금 아무 사업에도 없던 사람."""
    current, entries = list(current), list(entries)
    tb, ta = _teams(current), _teams(entries)
    placed = {e.person_id if hasattr(e, "person_id") else e["person_id"] for e in current}
    grade_of = {p.id: p.grade.value for p in graph.people}
    out = []
    for jid in sorted(tb):
        if jid == skip or not (tb[jid] - ta.get(jid, set())):
            continue
        for pid in sorted(ta.get(jid, set()) - tb[jid]):
            out.append({"project_id": jid, "person_id": pid, "grade": grade_of.get(pid), "from_bench": pid not in placed})
    return out
