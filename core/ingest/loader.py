"""Read and validate a CSV bundle (see core/ingest/contract.py). Never fills in missing values."""
import csv
import datetime as dt
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from core.ingest.contract import FILE_SPECS, FILES, HORIZON_MONTHS, MANIFEST_KEYS, SCHEMA_VERSION, Column
from core.ingest.report import IngestReport

_MONTH = re.compile(r"^([0-9]{4})-([0-9]{2})$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_INT = re.compile(r"^-?[0-9]+$")
_FLOAT = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")           # ASCII digits only: no 1_0, no full-width digits, no NaN


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)           # macOS exports may use decomposed Hangul


@dataclass
class Bundle:
    manifest: dict
    horizon: list[dt.date]                      # first day of each planning month, in order
    tables: dict[str, list[dict]] = field(default_factory=dict)


def _parse_month(text: str) -> dt.date:
    m = _MONTH.match(text)
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError("expected YYYY-MM")
    return dt.date(int(m.group(1)), int(m.group(2)), 1)


def _parse(col: Column, text: str):
    if col.kind == "str":
        return text
    if col.kind == "enum":
        if text not in col.choices:
            raise ValueError(f"must be one of {', '.join(col.choices)}")
        return text
    if col.kind == "int":
        if not _INT.match(text):
            raise ValueError("expected an integer")
        value = int(text)
    elif col.kind == "float":
        if not _FLOAT.match(text):
            raise ValueError("expected a number like 0.5")
        value = float(text)
    elif col.kind == "date":
        if not _DATE.match(text):
            raise ValueError("expected YYYY-MM-DD")
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            raise ValueError("expected a valid YYYY-MM-DD date") from None
    elif col.kind == "month":
        return _parse_month(text)
    else:  # pragma: no cover - contract bug
        raise AssertionError(col.kind)
    if col.min is not None and value < col.min:
        raise ValueError(f"must be >= {col.min:g}")
    if col.max is not None and value > col.max:
        raise ValueError(f"must be <= {col.max:g}")
    return value


def _read_manifest(root: Path, report: IngestReport) -> tuple[dict, list[dt.date]]:
    path = root / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as exc:
        report.error("manifest.json", f"cannot read manifest: {exc}")
        return {}, []
    for key in MANIFEST_KEYS:
        if key not in manifest:
            report.error("manifest.json", "required key is missing", column=key)
    if "schema_version" in manifest and (type(manifest["schema_version"]) is not int
                                         or manifest["schema_version"] != SCHEMA_VERSION):
        report.error("manifest.json", f"schema_version must be {SCHEMA_VERSION}", column="schema_version")
    if "horizon_months" in manifest and (type(manifest["horizon_months"]) is not int
                                         or manifest["horizon_months"] != HORIZON_MONTHS):
        report.error("manifest.json", f"horizon_months must be {HORIZON_MONTHS} (current model)",
                     column="horizon_months")
    if "synthetic" in manifest and not isinstance(manifest["synthetic"], bool):
        report.error("manifest.json", "synthetic must be true or false", column="synthetic")
    for key in ("dataset_id", "cost_unit"):
        if key in manifest and (not isinstance(manifest[key], str) or not manifest[key].strip()):
            report.error("manifest.json", "must be a non-empty string", column=key)
    horizon: list[dt.date] = []
    if "horizon_start" in manifest:
        try:
            start = _parse_month(str(manifest["horizon_start"]))
            for k in range(HORIZON_MONTHS):
                y, m = divmod(start.month - 1 + k, 12)
                horizon.append(dt.date(start.year + y, m + 1, 1))
        except ValueError:
            report.error("manifest.json", "horizon_start must be YYYY-MM", column="horizon_start")
    return manifest, horizon


def _read_mapping(root: Path, report: IngestReport) -> dict[str, dict[str, str]]:
    path = root / "mapping.json"
    if not path.exists():
        return {}
    try:
        mapping = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        report.error("mapping.json", f"cannot read mapping: {exc}")
        return {}
    ok = isinstance(mapping, dict) and all(
        isinstance(v, dict) and all(isinstance(a, str) and isinstance(b, str) for a, b in v.items())
        for v in mapping.values())
    if not ok:
        report.error("mapping.json", 'expected {"file.csv": {"canonical_column": "source header"}}')
        return {}
    clean = {}
    for file, cols in mapping.items():
        spec = FILE_SPECS.get(file)
        if spec is None:
            report.error("mapping.json", f"unknown file '{file}'")
            continue
        names = {c.name for c in spec.columns}
        for canonical in cols:
            if canonical not in names:
                report.error("mapping.json", f"{file}: unknown column '{canonical}'")
        clean[file] = {_nfc(k): _nfc(v).strip() for k, v in cols.items()}
    return clean


