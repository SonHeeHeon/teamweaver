"""변경 기록(2026-10-22, claude-a -- 사용자 "액션 하나하나에 대한 것도 보고서로 남길 수 있게하고 기간으로 잡고 어떤 변경들을 했는지
각각에 대한 인사팀 보고 사항들을 정리해서 함께 pdf에 묶는것도", 설계 루트 `.omc/plan/2026-10-22-change-report.md`).

작업 이력(`api.workspace.History`)은 되돌리기용이라 최근 20판만 두고 다른 데이터를 열면 비운다 -- 기간 보고서의 원천이 될 수 없다.
그래서 판이 바뀔 때마다(`workspace.commit`·`undo`) 지우지 않는 기록에 한 건씩 덧붙인다. 한 건에는 **그때의** 사람 이름·사업 이름을 담는다
-- 나중 데이터의 이름으로 찾으면 다른 데이터를 연 뒤 지난 기록이 틀린다(검토 자료 목록 f1과 같은 문제).

저장: `<데이터 폴더>/change_log/log.json`(검토 자료와 같은 비공개 폴더 규칙). 이 기능 전에 생긴 이력은 읽을 때 이력·보관 판에서 메운다.
사람 이름은 가상 데이터일 때만 담는다(실데이터는 사번 그대로 -- K5, `display_names`와 같은 규칙).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from api.storage import atomic_write, data_dir, ensure_private_dir

KIND_LABEL = {
    "apply_auto": "신규 사업 자동 편성 확정", "apply_manual": "손 편성 확정", "apply_emergency": "긴급 투입 확정",
    "apply_plan": "연간 계획 확정", "cancel": "확정 취소", "undo": "되돌리기",
    "add_project": "신규 사업 등록", "update_project": "신규 사업 수정", "remove_project": "신규 사업 떨어짐·취소",
    "lock": "배치 고정", "unlock": "배치 고정 해제", "concurrency": "프로젝트 동시 수행 허용", "concurrency_off": "프로젝트 동시 수행 해제",
    "skill_names": "기술 이름 정리", "hr_sync": "인사 시스템에서 받기", "skill_months_owner": "기술 경력 관리 주체 변경",
    "base": "시작 데이터",
}
# 사람이 새로 들어간 사업마다 인사팀 검토 자료가 생기는 확정(api.review_docs.CONFIRM_KINDS와 같다)
REVIEW_KINDS = ("apply_auto", "apply_manual", "apply_emergency")
_LOCK = threading.Lock()


class ChangeLog:
    def __init__(self, root: Path | None = None):
        self.path = (root if root is not None else data_dir()) / "change_log" / "log.json"

    def entries(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def append(self, record: dict) -> None:
        with _LOCK:
            entries = self.entries()
            # 이름은 겹치지 않게 -- 같은 명단을 1초 안에 다시 확정하면 판 버전·시각(초)이 같다
            ids, base, n = {e.get("id") for e in entries}, record["id"], 1
            while record["id"] in ids:
                n += 1
                record["id"] = f"{base}-{n}"
            ensure_private_dir(self.path.parent)
            atomic_write(self.path, json.dumps(entries + [record], ensure_ascii=False, indent=1).encode("utf-8"))


def _key(e: dict) -> tuple[str, str, str]:
    return (str(e.get("version")), str(e.get("at")), str(e.get("kind")))


def record_id(version: str, at: str, kind: str) -> str:
    """보고서 요청에 쓰는 한 건의 이름(파일 이름에 안전한 글자만)."""
    import re
    return re.sub(r"[^A-Za-z0-9_-]", "", f"{at}-{kind}-{str(version)[:12]}")[:96]


def _rows(files: dict) -> list[dict]:
    from api.workspace_edits import current_table
    try:
        return current_table(files)[1]
    except Exception:                                   # noqa: BLE001 -- 기록은 판 변경을 막지 않는다
        return []


def people_changes(before_files: dict, files: dict, *, names: dict[str, str], grades: dict[str, str],
                   project_names: dict[str, str]) -> dict:
    """현재 배치 표 전후: 사람마다 옮김(사업이 바뀜)·새로(배치 없던 사람)·빠짐(배치가 없어짐), 사람이 새로 들어간 사업.
    이름·등급·사업 이름은 그때 값이다(이후 데이터와 상관없이 그대로 읽히게)."""
    def by_person(rows):
        out: dict[str, set[str]] = {}
        for r in rows:
            if r.get("person_id") and r.get("project_id"):
                out.setdefault(r["person_id"], set()).add(r["project_id"])
        return out
    rb, ra = _rows(before_files), _rows(files)
    pb, pa = by_person(rb), by_person(ra)

    def row(p: str) -> dict:
        return {"person_id": p, "name": names.get(p), "grade": grades.get(p),
                "from": sorted(pb.get(p, ())), "to": sorted(pa.get(p, ()))}
    old_pairs = {(r["person_id"], r["project_id"]) for r in rb if r.get("person_id")}
    joined_projects = sorted({r["project_id"] for r in ra if r.get("person_id") and (r["person_id"], r["project_id"]) not in old_pairs})
    touched = sorted({j for p in set(pb) | set(pa) if pb.get(p) != pa.get(p) for j in pb.get(p, set()) ^ pa.get(p, set())})
    return {"moved": [row(p) for p in sorted(set(pb) & set(pa)) if pb[p] != pa[p]],
            "joined": [row(p) for p in sorted(set(pa) - set(pb))],
            "left": [row(p) for p in sorted(set(pb) - set(pa))],
            "joined_projects": joined_projects,
            "projects": {j: project_names.get(j, j) for j in sorted(set(touched) | set(joined_projects))}}


def _context(dataset) -> tuple[dict, dict, dict]:
    """(사람 이름 -- 가상 데이터만, 등급, 사업 이름) -- 그 판 데이터에서."""
    from api.routes.justification import display_names
    g = dataset.graph
    return display_names(dataset), {p.id: p.grade.value for p in g.people}, {j.id: j.name for j in g.projects}


def build_record(*, kind: str, note: str, summary: dict, at: str, version: str, before_version: str | None, dataset,
                 before_files: dict, files: dict, by: str | None, prev_dataset=None) -> dict:
    """판 하나의 변경 기록. dataset은 새 판(이름·등급), prev_dataset은 바뀌기 전 판(빠진 사람·없어진 사업의 이름)."""
    names, grades, projects = _context(dataset)
    if prev_dataset is not None:
        n0, g0, p0 = _context(prev_dataset)
        names, grades, projects = {**n0, **names}, {**g0, **grades}, {**p0, **projects}
    rec = {"id": record_id(version, at, kind), "at": at, "kind": kind, "label": KIND_LABEL.get(kind, kind), "note": note,
           "by": by, "dataset_id": getattr(dataset.info, "dataset_id", None), "version": version, "before_version": before_version,
           "summary": summary or {}}
    rec["people"] = people_changes(before_files, files, names=names, grades=grades, project_names=projects)
    rec["review_targets"] = rec["people"]["joined_projects"] if kind in REVIEW_KINDS else []
    # 사유 줄에 나오는 사람(동시 수행 허용·고정·떨어진 사업의 팀원)과 사업의 그때 이름
    rec["names"] = {p: names[p] for p in summary_people(summary or {}) if p in names}
    rec["project_names"] = {j: projects[j] for j in summary_projects(summary or {}) if j in projects}
    return rec


def summary_people(summary: dict) -> set[str]:
    out = {g.get("person_id") for g in summary.get("concurrency_add") or [] if isinstance(g, dict)}
    out |= {p for p in summary.get("freed") or [] if isinstance(p, str)}
    out |= {p for p in summary.get("people") or [] if isinstance(p, str)}
    if isinstance(summary.get("person_id"), str):
        out.add(summary["person_id"])
    return {p for p in out if p}


def summary_projects(summary: dict) -> set[str]:
    out = set()
    for k in ("grade_swaps", "seat_waivers", "budget_raises"):
        out |= {x.get("project_id") for x in summary.get(k) or [] if isinstance(x, dict)}
    for k in ("budget_add", "headcount_add", "skills_add"):
        out |= set((summary.get(k) or {}).keys()) if isinstance(summary.get(k), dict) else set()
    if isinstance(summary.get("project_id"), str):
        out.add(summary["project_id"])
    return {j for j in out if j}


def undo_record(*, popped: dict, at: str, version: str, dataset_id: str | None, by: str | None,
                before_files: dict | None = None, files: dict | None = None, dataset=None, prev_dataset=None) -> dict:
    """되돌리기 한 건: 무엇(종류·메모·시각)을 꺼냈는지. 배치 전후를 알면 사람 변화도 담는다."""
    target = {"version": popped.get("version"), "at": popped.get("at"), "kind": popped.get("kind"), "note": popped.get("note"),
              "label": KIND_LABEL.get(str(popped.get("kind")), popped.get("kind"))}
    rec = {"id": record_id(version, at, "undo"), "at": at, "kind": "undo", "label": KIND_LABEL["undo"],
           "note": f"되돌리기: {target['note'] or target['label']}", "by": by, "dataset_id": dataset_id, "version": version,
           "before_version": popped.get("version"), "summary": {"undone": target}, "review_targets": []}
    if dataset is not None and before_files is not None and files is not None:
        names, grades, projects = _context(dataset)
        if prev_dataset is not None:
            n0, g0, p0 = _context(prev_dataset)
            names, grades, projects = {**n0, **names}, {**g0, **grades}, {**p0, **projects}
        rec["people"] = people_changes(before_files, files, names=names, grades=grades, project_names=projects)
    else:
        rec["people"] = {"moved": [], "joined": [], "left": [], "joined_projects": [], "projects": {}}
    return rec


def backfill(history_entries: list[dict], history, known: set[tuple[str, str, str]]) -> list[dict]:
    """기록이 생기기 전의 이력 항목(시작 판 base 빼고)을 보관 판 zip에서 메운 기록. 이름은 모른다(그 판 데이터를 다시 만들지 않는다) --
    사업 이름만 묶음 표에서 읽는다. 보관 판이 없으면 사람 변화 없이 종류·메모만."""
    from api.workspace import _read_zip
    out = []
    for i, e in enumerate(history_entries):
        if e.get("kind") == "base" or i == 0 or _key(e) in known:
            continue
        prev = history_entries[i - 1]
        before = _files_of(history, prev.get("content_version"), _read_zip)
        after = _files_of(history, e.get("content_version"), _read_zip)
        rec = {"id": record_id(str(e.get("version")), str(e.get("at")), str(e.get("kind"))), "at": e.get("at"), "kind": e.get("kind"),
               "label": KIND_LABEL.get(str(e.get("kind")), e.get("kind")), "note": e.get("note", ""), "by": None, "dataset_id": None,
               "version": e.get("version"), "before_version": prev.get("version"), "summary": e.get("summary") or {},
               "backfilled": True}
        if before is not None and after is not None:
            rec["people"] = people_changes(before, after, names={}, grades=_grades(after) | _grades(before),
                                           project_names=_project_names(before) | _project_names(after))
        else:
            rec["people"] = {"moved": [], "joined": [], "left": [], "joined_projects": [], "projects": {}}
        rec["review_targets"] = rec["people"]["joined_projects"] if rec["kind"] in REVIEW_KINDS else []
        out.append(rec)
    return out


def _files_of(history, content_version, read_zip) -> dict | None:
    if not content_version:
        return None
    blob = history.load_zip(str(content_version))
    return read_zip(blob) if blob is not None else None


def _grades(files: dict) -> dict[str, str]:
    from api.workspace_edits import read_table
    try:
        return {r["person_id"]: r.get("career_grade") or "" for r in read_table(files, "people.csv")[1]}
    except Exception:                                   # noqa: BLE001
        return {}


def _project_names(files: dict) -> dict[str, str]:
    from api.workspace_edits import read_table
    try:
        return {r["project_id"]: r.get("project_name") or r["project_id"] for r in read_table(files, "projects.csv")[1]}
    except Exception:                                   # noqa: BLE001
        return {}


def all_records(root: Path | None = None) -> list[dict]:
    """지금까지의 변경(오래된 것부터): 기록 + 기록 전 이력에서 메운 것."""
    from api.workspace import History
    log = ChangeLog(root).entries()
    hist = History(root)
    extra = backfill(hist.entries(), hist, {_key(e) for e in log})
    ids = {e.get("id") for e in log}
    for e in extra:                                     # 메운 기록도 이름이 겹치지 않게(같은 초에 같은 판)
        base, n = e["id"], 1
        while e["id"] in ids:
            n += 1
            e["id"] = f"{base}-{n}"
        ids.add(e["id"])
    return sorted(log + extra, key=lambda e: str(e.get("at") or ""))      # 같은 초는 덧붙인 순서 그대로(안정 정렬)


CONFIRMS = ("apply_auto", "apply_manual", "apply_emergency", "apply_plan")


def statuses(records: list[dict]) -> dict[str, str]:
    """기록 id → "undone"(되돌림) · "cancelled"(확정 취소). 시간 순서로 훑는다: 되돌리기는 그 앞의 같은 (판·시각·종류) 중 마지막 살아 있는 기록을,
    확정 취소는 그 앞의 같은 (판·시각) 확정 중 마지막 것을 가리킨다 -- 같은 명단을 1초 안에 다시 확정해 키가 같아도 옛 기록을 잘못 짚지 않게.
    취소를 되돌리면 그 확정은 다시 살아난다."""
    out: dict[str, str] = {}
    cancel_target: dict[str, str] = {}
    seen: list[dict] = []

    def last(pred):
        return next((x for x in reversed(seen) if x.get("id") not in out and pred(x)), None)
    for r in records:
        s = r.get("summary") or {}
        if r.get("kind") == "undo" and isinstance(s.get("undone"), dict):
            u = s["undone"]
            t = last(lambda x: (str(x.get("version")), str(x.get("at")), str(x.get("kind"))) == (str(u.get("version")), str(u.get("at")), str(u.get("kind"))))
            if t is not None:
                out[t["id"]] = "undone"
                if t["id"] in cancel_target:
                    out.pop(cancel_target.pop(t["id"]), None)
        elif r.get("kind") == "cancel":
            t = last(lambda x: x.get("kind") in CONFIRMS and (str(x.get("version")), str(x.get("at")))
                     == (str(s.get("target_version")), str(s.get("target_at"))))
            if t is not None:
                out[t["id"]] = "cancelled"
                cancel_target[r["id"]] = t["id"]
        seen.append(r)
    return out
