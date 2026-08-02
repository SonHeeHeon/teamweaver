from pydantic import BaseModel


class AssignEntry(BaseModel):
    person_id: str
    project_id: str
    alloc: float


class PlanAssignment(BaseModel):
    entries: list[AssignEntry]
    objective: float
    unfilled: list[str]
    violations: list[str]
    label: str = "A"

    def pairs(self) -> set[tuple[str, str]]:
        return {(e.person_id, e.project_id) for e in self.entries}