def _read_table(root: Path, spec, mapping: dict[str, str], report: IngestReport) -> list[dict] | None:
    path = root / spec.name
    if not path.is_file():
        if getattr(spec, "optional", False):
            return []
        report.error(spec.name, "required file is missing")
        return None
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, strict=True)
            headers = [_nfc(h).strip() for h in next(reader, [])]
            raw_rows = []
            for raw in reader:
                raw_rows.append((reader.line_num, [_nfc(cell) for cell in raw]))
    except UnicodeDecodeError:
        report.error(spec.name, "not UTF-8 text — save the CSV as UTF-8 (Excel: 'CSV UTF-8')")
        return None
    except (csv.Error, OSError) as exc:
        report.error(spec.name, f"cannot read CSV: {exc}")
        return None
    source_for = {c.name: mapping.get(c.name, c.name) for c in spec.columns}
    dup_headers = sorted({h for h in headers if h and headers.count(h) > 1})
    if dup_headers:
        report.error(spec.name, "duplicate header(s): " + ", ".join(dup_headers))
        return None
    dup_sources = sorted({v for v in source_for.values() if list(source_for.values()).count(v) > 1})
    if dup_sources:
        report.error(spec.name, "mapping sends several columns to the same header: " + ", ".join(dup_sources))
        return None
    index = {h: i for i, h in enumerate(headers)}
    known_sources = set(source_for.values())
    for h in index:
        if h and h not in known_sources:
            report.warn(spec.name, "unknown column is ignored", column=h)
    missing = [c for c in spec.columns if source_for[c.name] not in index]
    for c in missing:
        if c.required:
            report.error(spec.name, f"required column is missing (header '{source_for[c.name]}')", column=c.name)
    if any(c.required for c in missing):
        return None
    rows = []
    for r, (line, raw) in enumerate(raw_rows, start=1):
        if not any(cell.strip() for cell in raw):
            continue
        if len(raw) != len(headers):
            report.error(spec.name, f"has {len(raw)} values but the header has {len(headers)} "
                         f"(file line {line}; check unquoted commas)", row=r)
            continue
        row = {"__row__": r}
        for c in spec.columns:
            i = index.get(source_for[c.name])
            text = raw[i].strip() if i is not None and i < len(raw) else ""
            if text == "":
                if c.required:
                    report.error(spec.name, "required value is empty", row=r, column=c.name)
                row[c.name] = None
                continue
            try:
                row[c.name] = _parse(c, text)
            except ValueError as exc:
                report.error(spec.name, f"invalid value '{text}': {exc}", row=r, column=c.name)
                row[c.name] = None
        rows.append(row)
    report.row_counts[spec.name] = len(rows)
    return rows


def _check_keys_and_refs(tables: dict[str, list[dict]], report: IngestReport) -> None:
    for spec in FILES:
        rows = tables.get(spec.name)
        if rows is None:
            continue
        seen: dict[tuple, int] = {}
        for row in rows:
            key = tuple(row.get(k) for k in spec.key)
            if None in key:
                continue
            if key in seen:
                report.error(spec.name, f"duplicate key {key} (first at row {seen[key]})",
                             row=row["__row__"], column=",".join(spec.key))
            else:
                seen[key] = row["__row__"]
        for c in spec.columns:
            if not c.ref:
                continue
            target_file, target_col = c.ref.split(":")
            target = tables.get(target_file)
            if target is None:
                continue
            valid = {t[target_col] for t in target if t.get(target_col) is not None}
            for row in rows:
                if row.get(c.name) is not None and row[c.name] not in valid:
                    report.error(spec.name, f"'{row[c.name]}' not found in {target_file}",
                                 row=row["__row__"], column=c.name)


