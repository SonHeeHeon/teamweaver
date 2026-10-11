"""Budget-only, fixed-selection refinement; the independent validator stays strict."""
from dataclasses import dataclass, replace
import math
import time
from typing import Mapping, Literal

import numpy as np
import scipy
from scipy.optimize import linprog
from scipy.sparse import csr_matrix

from core.optimize.audit_types import RawMilpSolution, ValidationReport
from core.optimize.candidate import rebuild_plan
from core.optimize.milp import mean_alloc
from core.optimize.validation import _independent_partner_floor, validate_raw_solution


@dataclass(frozen=True)
class NumericalPolicy:
    version: str = "c1-near-feasible-v1"
    enabled: bool = False
    budget_relative_admission: float = 1e-7
    max_allocation_delta: float = 1e-7
    max_refinement_seconds: float = 2.

    def __post_init__(self):
        if self.version != "c1-near-feasible-v1" or type(self.enabled) is not bool:
            raise ValueError("unsupported numerical policy")
        for value, cap in [(self.budget_relative_admission,1e-7),
                           (self.max_allocation_delta,1e-7),(self.max_refinement_seconds,2.)]:
            if not math.isfinite(value) or not 0 < value <= cap:
                raise ValueError("numerical guardrails cannot be widened")


@dataclass(frozen=True)
class LinearAllocationConstraint:
    coefficients: Mapping[tuple[int,int],float]
    sense: Literal["LE","EQ","GE"]
    rhs: float


class UnsupportedModelMutation(ValueError):
    pass


@dataclass(frozen=True)
class ModelContractSnapshot:
    variables: dict
    objective: tuple
    objective_sense: int
    rows: dict


def _expression(expr):
    coefficients = tuple(sorted((v.name,float(c)) for v,c in expr.items()))
    return coefficients,float(expr.constant)


def capture_model_contract(prob):
    return ModelContractSnapshot(
        {v.name:(id(v),v.lowBound,v.upBound,v.cat) for v in prob.variables()},
        _expression(prob.objective),prob.sense,
        {name:(_expression(row),row.sense) for name,row in prob.constraints.items()})


def project_additive_constraints(before, after, *, allocation_variables, fixed_values):
    if (before.variables != after.variables or before.objective != after.objective
            or before.objective_sense != after.objective_sense
            or any(after.rows.get(name) != row for name,row in before.rows.items())):
        raise UnsupportedModelMutation("UNSUPPORTED_MODEL_MUTATION")
    projected = []
    # Include original rows too: post-refinement must satisfy the whole model.
    for (coefficients,constant),sense in after.rows.values():
        rhs = -constant
        a_coefficients = {}
        for name,coefficient in coefficients:
            if not math.isfinite(coefficient):
                raise UnsupportedModelMutation("nonfinite coefficient")
            if name in allocation_variables:
                a_coefficients[allocation_variables[name]] = coefficient
            elif name in fixed_values and math.isfinite(fixed_values[name]):
                rhs -= coefficient*fixed_values[name]
            else:
                raise UnsupportedModelMutation(f"unsupported variable: {name}")
        if not math.isfinite(rhs) or sense not in {-1,0,1}:
            raise UnsupportedModelMutation("invalid row")
        projected.append(LinearAllocationConstraint(a_coefficients,{-1:"LE",0:"EQ",1:"GE"}[sense],rhs))
    return tuple(projected)


@dataclass(frozen=True)
class RefinementEvidence:
    policy_version: str
    attempted: bool
    reason: str
    elapsed_seconds: float
    max_allocation_delta: float | None
    objective_before: float | None
    objective_after: float | None
    budget_residuals_before: tuple
    budget_residuals_after: tuple
    auxiliary_engine: str = "SciPy/HiGHS"
    auxiliary_version: str = scipy.__version__


@dataclass(frozen=True)
class CandidateAssessment:
    native_capture: RawMilpSolution | None
    native_validation: ValidationReport | None
    validation_candidate: RawMilpSolution
    initial_validation: ValidationReport
    accepted: RawMilpSolution | None
    final_validation: ValidationReport | None
    refinement: RefinementEvidence


def _alloc_view(raw):
    """보정이 움직이는 투입률 변수: fixed면 a[(i, j)], monthly면 a_month[(i, j, m)]."""
    return raw.a_month if raw.a_month is not None else raw.a


def _budget_cells(graph, raw):
    """(project, month|None) 단위 예산 칸 -- monthly면 달마다, fixed면 프로젝트마다 하나."""
    if raw.a_month is None:
        return [(j, None) for j in range(len(graph.projects))]
    return [(j, m) for j, p in enumerate(graph.projects) for m in p.months]


