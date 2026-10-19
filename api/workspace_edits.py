"""작업 데이터의 원천 표를 고치는 순수 함수(2026-10-18, claude-b -- 3가지 핵심 상황 설계 `.omc/plan/2026-10-18-three-scenarios.md`).

작업 데이터 = 지금 켜진 CSV 묶음. 신규 사업 등록·단계 바꾸기·떨어짐·고정(사업·사람)·확정은 모두 이 묶음의 표에 행을 더하거나
고친 **새 판**이다. 여기서는 파일 이름 → 원문(`Files`)을 받아 새 `Files`를 돌려줄 뿐, 검증·켜기는 api/workspace가 업로드와 같은
경로(core.ingest 검증)로 한다. CSV 계약(core/ingest/contract.py)은 바꾸지 않는다 -- 단계·이전 사업은 manifest의 `pipeline` 칸에 둔다.

- 신규 사업 단계: 예정(발주 예정) · 제안(제안 중) · 수주. 표의 `phase`는 예정·제안이면 "제안", 수주면 "실행".
- 고정: `current_assignments.csv`의 `locked=Y`. 사업 전체 또는 지정한 사람만(사용자 2026-10-18: "프로젝트 내 사람에도 적용").
  기존 계산(운영 중 K 비교·보강 후보·최선 n명)이 잠긴 배치를 빼지 않으므로 계산 코드는 그대로다.
- manifest `files`(파일별 sha256)가 있으면 고친 뒤 다시 잰다 -- 로더가 맞는지 검사한다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Files = dict[str, str]
GRADES = ("특급", "고급", "중급", "초급")
SECTORS = ("대내", "대외금융", "대외공공")
STAGES = ("예정", "제안", "수주")
PHASE_OF_STAGE = {"예정": "제안", "제안": "제안", "수주": "실행"}
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

PROJECTS = "projects.csv"
GRADE_REQS = "project_grade_requirements.csv"
SKILL_REQS = "project_skill_requirements.csv"
CURRENT = "current_assignments.csv"
MANIFEST = "manifest.json"


class EditError(ValueError):
    """요청이 지금 데이터와 맞지 않는다(없는 사업·사람, 잠긴 배치 빼기 등) -- 라우트가 422로 바꾼다."""


# --- 표 읽기·쓰기 ---------------------------------------------------------------------------------------------------

def read_table(files: Files, name: str) -> tuple[list[str], list[dict]]:
    text = files.get(name)
    if text is None:
        raise EditError(f"묶음에 {name}이(가) 없다")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return list(reader.fieldnames or []), [dict(r) for r in reader]


def write_table(files: Files, name: str, header: list[str], rows: list[dict]) -> None:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=header, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in header})
    files[name] = out.getvalue()


def read_manifest(files: Files) -> dict:
    try:
        m = json.loads(files[MANIFEST])
    except (KeyError, ValueError) as exc:
        raise EditError("manifest.json을 읽지 못했다") from exc
    if not isinstance(m, dict):
        raise EditError("manifest.json 형식이 아니다")
    return m


def write_manifest(files: Files, m: dict) -> None:
    files[MANIFEST] = json.dumps(m, ensure_ascii=False, indent=1) + "\n"


def rehash(files: Files) -> None:
    """manifest `files`(있으면)를 지금 원문으로 다시 잰다. 마지막에 부른다."""
    m = read_manifest(files)
    listed = m.get("files")
    if isinstance(listed, dict):
        m["files"] = {name: hashlib.sha256(files[name].encode("utf-8")).hexdigest()
                      for name in listed if name in files}
        write_manifest(files, m)


def _copy(files: Files) -> Files:
    return dict(files)


def pipeline_of(m: dict) -> dict[str, dict]:
    p = m.get("pipeline")
    return {str(k): dict(v) for k, v in p.items() if isinstance(v, dict)} if isinstance(p, dict) else {}


CURRENT_HEADER = ["person_id", "project_id", "alloc", "locked"]


def is_locked(row: dict) -> bool:
    """입력 검증(core.ingest)과 같은 정규화 -- " Y "도 고정이다(Codex 리뷰 2026-10-19: 정확히 "Y"만 보면 고정 배치를 뺄 수 있었다)."""
    return str(row.get("locked") or "").strip().upper() == "Y"


def current_table(files: Files) -> tuple[list[str], list[dict]]:
    """현재 배치 표. 선택 파일이라 없으면(백지 묶음) 표준 머리줄의 빈 표 -- 첫 확정이 되게(Codex 리뷰 2026-10-19)."""
    if CURRENT not in files:
        return list(CURRENT_HEADER), []
    return read_table(files, CURRENT)


def _assigned_people(rows: list[dict]) -> set[str]:
    return {r["person_id"] for r in rows if r.get("person_id")}


def _refresh_bench(files: Files, m: dict) -> None:
    """대기 인력 = 사람 표에 있고 현재 배치가 없는 사람(운영 중 데이터에서만 -- bench 칸이 있을 때)."""
    if "bench" not in m:
        return
    _, people = read_table(files, "people.csv")
    _, cur = current_table(files)
    busy = _assigned_people(cur)
    m["bench"] = [p["person_id"] for p in people if p.get("person_id") and p["person_id"] not in busy]


# --- 신규 사업 ------------------------------------------------------------------------------------------------------

class SkillNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    min_months: int = Field(default=24, ge=0, le=360)
    headcount: int = Field(default=1, ge=1, le=50)


class NewProject(BaseModel):
    """부서장이 채우는 신규 사업 양식(모든 사업이 같은 틀)."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    sector: Literal["대내", "대외금융", "대외공공"]
    start_month: str
    end_month: str
    monthly_budget: int = Field(ge=1, le=10_000_000)
    stage: Literal["예정", "제안", "수주"] = "예정"
    predecessor: str | None = Field(default=None, max_length=32)
    grades: dict[Literal["특급", "고급", "중급", "초급"], Annotated[int, Field(ge=0, le=50)]]
    skills: list[SkillNeed] = Field(default=[], max_length=30)

    @field_validator("start_month", "end_month")
    @classmethod
    def _month(cls, v: str) -> str:
        if not _MONTH.match(v):
            raise ValueError("달은 YYYY-MM 형식")
        return v

    @model_validator(mode="after")
    def _check(self) -> "NewProject":
        if self.end_month < self.start_month:
            raise ValueError("종료 달이 시작 달보다 앞이다")
        if sum(self.grades.values()) < 1:
            raise ValueError("등급별 인원이 한 명 이상이어야 한다")
        names = [s.name.strip().lower() for s in self.skills]
        if len(names) != len(set(names)):
            raise ValueError("같은 기술이 두 번 들어 있다")
        return self


