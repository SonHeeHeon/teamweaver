from dataclasses import dataclass

from core.domain.models import Grade
from core.optimize.types import PlanAssignment


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
