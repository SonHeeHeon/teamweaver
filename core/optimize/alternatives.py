import logging
import math
import re
from typing import Iterator

import pulp
from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams, solve_milp, solve_milp_diagnostic
from core.optimize.types import PlanAssignment

log = logging.getLogger(__name__)

_LABELS = "ABCDEFG"
_QUALITY_FLOOR = 0.95
_PLAN_A_GAP = 0.01


def meets_quality_floor(alt_objective: float, plan_a_objective: float,
                        floor: float = _QUALITY_FLOOR) -> bool:
    """Sign-symmetric "within `floor` of Plan A's quality" gate.

    `solve_milp`'s objective bakes in `-slack_penalty * sum(slack)` (100 per
    unfilled grade slot -- see core/optimize/milp.py), so it can be a large
    NEGATIVE number whenever any slot goes unfilled. The textbook
    `alt_objective >= floor * plan_a_objective` silently inverts once
    plan_a_objective < 0: e.g. objA=-100 -> floor*objA = -95, which sits
    ABOVE objA (closer to zero). The "floor" check then demands alt beat
    -95, i.e. beat Plan A by 5% of its magnitude, rather than merely stay
    within 5% of it. Since every alt is solved under a strict superset of
    Plan A's constraints (the diversity no-good cuts added below), alt's
    objective can essentially never exceed the unconstrained global optimum
    objA -- so with objA < 0 the naive formula rejects every single
    alternative, silently producing zero alternatives for the demo
    (directly threatening the spec's "Plan A 외 유효 대안 >= 3개" target).

    Fix: measure the allowed degradation as `(1-floor)` of Plan A's
    *magnitude* and subtract it from objA. This is sign-symmetric and
    reduces to the textbook `floor*objA` exactly when objA >= 0.
    """
    allowed_drop = (1.0 - floor) * abs(plan_a_objective)
    return alt_objective >= plan_a_objective - allowed_drop - 1e-6


# milp.py가 만드는 "<프로젝트>:<등급>:<n>명 미충원". 형식이 바뀌면 조용히 0으로 세지 않고 실패한다.
_UNFILLED_COUNT = re.compile(r"^[^:]*:[^:]*:(\d+)명")


def _unfilled_people(plan: PlanAssignment) -> int:
    total = 0
    for u in plan.unfilled:
        m = _UNFILLED_COUNT.match(u)
        if m is None:
            raise ValueError(f"미충원 표기 형식이 다르다: {u!r}")
        total += int(m.group(1))
    return total


def rejection_reason(alt: PlanAssignment, accepted: list[PlanAssignment],
                     plan_a: PlanAssignment) -> str | None:
    """대안으로 내지 않을 이유(C2 [A-P1]). None이면 받는다.

    - empty: 아무도 배치하지 않은 팀은 선택지가 아니다(다양성 컷은 빈 팀을 막지 못해 반복됐다).
    - duplicate: 이미 낸 플랜과 구성((사람, 프로젝트) 집합)이 같다 -- 투입률만 다른 것은 같은 팀이다.
    - quality: Plan A 품질의 95% 미만(meets_quality_floor).
    - unfilled: 미충원 인원이 Plan A보다 많다. 미충원 1명은 목적식에서 -100점이라, A가 크게
      음수면 95% 하한이 "한 명 더 못 채운 팀"을 통과시켰다."""
    if not alt.entries:
        return "empty"
    signature = alt.pairs()
    if any(signature == p.pairs() for p in accepted):
        return "duplicate"
    if not meets_quality_floor(alt.objective, plan_a.objective):
        return "quality"
    if _unfilled_people(alt) > _unfilled_people(plan_a):
        return "unfilled"
    return None


