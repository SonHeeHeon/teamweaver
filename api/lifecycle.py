"""사업 수명 주기 표시(2026-10-19 -- 사용자 "24년에 시작해서 아직 진행중인 플젝 … 추가 과제가 있어 6달만 회사 인력은 2명만 추가(외주는 여러명)").

화면에 보일 사업 기록만 만든다 -- **계산(MILP·평가기)은 읽지 않는다**(외주 인원도 참고값).
- manifest `lifecycle`(claude-a 생성기 v4, 모양 검사 `core.ingest.lifecycle.check`): {사업: {stage, started, parent?, external_headcount?}}
- manifest `pipeline`의 추가 과제(화면에서 등록·수정, `api.workspace_edits`): parent·external_headcount -- 신규 사업은 이쪽이 원본이라 우선한다.
- 원래 기간: 계획 기간 전에 시작했거나 뒤에 끝나는 사업은 로더가 계획 안으로 자르므로(`core.ingest.convert`), 원문 달을 따로 들고 다닌다.
"""
from __future__ import annotations

import re

from core.ingest.lifecycle import STAGES

ADDON = "추가 과제"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month(v) -> str | None:
    return v if isinstance(v, str) and _MONTH.match(v) else None


def _count(v) -> int | None:
    return v if type(v) is int and v >= 0 else None


def lifecycle_of(manifest: dict | None) -> dict[str, dict]:
    """사업 ID → {stage, started, parent, external_headcount}. 모양이 틀린 항목은 버린다(로더는 경고만 낸다)."""
    m = manifest if isinstance(manifest, dict) else {}
    out: dict[str, dict] = {}
    raw = m.get("lifecycle")
    for pid, e in (raw.items() if isinstance(raw, dict) else ()):
        if not isinstance(e, dict) or e.get("stage") not in STAGES:
            continue
        parent = e.get("parent") if isinstance(e.get("parent"), str) and e.get("parent") != pid else None
        out[str(pid)] = {"stage": e["stage"], "started": _month(e.get("started")), "parent": parent,
                         "external_headcount": _count(e.get("external_headcount")) if parent else None}
    pipe = m.get("pipeline")
    for pid, e in (pipe.items() if isinstance(pipe, dict) else ()):
        if not isinstance(e, dict):
            continue
        parent = e.get("parent") if isinstance(e.get("parent"), str) and e.get("parent") and e.get("parent") != pid else None
        if parent:
            base = out.get(pid) or {"started": None}
            out[pid] = {**base, "stage": ADDON, "parent": parent, "external_headcount": _count(e.get("external_headcount")) or 0}
        elif pid in out and out[pid]["parent"]:
            # 화면에서 추가 과제를 풀었다(pipeline에 상위 없음) -- 생성 때 적힌 연결은 옛 기록이다
            if out[pid]["stage"] == ADDON:
                del out[pid]
            else:
                out[pid] = {**out[pid], "parent": None, "external_headcount": None}
    return out


def original_periods(bundle, project_ids) -> dict[str, dict]:
    """계획 기간 밖으로 이어지는 사업만 원문 기간 {start, end}("YYYY-MM"). bundle이 없으면(고정 JSON fixture) 빈 사전."""
    if bundle is None or not getattr(bundle, "horizon", None):
        return {}
    keep = set(project_ids)
    first, last = bundle.horizon[0], bundle.horizon[-1]
    out: dict[str, dict] = {}
    for row in bundle.tables.get("projects.csv", []):
        pid, s, e = row.get("project_id"), row.get("start_month"), row.get("end_month")
        if pid in keep and hasattr(s, "strftime") and hasattr(e, "strftime") and (s < first or e > last):
            out[pid] = {"start": s.strftime("%Y-%m"), "end": e.strftime("%Y-%m")}
    return out