def next_project_id(rows: list[dict]) -> str:
    nums = [int(m.group(1)) for r in rows if (m := re.fullmatch(r"J(\d+)", r.get("project_id") or ""))]
    return f"J{(max(nums) + 1 if nums else 1):03d}"


def _put_project_rows(files: Files, pid: str, form: NewProject) -> None:
    h, rows = read_table(files, PROJECTS)
    row = {"project_id": pid, "project_name": form.name.strip(), "sector": form.sector,
           "phase": PHASE_OF_STAGE[form.stage], "start_month": form.start_month, "end_month": form.end_month,
           "monthly_budget": str(form.monthly_budget)}
    rows = [r for r in rows if r.get("project_id") != pid] + [row]
    write_table(files, PROJECTS, h, rows)
    gh, grows = read_table(files, GRADE_REQS)
    grows = [r for r in grows if r.get("project_id") != pid] + [
        # 0명도 행으로 남긴다 -- 변환기는 "적힌 0명 = 그 등급 금지", "안 적힌 등급 = 정원 제한 없음"으로 읽는다(Codex 리뷰 2026-10-19)
        {"project_id": pid, "career_grade": g, "headcount": str(n)} for g, n in form.grades.items()]
    write_table(files, GRADE_REQS, gh, grows)
    sh, srows = read_table(files, SKILL_REQS)
    srows = [r for r in srows if r.get("project_id") != pid] + [
        {"project_id": pid, "skill_name": s.name.strip(), "min_experience_months": str(s.min_months),
         "headcount": str(s.headcount)} for s in form.skills]
    write_table(files, SKILL_REQS, sh, srows)