def _diversity_cut(prev_sets: list[set[tuple[str, str]]],
                   pdx: dict[str, int], jdx: dict[str, int]):
    """Build an `extra_constraints(prob, z)` callback enforcing, for EVERY plan
    already accepted (Plan A and every alt accepted so far -- not just Plan A),
    a no-good cut approximating Jaccard(alt, prior) <= 0.8:
    `Σ_{(i,j)∈prior} z_ij <= floor(0.8·|prior|)`.
    """
    def cut(prob, z):
        for pairs_set in prev_sets:
            if not pairs_set:
                continue
            prob += pulp.lpSum(z[pdx[pid]][jdx[jid]] for pid, jid in pairs_set) \
                    <= math.floor(0.8 * len(pairs_set))
    return cut


def _unfilled_cap(graph: MemoryGraph, max_unfilled: int):
    """미충원 인원이 Plan A보다 많은 대안을 모델 안에서 막는다(C2, 리뷰 SHOULD-1).

    사후 거절만 하면 컷 아래 최적해가 "1명 덜 채운 팀"일 때 거기서 끝나, 미충원이 A와 같은
    다른 쓸 만한 대안을 놓친다. 정원식이 `Σ z(등급 g) + slack = need`라 Σ slack ≤ A의 미충원은
    z만으로 `Σ z(정원에 적힌 등급) ≥ Σ need − A의 미충원`이다."""
    def cap(prob, z):
        listed, need = [], 0
        for j, pj in enumerate(graph.projects):
            for g, n in pj.grade_headcount.items():
                need += n
                listed.extend(z[i][j] for i, pe in enumerate(graph.people) if pe.grade == g)
        if need:
            prob += pulp.lpSum(listed) >= need - max_unfilled
    return cap


def _solve(graph, S, C, params, extra_constraints=None) -> tuple[PlanAssignment, str]:
    """플랜과 종료 사유. "time_limit_incumbent"면 시간 한도에서 멈춘 해다(milp.py)."""
    raw = solve_milp_diagnostic(graph, S, C, params, extra_constraints=extra_constraints)
    reason = raw.evidence.termination_reason
    return raw.plan.model_copy(update={"time_limited": reason == "time_limit_incumbent"}), reason


def _failure_reason(exc: RuntimeError) -> str:
    """대안 solve 실패의 분류(milp.py의 예외 문구). 캐시·화면 안내가 이 값으로 갈린다.
    - no_feasible_alternative: 컷·상한 아래 해가 없다(결정적 -- 캐시해도 된다).
    - time_limit: 시간 안에 해를 못 찾았다(부하에 따라 다르다 -- 캐시하지 않는다).
    - validation_rejected / solver_failed: 독립 검증 거절·기타 실패(보수적으로 캐시하지 않는다)."""
    text = str(exc)
    if text.startswith("MILP failed: Infeasible"):
        return "no_feasible_alternative"
    if "no incumbent solution within time_limit" in text:
        return "time_limit"
    if "invalid incumbent candidate" in text:
        return "validation_rejected"
    return "solver_failed"


_NOT_CACHEABLE = frozenset({"time_limit", "validation_rejected", "solver_failed"})


def cacheable(outcome: dict) -> bool:
    """이 묶음을 캐시해도 되는가. 시간 한도에 걸린 해나 실패로 끊긴 묶음은 부하에 따라 달라진다."""
    return not outcome.get("time_limited") and outcome.get("stop_reason") not in _NOT_CACHEABLE