def _residuals(graph, raw):
    if raw is None:
        return ()
    view = _alloc_view(raw)
    rows = []
    for j, m in _budget_cells(graph, raw):
        project = graph.projects[j]
        key = (lambda i: (i, j)) if m is None else (lambda i: (i, j, m))
        terms = [p.monthly_rate*view.get(key(i),float("nan"))
                 if isinstance(view.get(key(i)),(int,float)) else float("nan")
                 for i,p in enumerate(graph.people)]
        if not all(math.isfinite(x) for x in terms):
            return ()
        absolute = sum(terms)-project.monthly_budget
        scale = max(1.,abs(project.monthly_budget),sum(abs(x) for x in terms))
        rows.append({"project_id":project.id if m is None else f"{project.id}:m{m}",
                     "absolute":absolute,"normalized":max(0.,absolute)/scale})
    return tuple(rows)


def _linear_feasible(rows, allocations):
    for row in rows:
        if row.sense not in {"LE","EQ","GE"} or not math.isfinite(row.rhs):
            return False
        try:
            actual = sum(float(c)*allocations[key] for key,c in row.coefficients.items())
        except (KeyError,TypeError,ValueError):
            return False
        if not math.isfinite(actual): return False
        error = actual-row.rhs
        if ((row.sense == "LE" and error > 1e-6) or (row.sense == "GE" and error < -1e-6)
                or (row.sense == "EQ" and abs(error) > 1e-6)):
            return False
    return True


def _allocation_lp(graph, S, params, native, extra, seconds, delta):
    """고정팀 투입률 LP. 가용률·예산 행을 직접 쓴다 -- milp.py의 a 관련 제약이 바뀌면 여기(와
    validation.py)도 함께 고친다(MILP 정식 동기화 대상, CLAUDE.md "함정")."""
    view = _alloc_view(native)
    monthly = native.a_month is not None
    keys = sorted(view)
    index = {key:i for i,key in enumerate(keys)}
    zf = lambda key: native.z[(key[0], key[1])]
    # 상한은 원본 값이다: 보정은 예산 초과를 줄이는 것이지, 목적(max S·a)을 따라 예산이 남는 배정을
    # 올리는 것이 아니다(통합 리뷰 SHOULD -- 올리면 벤치에서 목적값이 bound를 넘을 수 있었다).
    bounds = [(max(params.min_alloc*zf(key),view[key]-delta),
               min(zf(key),view[key])) if zf(key) else (0.,0.) for key in keys]
    if any(lo > hi for lo,hi in bounds):
        raise ValueError("inconsistent allocation bounds")
    rows = []
    for i,person in enumerate(graph.people):
        for month,availability in enumerate(person.availability):
            rows.append(LinearAllocationConstraint({((i,j,month) if monthly else (i,j)):1.
                                                    for j,p in enumerate(graph.projects)
                                                    if month in p.months},"LE",availability))
    for j, m in _budget_cells(graph, native):
        project = graph.projects[j]
        rows.append(LinearAllocationConstraint({((i,j) if m is None else (i,j,m)):float(p.monthly_rate)
                                                for i,p in enumerate(graph.people)},
                                               "LE",float(project.monthly_budget)))
    rows.extend(extra)
    ub_rows,eq_rows,ub_rhs,eq_rhs = [],[],[],[]
    for row in rows:
        if row.sense not in {"LE","EQ","GE"} or not math.isfinite(row.rhs):
            raise ValueError("invalid LP row")
        factor = -1. if row.sense == "GE" else 1.
        coefficients = {index[k]:float(v)*factor for k,v in row.coefficients.items()}
        if not all(math.isfinite(v) for v in coefficients.values()): raise ValueError("nonfinite LP row")
        if row.sense == "EQ": eq_rows.append(coefficients);eq_rhs.append(row.rhs)
        else: ub_rows.append(coefficients);ub_rhs.append(factor*row.rhs)
    def matrix(data):
        if not data: return None
        triples = [(i,j,v) for i,row in enumerate(data) for j,v in row.items()]
        return csr_matrix(([t[2] for t in triples],([t[0] for t in triples],[t[1] for t in triples])),shape=(len(data),len(keys)))
    coef = [(-float(S[k[0],k[1]])/len(graph.projects[k[1]].months)) if monthly else -float(S[k[0],k[1]])
            for k in keys]
    result = linprog(coef,A_ub=matrix(ub_rows),b_ub=ub_rhs or None,
                     A_eq=matrix(eq_rows),b_eq=eq_rhs or None,bounds=bounds,method="highs",
                     options={"time_limit":seconds,"primal_feasibility_tolerance":1e-10,
                              "dual_feasibility_tolerance":1e-10})
    if not result.success:
        raise ValueError(f"LP_NOT_SUCCESS:{result.message}")
    return {key:float(value) for key,value in zip(keys,result.x,strict=True)}


