from dataclasses import dataclass

from core.domain.models import Grade
from core.optimize.types import PlanAssignment


@dataclass(frozen=True)
class SolverEvidence:
    """JSON-safe diagnostics from the solver invocation."""

    solver_name: str
    native_status: str
    termination_reason: str
    has_incumbent: bool
    best_bound: float | None
    options: dict[str, int | float | str | bool | None]


@dataclass(frozen=True)
class RawMilpSolution:
    """Solver values retained before display-oriented allocation flooring."""

    plan: PlanAssignment
    status: str
    objective: float
    z: dict[tuple[int, int], float]
    a: dict[tuple[int, int], float]
    y: dict[tuple[int, int, int], float]
    slack: dict[tuple[int, Grade], float]
    reward_pairs: tuple[tuple[int, int], ...]
    penalty_pairs: tuple[tuple[int, int], ...]
    variable_count: int
    constraint_count: int
    evidence: SolverEvidence
    # 월별 모드의 달별 투입률 {(i, j, m): 값}. 있으면 a[(i, j)]는 진행 달 평균이다(검증기가 일치를 본다).
    a_month: dict[tuple[int, int, int], float] | None = None


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    location: str
    actual: float
    limit: float
    error: float


@dataclass(frozen=True)
class ObjectiveBreakdown:
    skill: float
    synergy: float
    overfamiliarity: float
    unfilled: float
    total: float


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    issues: tuple[ValidationIssue, ...]
    objective: ObjectiveBreakdown
    solver_objective_error: float