def add_project(files: Files, form: NewProject, *, registered_at: str = "") -> tuple[Files, str]:
    files = _copy(files)
    _, rows = read_table(files, PROJECTS)
    if form.predecessor and form.predecessor not in {r.get("project_id") for r in rows}:
        raise EditError(f"이전 사업 {form.predecessor}이(가) 지금 데이터에 없다")
    pid = next_project_id(rows)
    _put_project_rows(files, pid, form)
    m = read_manifest(files)
    m.setdefault("scenario", "operating")
    m["proposals"] = [p for p in (m.get("proposals") or []) if p != pid] + [pid]
    pipe = pipeline_of(m)
    pipe[pid] = {"stage": form.stage, "predecessor": form.predecessor, "registered_at": registered_at}
    m["pipeline"] = pipe
    write_manifest(files, m)
    rehash(files)
    return files, pid


def project_form(files: Files, pid: str) -> NewProject:
    """지금 표에서 사업 양식을 다시 만든다(고도화 과제의 '이전 사업 복사', 양식 고치기)."""
    _, rows = read_table(files, PROJECTS)
    row = next((r for r in rows if r.get("project_id") == pid), None)
    if row is None:
        raise EditError(f"없는 사업: {pid}")
    _, grows = read_table(files, GRADE_REQS)
    _, srows = read_table(files, SKILL_REQS)
    stage = pipeline_of(read_manifest(files)).get(pid, {}).get("stage")
    if stage not in STAGES:
        stage = "수주" if row.get("phase") == "실행" else "제안"
    return NewProject(name=row["project_name"], sector=row["sector"], start_month=row["start_month"],
                      end_month=row["end_month"], monthly_budget=int(row["monthly_budget"]), stage=stage,
                      predecessor=pipeline_of(read_manifest(files)).get(pid, {}).get("predecessor"),
                      grades={r["career_grade"]: int(r["headcount"]) for r in grows if r.get("project_id") == pid},
                      skills=[SkillNeed(name=r["skill_name"], min_months=int(r["min_experience_months"]),
                                        headcount=int(r["headcount"])) for r in srows if r.get("project_id") == pid])


def update_project(files: Files, pid: str, *, stage: str | None = None, form: NewProject | None = None) -> Files:
    files = _copy(files)
    m = read_manifest(files)
    pipe = pipeline_of(m)
    if pid not in pipe and pid not in (m.get("proposals") or []):
        raise EditError(f"{pid}은(는) 신규 사업(파이프라인)이 아니다")
    cur = project_form(files, pid)
    new = form or cur
    if stage is not None:
        if stage not in STAGES:
            raise EditError(f"단계는 {', '.join(STAGES)} 중 하나")
        new = new.model_copy(update={"stage": stage})
    _put_project_rows(files, pid, new)
    pipe[pid] = {**pipe.get(pid, {}), "stage": new.stage, "predecessor": new.predecessor}
    m["pipeline"] = pipe
    write_manifest(files, m)
    rehash(files)
    return files


def remove_project(files: Files, pid: str) -> tuple[Files, list[str]]:
    """떨어짐·취소: 사업 행·요구·배치를 지운다. 다른 배치가 없어진 팀원은 대기 인력이 된다. (새 판, 대기가 된 사람)"""
    files = _copy(files)
    m = read_manifest(files)
    if pid not in pipeline_of(m) and pid not in (m.get("proposals") or []):
        raise EditError(f"{pid}은(는) 신규 사업(파이프라인)이 아니다 -- 진행 사업은 지우지 않는다")
    for name in (PROJECTS, GRADE_REQS, SKILL_REQS):
        h, rows = read_table(files, name)
        write_table(files, name, h, [r for r in rows if r.get("project_id") != pid])
    freed: list[str] = []
    if CURRENT in files:
        h, cur = read_table(files, CURRENT)
        team = {r["person_id"] for r in cur if r.get("project_id") == pid}
        rest = [r for r in cur if r.get("project_id") != pid]
        busy = _assigned_people(rest)
        freed = sorted(p for p in team if p not in busy)
        write_table(files, CURRENT, h, rest)
    m["proposals"] = [p for p in (m.get("proposals") or []) if p != pid]
    pipe = pipeline_of(m)
    pipe.pop(pid, None)
    m["pipeline"] = pipe
    _refresh_bench(files, m)
    write_manifest(files, m)
    rehash(files)
    return files, freed