def assess_candidate(graph, S, C, params, candidate, *, native_capture, policy,
                     deadline=None, extra_linear_constraints=()):
    initial = validate_raw_solution(graph,S,C,params,candidate)
    native_validation = (initial if native_capture is candidate else
                         validate_raw_solution(graph,S,C,params,native_capture) if native_capture is not None else None)
    start = time.monotonic()
    end = min(start+policy.max_refinement_seconds, deadline) if deadline is not None else start+policy.max_refinement_seconds
    before = _residuals(graph,native_capture)
    def finish(reason, *, attempted=False, accepted=None, final=None, delta=None):
        after = _residuals(graph,accepted)
        elapsed = time.monotonic()-start
        if accepted is not None and attempted and time.monotonic() > end:
            reason,accepted = "REFINEMENT_BUDGET_EXCEEDED",None
        evidence = RefinementEvidence(policy.version,attempted,reason,elapsed,delta,
                                      native_capture.objective if native_capture else None,
                                      accepted.objective if accepted else None,before,after)
        return CandidateAssessment(native_capture,native_validation,candidate,initial,accepted,final,evidence)
    if initial.valid:
        # 독립 검증기는 기본 모델만 본다. 추가 조건(다양성 컷)은 여기서 확인한다(통합 리뷰 SHOULD).
        if not _linear_feasible(extra_linear_constraints,_alloc_view(candidate)):
            return finish("EXTRA_CONSTRAINT_VIOLATED")
        return finish("ALREADY_VALID",accepted=candidate,final=initial)
    if not policy.enabled:
        return finish("POLICY_DISABLED")
    if native_capture is None or native_validation is None:
        return finish("NATIVE_CAPTURE_MISSING")
    # 반환 명단 예산(plan_budget, 2026-10-11)은 원해 예산 잔차에 딸려 오는 것이라 같이 보정한다 -- 보정 뒤 명단을 다시 만든다
    # (Claude 폴백 리뷰 SHOULD: 빼면 예전에 보정으로 살리던 해가 거절된다)
    def _budget_only(issues) -> bool:
        codes = {i.code for i in issues}
        return "budget" in codes and codes <= {"budget", "plan_budget"}
    if (not _budget_only(initial.issues) or not _budget_only(native_validation.issues)
            or any(v not in (0.,1.) for v in native_capture.z.values())):
        return finish("INELIGIBLE_CONSTRAINT_OR_DOMAIN")
    if not before or any(r["normalized"] > policy.budget_relative_admission for r in before):
        return finish("BUDGET_RESIDUAL_TOO_LARGE")
    if end <= time.monotonic():
        return finish("REFINEMENT_BUDGET_EXCEEDED")
    try:
        allocations = _allocation_lp(graph,S,params,native_capture,extra_linear_constraints,
                                     end-time.monotonic(),policy.max_allocation_delta)
        native_view = _alloc_view(native_capture)
        delta = max(abs(allocations[k]-native_view[k]) for k in allocations)
        reward_pairs,penalty_pairs = set(candidate.reward_pairs),set(candidate.penalty_pairs)
        skill_alloc = allocations if native_capture.a_month is None else mean_alloc(graph, allocations)
        objective = (sum(float(S[i,j])*a for (i,j),a in skill_alloc.items())
                     + getattr(params,"seat_fit_weight",0.0)*sum(float(S[i,j])*v for (i,j),v in candidate.z.items())
                     + params.lam*sum(float(C[p,q])*v for (p,q,j),v in candidate.y.items() if (p,q) in reward_pairs)
                     - params.mu*sum(v for (p,q,j),v in candidate.y.items() if (p,q) in penalty_pairs)
                     # 실험 G(파트너 다양성 하한): z만 쓰는 항이라 투입률 보정과 무관하다 -- 빠뜨리면 검증기가 거절(리뷰 MUST)
                     + _independent_partner_floor(candidate.z,candidate.penalty_pairs,params,
                                                  len(graph.people),len(graph.projects))
                     - params.slack_penalty*sum(candidate.slack.values()))
        refined = rebuild_plan(graph,params,candidate,allocations=allocations,objective=objective)
        final = validate_raw_solution(graph,S,C,params,refined)
        if time.monotonic() > end:
            return finish("REFINEMENT_BUDGET_EXCEEDED",attempted=True,final=final,delta=delta)
        if not math.isfinite(delta) or delta > policy.max_allocation_delta+1e-15:
            return finish("ALLOCATION_DELTA_EXCEEDED",attempted=True,final=final,delta=delta)
        if not final.valid or not _linear_feasible(extra_linear_constraints,allocations):
            return finish("FINAL_VALIDATION_REJECTED",attempted=True,final=final,delta=delta)
        return finish("REFINED",attempted=True,accepted=refined,final=final,delta=delta)
    except (ValueError,TypeError,KeyError,RuntimeError) as exc:
        return finish(f"REFINEMENT_FAILED:{type(exc).__name__}:{exc}",attempted=True)
