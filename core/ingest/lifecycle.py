"""사업 수명 주기(2026-10-19, 생성기 v4 -- 설계 .omc/plan/2026-10-19-lifecycle-mix.md).

사용자: "24년에 시작해서 아직 진행중인 플젝(24년엔 개발, 25~26년엔 운영 …), 26년엔 운영에 클라우드 마이그레이션 … 추가 과제가 있어 6달만 회사 인력은
2명만 추가(외주는 여러명)". 실제 시작 달은 `projects.csv start_month`(계획 기간 전이면 로더가 자른다), 단계는 이름 머리말과 manifest `lifecycle`:

    "lifecycle": {"J021": {"stage": "운영", "started": "2024-03"},
                  "J025": {"stage": "추가 과제", "started": "2026-11", "parent": "J021", "external_headcount": 4}}

계산은 이 칸을 읽지 않는다(외주 인원도 참고값). 운영 중 묶음의 추가 과제 제안은 작업 데이터의 `pipeline` 모양(parent·external_headcount)에도 들어간다.
"""
from __future__ import annotations

import re

STAGES = ("개발", "운영", "고도화", "추가 과제")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def check(manifest: dict, projects: list[dict], report) -> None:
    """manifest `lifecycle` 모양 검사 -- 계산에 쓰지 않는 기록이라 어긋나면 경고만(없으면 아무것도 하지 않는다)."""
    entries = manifest.get("lifecycle")
    if entries is None:
        return
    if not isinstance(entries, dict):
        report.warn("manifest.json", "lifecycle은 사업 ID → 기록 객체여야 한다", column="lifecycle")
        return
    rows = {str(p.get("project_id")): p for p in projects}
    for pid, e in entries.items():
        where = f"lifecycle[{pid}]"
        if pid not in rows:
            report.warn("manifest.json", f"{where}: 없는 사업", column="lifecycle")
            continue
        if not isinstance(e, dict):
            report.warn("manifest.json", f"{where}: 객체여야 한다", column="lifecycle")
            continue
        if e.get("stage") not in STAGES:
            report.warn("manifest.json", f"{where}: stage는 {list(STAGES)} 중 하나", column="lifecycle")
        started = e.get("started")
        if not isinstance(started, str) or not _MONTH.match(started):
            report.warn("manifest.json", f"{where}: started는 YYYY-MM", column="lifecycle")
        elif started != _month_text(rows[pid].get("start_month")):
            report.warn("manifest.json", f"{where}: started {started}가 projects.csv 시작 달과 다르다", column="lifecycle")
        if "external_headcount" in e and (type(e["external_headcount"]) is not int or e["external_headcount"] < 0):
            report.warn("manifest.json", f"{where}: external_headcount는 0 이상 정수(외주 인원, 참고값)", column="lifecycle")
        parent = e.get("parent")
        if parent is None:
            continue
        if not isinstance(parent, str) or parent not in rows or parent == pid:
            report.warn("manifest.json", f"{where}: 없는 상위 사업 {parent!r}", column="lifecycle")
            continue
        child, up = rows[pid], rows[parent]
        if not (_month_text(up.get("start_month")) <= _month_text(child.get("start_month"))
                and _month_text(child.get("end_month")) <= _month_text(up.get("end_month"))):
            report.warn("manifest.json", f"{where}: 과제 기간이 상위 사업 {parent} 기간 밖이다", column="lifecycle")


def _month_text(v) -> str:
    """로더가 읽은 날짜(date)나 원문 문자열을 YYYY-MM으로."""
    return v.strftime("%Y-%m") if hasattr(v, "strftime") else str(v or "")[:7]


def entries_of(manifest: dict | None) -> dict[str, dict]:
    """사업 → 수명 주기 기록(화면·보고용). 검사를 통과하지 않은 칸도 객체면 그대로 준다."""
    raw = (manifest or {}).get("lifecycle") or {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