# --- 고정(자물쇠) ---------------------------------------------------------------------------------------------------

def set_lock(files: Files, pid: str, people: list[str] | None, locked: bool) -> Files:
    """사업 전체(people=None) 또는 지정한 사람의 배치를 고정/해제한다."""
    files = _copy(files)
    h, cur = current_table(files)
    team = {r["person_id"] for r in cur if r.get("project_id") == pid}
    if not team:
        raise EditError(f"{pid}에 현재 배치가 없다")
    targets = team if people is None else set(people)
    missing = sorted(targets - team)
    if missing:
        raise EditError(f"{pid} 팀원이 아니다: {', '.join(missing[:5])}")
    flag = "Y" if locked else "N"
    for r in cur:
        if r.get("project_id") == pid and r.get("person_id") in targets:
            r["locked"] = flag
    write_table(files, CURRENT, h, cur)
    rehash(files)
    return files


# --- 프로젝트 동시 수행 허용(2026-10-19 사용자 결정 -- 사람 단위, 사유 필수) -------------------------------------------

def concurrency_of(m: dict) -> list[dict]:
    return [e for e in (m.get("concurrency") or []) if isinstance(e, dict) and isinstance(e.get("person_id"), str)]


def set_concurrency(files: Files, person_id: str, allow: bool, *, max_projects: int = 2, reason: str | None = None,
                    memo: str = "", granted_at: str = "") -> Files:
    """부서장이 그 사람에게 프로젝트 동시 수행을 허용(사유 필수)하거나 해제한다 -- manifest `concurrency`.
    해제는 그 사람이 지금 같은 달 두 사업 이상에 있으면 거절한다(먼저 한 사업에서 빼야 데이터가 맞는다)."""
    from core.ingest.concurrency import MAX_PROJECTS, REASONS
    files = _copy(files)
    _, people = read_table(files, "people.csv")
    if person_id not in {p["person_id"] for p in people}:
        raise EditError(f"없는 사람: {person_id}")
    m = read_manifest(files)
    had = next((e for e in concurrency_of(m) if e["person_id"] == person_id), None)
    grants = [e for e in concurrency_of(m) if e["person_id"] != person_id]
    if allow:
        if reason not in REASONS:
            raise EditError(f"사유를 골라야 한다: {', '.join(REASONS)}")
        if max_projects not in MAX_PROJECTS:
            raise EditError(f"최대 동시 사업 수는 {list(MAX_PROJECTS)} 중 하나")
        # 지금 겹침보다 낮게 다시 걸면 위반이 있는 판이 켜진다(폴백 리뷰 P2) -- 먼저 사업에서 빼야 한다
        if _overlaps(files, person_id) > max_projects:
            raise EditError(f"{person_id}는 지금 같은 달 {_overlaps(files, person_id)}개 사업에 있다 -- 최대 {max_projects}개로 줄일 수 없다")
        grants.append({"person_id": person_id, "max_projects": int(max_projects), "reason": reason,
                       "memo": memo or "", "granted_at": granted_at or ""})
    else:
        if had is None:
            raise EditError(f"{person_id}에게 허용된 프로젝트 동시 수행이 없다")       # 바뀐 것 없는 빈 판을 만들지 않는다
        if _overlaps(files, person_id) > 1:
            raise EditError(f"{person_id}는 지금 같은 달 두 사업 이상에 있다 -- 먼저 한 사업에서 빼고 해제한다")
    if grants:
        m["concurrency"] = sorted(grants, key=lambda e: e["person_id"])
    else:
        m.pop("concurrency", None)
    write_manifest(files, m)
    rehash(files)
    return files


def _overlaps(files: Files, person_id: str) -> int:
    """그 사람의 현재 배치가 같은 달에 몇 개까지 겹치는가(달력 달 기준)."""
    _, cur = current_table(files)
    _, projects = read_table(files, PROJECTS)
    span = {r["project_id"]: (r["start_month"], r["end_month"]) for r in projects}
    mine = [span[r["project_id"]] for r in cur if r.get("person_id") == person_id and r.get("project_id") in span]
    months = {mo for a, _ in mine for mo in (a,)} | {b for _, b in mine}
    return max((sum(1 for a, b in mine if a <= mo <= b) for mo in months), default=0)