def generate_plans_streaming(graph: MemoryGraph, S, C, params: MilpParams,
                             n_alternatives: int = 3,
                             outcome: dict | None = None) -> Iterator[PlanAssignment]:
    """generate_plans의 제너레이터판. API 레이어(Task 4)가 Plan A를 먼저
    yield받아 즉시 클라이언트로 흘리고, 이후 대안이 나오는 대로 흘릴 수 있게
    한다(A안: 점진 반환). 로직은 generate_plans와 동일 -- 반환 방식만 다르다.

    Plan A anchors both the headline "95% of Plan A" quality floor (meets_quality_floor)
    and Task 14's E2E assertion. If Plan A is solved only to the caller's gap (default
    0.05), it is itself merely guaranteed within 5% of the TRUE optimum -- so the
    end-to-end worst-case guarantee for an alternative vs. the true optimum compounds to
    roughly floor*(1-gap) = 0.95*0.95 ~= 90.25%, not the 95% the "quality floor" name
    implies. Plan A is solved exactly once (alternatives are solved n_alternatives times),
    so tightening just its gap is cheap -- solve it far closer to true-optimal and leave
    alternatives at the caller's (looser, faster) gap.
    """
    plan_a_params = params.model_copy(update={"gap": min(params.gap, _PLAN_A_GAP)})
    plan_a, status = _solve(graph, S, C, plan_a_params)
    if outcome is not None and status == "time_limit_incumbent":
        # 시간 한도에 걸린 해(incumbent)는 부하에 따라 달라진다 -- 호출부는 캐시하지 않는다(SHOULD-2).
        outcome["time_limited"] = True
    yield plan_a
    plans = [plan_a]
    # 미충원 상한(_unfilled_cap)은 품질 하한이 미충원 1명분 감점(slack_penalty)보다 큰 하락을 허용할
    # 때만 넣는다. 그 밖에는 하한이 이미 그런 해를 거절하고, 늘 넣으면 기본 데이터에서 계산이 ~30%
    # 느려졌다(실측 36초 → 47초, 대안 3개). 다른 항의 이득을 무시한 어림이라 경계 근처에서는 사후
    # 검사(rejection_reason "unfilled")로 멈출 수 있다 -- 정확성은 사후 검사가 지킨다.
    needs_cap = (1.0 - _QUALITY_FLOOR) * abs(plan_a.objective) >= params.slack_penalty
    jdx = graph.project_index
    pdx = graph.pid_index
    for _ in range(n_alternatives):
        prev_sets = [p.pairs() for p in plans]
        diversity = _diversity_cut(prev_sets, pdx, jdx)
        cap = (_unfilled_cap(graph, _unfilled_people(plan_a)) if needs_cap
               else (lambda prob, z: None))

        def cut(prob, z, diversity=diversity, cap=cap):
            diversity(prob, z)
            cap(prob, z)
        try:
            alt, status = _solve(graph, S, C, params, extra_constraints=cut)
            if outcome is not None and status == "time_limit_incumbent":
                outcome["time_limited"] = True
        except RuntimeError as exc:
            # 대안 하나가 독립 검증에 거절되거나(C0) 풀리지 않으면 그 앞까지의 유효한 플랜만
            # 돌려준다. 예외를 올리면 이미 낸 A~C까지 묶음 전체가 실패하고, 부팅 사전계산이면
            # 서버가 뜨지 않는다(통합 리뷰 MUST). 실패한 대안은 컷에 넣지 않으므로 다음 반복도 같은
            # 문제를 다시 풀 뿐이다 -- 여기서 멈춘다.
            log.warning("대안 %s 생성을 멈춤(앞선 %d개만 반환): %s", _LABELS[len(plans)], len(plans), exc)
            if outcome is not None:
                outcome["stop_reason"] = _failure_reason(exc)
            break
        reason = rejection_reason(alt, plans, plan_a)
        if reason is not None:
            # 거절한 대안을 컷에 넣지 않았으니 다음 solve도 같은 답을 낸다 -- 여기서 멈추고,
            # 요청보다 적게 낸 것은 호출부(화면)가 "조건을 만족하는 대안 없음"으로 알린다.
            log.info("대안 %s 거절(%s) -- Plan A 포함 %d개로 마친다", _LABELS[len(plans)], reason, len(plans))
            if outcome is not None:
                outcome["stop_reason"] = reason
            break
        alt.label = _LABELS[len(plans)]
        plans.append(alt)
        yield alt


def generate_plans(graph: MemoryGraph, S, C, params: MilpParams,
                   n_alternatives: int = 3, outcome: dict | None = None) -> list[PlanAssignment]:
    return list(generate_plans_streaming(graph, S, C, params, n_alternatives, outcome))
