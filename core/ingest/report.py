from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue:
    level: str                  # "error" | "warning"
    file: str
    row: int | None             # 1-based data row (header excluded); None for file-level issues
    column: str | None
    message: str

    def __str__(self) -> str:
        where = self.file + (f":{self.row}" if self.row is not None else "") + (f" [{self.column}]" if self.column else "")
        return f"{self.level.upper()} {where} — {self.message}"


@dataclass
class IngestReport:
    issues: list[Issue] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)     # conversion assumptions applied (not problems)

    def error(self, file: str, message: str, row: int | None = None, column: str | None = None) -> None:
        self.issues.append(Issue("error", file, row, column, message))

    def warn(self, file: str, message: str, row: int | None = None, column: str | None = None) -> None:
        self.issues.append(Issue("warning", file, row, column, message))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = [f"rows: {self.row_counts}", f"errors: {len(self.errors)}, warnings: {len(self.warnings)}"]
        lines += [str(i) for i in self.issues]
        lines += [f"NOTE {n}" for n in self.notes]
        return "\n".join(lines)