def _check_rules(tables: dict[str, list[dict]], horizon: list[dt.date], report: IngestReport) -> None:
    for row in tables.get("work_history.csv") or []:
        if row["start_date"] and row["end_date"] and row["end_date"] < row["start_date"]:
            report.error("work_history.csv", "end_date is before start_date", row=row["__row__"], column="end_date")
    for row in tables.get("projects.csv") or []:
        if row["start_month"] and row["end_month"] and row["end_month"] < row["start_month"]:
            report.error("projects.csv", "end_month is before start_month", row=row["__row__"], column="end_month")
    for row in tables.get("reviews.csv") or []:
        if row["reviewer_id"] and row["reviewer_id"] == row["reviewee_id"]:
            report.error("reviews.csv", "a person cannot review themselves", row=row["__row__"], column="reviewer_id")

    items = tables.get("review_items.csv")
    if items is not None and tables.get("reviews.csv") is not None:
        counts = Counter((r["review_id"], r["polarity"]) for r in items if r["review_id"] and r["polarity"])
        for review in tables["reviews.csv"]:
            if review["review_id"] is None:
                continue
            for polarity in ("positive", "negative"):
                n = counts.get((review["review_id"], polarity), 0)
                if not 1 <= n <= 5:
                    report.error("review_items.csv",
                                 f"review {review['review_id']} has {n} {polarity} items (allowed 1-5)")

    avail = tables.get("availability.csv")
    people = tables.get("people.csv")
    if avail is not None and people is not None and horizon:
        have = defaultdict(set)
        for row in avail:
            if row["month"] is None or row["person_id"] is None:
                continue
            if row["month"] in horizon:
                have[row["person_id"]].add(row["month"])
            else:
                report.warn("availability.csv", "month outside the planning horizon is ignored",
                            row=row["__row__"], column="month")
        for pid in dict.fromkeys(p["person_id"] for p in people if p["person_id"] is not None):
            missing = [m for m in horizon if m not in have[pid]]
            if missing:
                report.error("availability.csv", f"{pid} has no availability for "
                             + ", ".join(m.strftime("%Y-%m") for m in missing)
                             + " (missing months are never assumed available)")


    current = tables.get("current_assignments.csv") or []
    if current:
        phase = {p["project_id"]: p["phase"] for p in tables.get("projects.csv") or []}
        best = defaultdict(float)
        for row in avail or []:
            if row["person_id"] is not None and row["month"] in horizon and row["available_mm"] is not None:
                best[row["person_id"]] = max(best[row["person_id"]], row["available_mm"])
        total = defaultdict(float)
        for row in current:
            if phase.get(row["project_id"]) == "제안":
                report.warn("current_assignments.csv", f"{row['project_id']} is a proposal; a current roster is "
                            "unusual there", row=row["__row__"], column="project_id")
            if row["person_id"] is not None and row["alloc"] is not None:
                total[row["person_id"]] += row["alloc"]
        for pid, s in sorted(total.items()):
            if s > best.get(pid, 0.0) + 1e-9:
                report.warn("current_assignments.csv", f"{pid}'s current allocations add up to {s:.2f}, above "
                            f"any month's availability ({best.get(pid, 0.0):.2f})")


def _check_hashes(root: Path, manifest: dict, report: IngestReport) -> None:
    """Optional manifest "files": {name: sha256}. When present, every listed file must match."""
    files = manifest.get("files")
    if files is None:
        return
    if not isinstance(files, dict):
        report.error("manifest.json", 'files must be {"name.csv": "sha256"}', column="files")
        return
    import hashlib
    for name, digest in files.items():
        path = root / str(name)
        if (name not in FILE_SPECS and name != "mapping.json") or not path.is_file():
            report.error("manifest.json", f"files lists '{name}', which is not a bundle file", column="files")
            continue
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            report.error(str(name), f"cannot read file to check its sha256: {exc}")
            continue
        if actual != digest:
            report.error(str(name), "content differs from the sha256 in manifest.json (edited after export?)")


def load_bundle(root: Path) -> tuple[Bundle, IngestReport]:
    root = Path(root)
    report = IngestReport()
    manifest, horizon = _read_manifest(root, report)
    mapping = _read_mapping(root, report)
    tables: dict[str, list[dict]] = {}
    for spec in FILES:
        rows = _read_table(root, spec, mapping.get(spec.name, {}), report)
        if rows is not None:
            tables[spec.name] = rows
    _check_hashes(root, manifest, report)
    _check_keys_and_refs(tables, report)
    _check_rules(tables, horizon, report)
    return Bundle(manifest=manifest, horizon=horizon, tables=tables), report


__all__ = ["Bundle", "load_bundle", "FILE_SPECS"]
