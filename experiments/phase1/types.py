from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from core.graph.memory_graph import MemoryGraph
from core.optimize.milp import MilpParams


class SolverAvailabilityState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class SolverAvailability:
    state: SolverAvailabilityState
    import_name: str
    version: str | None
    error: str | None


@dataclass(frozen=True)
class BenchmarkProblem:
    graph: MemoryGraph
    S: np.ndarray
    C: np.ndarray
    params: MilpParams


@dataclass(frozen=True)
class SolverOptions:
    threads: int = 1
    time_limit_seconds: float = 120.0
    relative_gap: float = 0.0

    def __post_init__(self) -> None:
        if self.threads != 1:
            raise ValueError("the Phase 1 benchmark fixes every solver to one thread")
        if self.time_limit_seconds <= 0:
            raise ValueError("time_limit_seconds must be positive")
        if self.relative_gap < 0:
            raise ValueError("relative_gap must be nonnegative")

