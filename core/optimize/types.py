from pydantic import BaseModel


class AssignEntry(BaseModel):
    person_id: str
    project_id: str
    alloc: float


class PlanAssignment(BaseModel):
    entries: list[AssignEntry]
    objective: float
    unfilled: list[str]
    # Structurally always [] for MILP solutions (core/optimize/milp.py hardcodes
    # violations=[] since the solver's own constraints -- budget, availability,
    # grade headcount -- make a constraint-violating solution infeasible by
    # construction; the only "shortfall" it can express is `unfilled` via slack).
    # Only the Greedy baseline (core/optimize/greedy.py), which has no solver to
    # reject an infeasible choice, populates this with real violation messages
    # (e.g. over-budget projects). Treat a non-empty `violations` as Greedy-only
    # signal, not something to expect from any MILP-produced plan.
    violations: list[str]
    label: str = "A"

    def pairs(self) -> set[tuple[str, str]]:
        return {(e.person_id, e.project_id) for e in self.entries}