# --- 확정 -----------------------------------------------------------------------------------------------------------

def apply_entries(files: Files, entries: list[dict], *, headcount_add: dict[str, dict[str, int]] | None = None,
                  budget_add: dict[str, int] | None = None,
                  skills_add: dict[str, list[dict]] | None = None) -> tuple[Files, dict]:
    """현재 배치를 entries로 바꾼다. 잠긴 배치는 그대로 있어야 한다(빠지면 EditError). 남는 배치는 잠김을 유지하고 새 배치는 N.
    긴급 투입이면 그 사업의 정원(+headcount_add)·월 예산(+budget_add)·요구 기술(skills_add)을 함께 올린다.
    반환: (새 판, 바뀐 내용 {added, removed, changed})."""
    files = _copy(files)
    h, cur = current_table(files)
    seen: set[tuple[str, str]] = set()
    new_rows = []
    old = {(r["person_id"], r["project_id"]): r for r in cur}
    for e in entries:
        key = (str(e["person_id"]), str(e["project_id"]))
        if key in seen:
            raise EditError(f"같은 배치가 두 번 들어 있다: {key[0]}@{key[1]}")
        seen.add(key)
        alloc = float(e["alloc"])
        if not 0.0 < alloc <= 1.0:
            raise EditError(f"투입률은 0 초과 1 이하: {key[0]}@{key[1]}")
        prev = old.get(key)
        new_rows.append({**(prev or {}), "person_id": key[0], "project_id": key[1], "alloc": f"{alloc:g}",
                         "locked": "Y" if prev is not None and is_locked(prev) else "N"})
    dropped_locked = sorted(f"{p}@{j}" for (p, j), r in old.items() if is_locked(r) and (p, j) not in seen)
    if dropped_locked:
        raise EditError("고정한 배치는 뺄 수 없다: " + ", ".join(dropped_locked[:5]))
    write_table(files, CURRENT, h, new_rows)
    new_alloc = {(r["person_id"], r["project_id"]): float(r["alloc"]) for r in new_rows}
    changes = {"added": sorted(f"{p}@{j}" for (p, j) in seen - set(old)),
               "removed": sorted(f"{p}@{j}" for (p, j) in set(old) - seen),
               "changed": sorted(f"{p}@{j}" for (p, j) in seen & set(old)
                                 if abs(float(old[(p, j)]["alloc"]) - new_alloc[(p, j)]) > 1e-9)}
    if headcount_add:
        gh, grows = read_table(files, GRADE_REQS)
        for pid, adds in headcount_add.items():
            for g, n in adds.items():
                if n <= 0:
                    continue
                if g not in GRADES:
                    raise EditError(f"등급이 아니다: {g}")
                row = next((r for r in grows if r.get("project_id") == pid and r.get("career_grade") == g), None)
                if row is None:
                    grows.append({"project_id": pid, "career_grade": g, "headcount": str(n)})
                else:
                    row["headcount"] = str(int(row["headcount"]) + n)
        write_table(files, GRADE_REQS, gh, grows)
    if budget_add:
        ph, prows = read_table(files, PROJECTS)
        for pid, add in budget_add.items():
            row = next((r for r in prows if r.get("project_id") == pid), None)
            if row is None:
                raise EditError(f"없는 사업: {pid}")
            row["monthly_budget"] = str(int(row["monthly_budget"]) + max(0, int(add)))
        write_table(files, PROJECTS, ph, prows)
    if skills_add:
        sh, srows = read_table(files, SKILL_REQS)
        for pid, skills in skills_add.items():
            have = {r["skill_name"].strip().lower() for r in srows if r.get("project_id") == pid}
            for s in skills:
                if s["name"].strip().lower() not in have:
                    srows.append({"project_id": pid, "skill_name": s["name"].strip(),
                                  "min_experience_months": str(int(s.get("min_months", 12))),
                                  "headcount": str(int(s.get("headcount", 1)))})
        write_table(files, SKILL_REQS, sh, srows)
    m = read_manifest(files)
    _refresh_bench(files, m)
    write_manifest(files, m)
    rehash(files)
    return files, changes
