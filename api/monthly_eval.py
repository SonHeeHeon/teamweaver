"""월별 투입률 명단의 평가 보정(평가기가 달별 값을 읽기 전까지의 임시 장치).

`core/evaluate/plan_eval.py`(claude-a 영역)는 `alloc`(진행 달 평균)만 읽는다. 끝나는 달이 다른 프로젝트가
겹치면 평균을 더한 값이 그달 가용률을 넘어, 솔버가 낸 정상 월별 플랜이 "가용률 위반"으로 보였다(리뷰 M2).
반대로 한 달 예산 초과는 평균이 가린다. 그래서 명단에 `monthly_alloc`이 하나라도 있으면 가용률·예산·
투입률 범위 위반을 **달별 값으로 다시 계산해 바꿔 끼운다**. 목적값은 그대로 둔다(기술항은 S × 진행 달
평균 = S × alloc이라 같다).

평가기가 월별을 지원하면(모듈 상수 `SUPPORTS_MONTHLY_ALLOC = True`) 아무것도 하지 않는다 -- claude-a 요청 참고.

전제: 교체는 빠지는 사람의 monthly_alloc을 그대로 이어받는다(api/routes/whatif._swapped_entries). 그래서 교체 전후
명단이 같은 방식으로 보정되고, 새 위반 비교((code, location))가 섞이지 않는다. 교체가 월별 항목을 일반 항목으로
바꾸게 되면 이 전제가 깨진다."""
from dataclasses import replace

import core.evaluate.plan_eval as plan_eval
from core.evaluate.plan_eval import PlanEvaluation, PlanViolation

TOL = 1e-6
MONTHLY_CODES = {"availability", "budget", "alloc_range"}


def _month_value(entry, month: int) -> float:
    m = entry.monthly_alloc
    return float(m[month]) if m is not None and month in m else float(entry.alloc)


def adjust_for_monthly(graph, params, entries, ev: PlanEvaluation) -> PlanEvaluation:
    if getattr(plan_eval, "SUPPORTS_MONTHLY_ALLOC", False) or not any(e.monthly_alloc for e in entries):
        return ev
    people = {p.id: p for p in graph.people}
    projects = {p.id: p for p in graph.projects}
    kept = [v for v in ev.violations if v.code not in MONTHLY_CODES]
    extra: list[PlanViolation] = []
    load: dict[tuple[str, int], float] = {}
    cost: dict[tuple[str, int], float] = {}
    for e in entries:
        project = projects[e.project_id]
        for m in project.months:
            v = _month_value(e, m)
            load[(e.person_id, m)] = load.get((e.person_id, m), 0.0) + v
            cost[(e.project_id, m)] = cost.get((e.project_id, m), 0.0) + people[e.person_id].monthly_rate * v
            if v < params.min_alloc - TOL or v > 1.0 + TOL:
                extra.append(PlanViolation(
                    "alloc_range", f"{e.person_id}:{e.project_id}:month{m}", v, params.min_alloc,
                    f"{e.person_id}의 {e.project_id} {m + 1}번째 달 투입률 {v:.2f}가 허용 범위"
                    f"({params.min_alloc:.2f}~1.00) 밖"))
    for (pid, m), total in sorted(load.items()):
        available = people[pid].availability[m]
        if total > available + TOL:
            extra.append(PlanViolation(
                "availability", f"{pid}:month{m}", total, available,
                f"{pid}의 계획 {m + 1}번째 달 투입 합 {total:.2f}가 가용률 {available:.2f}를 초과"))
    for (jid, m), total in sorted(cost.items()):
        budget = projects[jid].monthly_budget
        if total > budget + TOL:
            extra.append(PlanViolation(
                "budget", f"{jid}:month{m}", total, budget,
                f"{jid} {m + 1}번째 달 비용 {total:,.0f}이 예산 {budget:,}을 초과"))
    return replace(ev, violations=tuple(kept) + tuple(extra))


def check_monthly_entries(graph, params, entries) -> None:
    """요청 경계 검사: monthly_alloc은 그 프로젝트의 진행 달을 빠짐없이 덮고, alloc은 그 평균이어야 한다(리뷰 S2).
    틀리면 ValueError(라우트가 422로 바꾼다)."""
    projects = {p.id: p for p in graph.projects}
    for e in entries:
        if not e.monthly_alloc:
            continue
        project = projects.get(e.project_id)
        if project is None:
            continue                                # 모르는 프로젝트는 평가기가 따로 거절한다
        if set(e.monthly_alloc) != set(project.months):
            raise ValueError(f"{e.person_id}의 {e.project_id} 월별 투입률 달이 진행 달 {list(project.months)}과 다르다")
        mean = sum(e.monthly_alloc.values()) / len(e.monthly_alloc)
        if abs(mean - e.alloc) > 1e-5:
            raise ValueError(f"{e.person_id}의 {e.project_id} alloc {e.alloc}이 월별 평균 {mean:.6f}과 다르다")
