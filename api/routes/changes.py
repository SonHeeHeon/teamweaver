"""변경 보고서 API(2026-10-22, claude-a -- 사용자 "액션 하나하나에 대한 것도 보고서로 남길 수 있게하고 기간으로 잡고 어떤 변경들을 했는지
각각에 대한 인사팀 보고 사항들을 정리해서 함께 pdf에 묶는것도", 원천 `api.change_log`).

- GET  /api/changes?start=&end=     기간 안의 변경(최신 먼저): 무엇이 바뀌었나·사유 줄·상태(취소·되돌림)·인사팀 검토 자료 상태
- POST /api/changes/report {ids}    고른 변경들(한 건이면 건별 보고서)을 한 PDF로 -- 표지 요약 + 변경마다 내용·사유 + 그 확정의 검토 자료 전문

묶음 PDF는 검토 자료의 "내보냄" 상태를 바꾸지 않는다(보고·열람용 -- 결재 첨부는 자료별 PDF 그대로).
"""
from __future__ import annotations

from datetime import datetime, timezone

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from api.admin import require_admin
from api.change_log import KIND_LABEL, REVIEW_KINDS, all_records, statuses

router = APIRouter()
ORIGIN = {"bench": "대기 인력", "moved": "진행 사업에서 옮겨 옴"}


def _parse(ts: str | None, name: str) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name}은(는) ISO 시각이어야 한다") from exc
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _at(rec: dict) -> datetime | None:
    try:
        return _parse(str(rec.get("at") or ""), "at")
    except HTTPException:
        return None


def _won(n) -> str:
    return f"{int(n):,}"


def reason_lines(rec: dict) -> list[str]:
    """사유·결정 줄(화면·PDF 공용). 이름·사업 이름은 그 기록에 담긴 그때 값."""
    s = rec.get("summary") or {}
    names = {**(rec.get("names") or {}), **{x["person_id"]: x.get("name") for g in ("moved", "joined", "left")
                                           for x in (rec.get("people") or {}).get(g, []) if x.get("name")}}
    pname = {**((rec.get("people") or {}).get("projects") or {}), **(rec.get("project_names") or {})}
    who = lambda p: names.get(p) or p                                                  # noqa: E731
    pj = lambda j: f"{pname[j]}({j})" if pname.get(j) and pname[j] != j else str(j)   # noqa: E731
    kind = rec.get("kind")
    out: list[str] = []
    if kind in ("apply_auto", "apply_manual", "apply_emergency", "apply_plan"):
        if s.get("situation"):
            out.append(f"상황: {s['situation']}")
        for g in s.get("grade_swaps") or []:
            from core.evaluate.grade_swap import ORDER
            dirn = "아래" if ORDER.index(g["grade"]) > ORDER.index(g["seat"]) else "위"
            out.append(f"등급 대체: {pj(g['project_id'])} {g['seat']} 자리 {g['count']}개를 {g['grade']}으로({dirn} 등급) — 사유 {g.get('reason') or '-'}")
        for w in s.get("seat_waivers") or []:
            out.append(f"빼 온 자리 메우지 않음: {pj(w['project_id'])} {w['grade']} {w['count']}자리(정원에서 뺌) — 사유 {w.get('reason') or '-'}")
        for b in s.get("budget_raises") or []:
            out.append(f"예산 증액: {pj(b['project_id'])} 월 +{_won(b['amount'])} — 사유 {b.get('reason') or '-'}")
        for j, amt in (s.get("budget_add") or {}).items():
            if amt:
                out.append(f"월 예산 증액(등급 대체·긴급 투입 증원): {pj(j)} +{_won(amt)}")
        for j, by in (s.get("headcount_add") or {}).items():
            add = ", ".join(f"{g} +{n}" for g, n in (by or {}).items() if n)
            if add:
                out.append(f"정원 증원: {pj(j)} {add}")
        for j, sk in (s.get("skills_add") or {}).items():
            if sk:
                out.append(f"요구 기술 추가: {pj(j)} {', '.join(sk)}")
        for g in s.get("concurrency_add") or []:
            memo = f" · {g['memo']}" if g.get("memo") else ""
            out.append(f"프로젝트 동시 수행 허용: {who(g['person_id'])} — 사유 {g.get('reason') or '-'}{memo}")
        if s.get("locks_released"):
            out.append(f"고정을 풀고 확정: {len(s['locks_released'])}건")
    elif kind == "cancel":
        out.append(f"취소한 확정: {s.get('target_note') or rec.get('note', '').removeprefix('확정 취소: ')} (확정 시각 {s.get('target_at') or '-'})")
    elif kind == "undo":
        u = s.get("undone") or {}
        out.append(f"되돌린 변경: {u.get('label') or u.get('kind')} — {u.get('note') or ''}")
    elif kind in ("add_project", "update_project"):
        if s.get("project_id"):
            out.append(f"사업: {pj(s['project_id'])}" + (f" · 단계 {s['stage']}" if s.get("stage") else ""))
    elif kind == "remove_project":
        freed = [who(p) for p in s.get("freed") or []]
        out.append(f"사업: {pj(s.get('project_id'))}" + (f" · 대기로 돌아간 팀원 {len(freed)}명({', '.join(freed)})" if freed else ""))
    elif kind in ("lock", "unlock"):
        ppl = [who(p) for p in s.get("people") or []]
        out.append(f"사업: {pj(s.get('project_id'))}" + (f" · 사람 {', '.join(ppl)}" if ppl else " · 사업 전체"))
    elif kind in ("concurrency", "concurrency_off"):
        line = f"{who(s.get('person_id'))}"
        if s.get("allow"):
            line += f" — 같은 달 최대 {s.get('max_projects')}개 사업, 사유 {s.get('reason') or '-'}" + (f" · {s['memo']}" if s.get("memo") else "")
        out.append(line)
    elif kind == "skill_names":
        ds = s.get("decisions") or []
        out.append(f"기술 이름 결정 {len(ds)}건: " + ", ".join(f"{d.get('name')} → {d.get('concept') or d.get('decision')}" for d in ds[:12])
                   + (" 외" if len(ds) > 12 else ""))
    elif kind == "hr_sync":
        out.append(f"받은 표: {', '.join(s.get('changed') or []) or '-'}" + (f" · 기준 달 {s['as_of']}" if s.get("as_of") else ""))
    return out


def _review_rows(rec: dict, stored: dict) -> list[dict]:
    pname = (rec.get("people") or {}).get("projects") or {}
    out = []
    for j in rec.get("review_targets") or []:
        d = stored.get((str(rec.get("version")), j))
        out.append({"project_id": j, "name": (d or {}).get("project_name") or pname.get(j, j), "exists": d is not None,
                    "status": (d or {}).get("status"),
                    "objections": sum(1 for n in (d or {}).get("notes", []) if n.get("type") == "objection")})
    return out


def item_view(rec: dict, status: dict, stored: dict) -> dict:
    people = rec.get("people") or {}
    return {"id": rec["id"], "at": rec.get("at"), "kind": rec.get("kind"), "label": rec.get("label") or KIND_LABEL.get(rec.get("kind"), rec.get("kind")),
            "note": rec.get("note", ""), "by": rec.get("by"), "dataset_id": rec.get("dataset_id"), "version": rec.get("version"),
            "status": status.get(rec["id"]), "backfilled": bool(rec.get("backfilled")),
            "people": {k: people.get(k, []) for k in ("moved", "joined", "left")},
            "counts": {k: len(people.get(k, [])) for k in ("moved", "joined", "left")},
            "projects": people.get("projects") or {},
            "reasons": reason_lines(rec), "review": _review_rows(rec, stored),
            "review_kind": rec.get("kind") in REVIEW_KINDS}


def _stored() -> dict:
    from api.review_docs import ReviewDocStore
    return {(d["confirm"]["version"], d["project_id"]): d for d in ReviewDocStore().all()}


def _in_period(rec: dict, start: datetime | None, end: datetime | None) -> bool:
    at = _at(rec)
    if at is None:
        return start is None and end is None
    return (start is None or at >= start) and (end is None or at < end)


@router.get("/api/changes")
def list_changes(start: str | None = None, end: str | None = None) -> dict:
    s, e = _parse(start, "start"), _parse(end, "end")
    records = all_records()
    st, stored = statuses(records), _stored()
    items = [item_view(r, st, stored) for r in reversed(records) if r.get("kind") != "base" and _in_period(r, s, e)]
    return {"items": items, "total": len(items), "start": start, "end": end}


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=100)
    start: str | None = Field(default=None, max_length=40)      # 표지에 적는 기간(고르기는 ids)
    end: str | None = Field(default=None, max_length=40)


def totals(items: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    for it in items:
        by_kind[it["label"]] = by_kind.get(it["label"], 0) + 1
    live = [it for it in items if not it["status"]]
    return {"changes": len(items), "by_kind": by_kind,
            "moved": sum(it["counts"]["moved"] for it in live), "joined": sum(it["counts"]["joined"] for it in live),
            "left": sum(it["counts"]["left"] for it in live),
            "review_docs": sum(len(it["review"]) for it in items), "review_missing": sum(1 for it in items for r in it["review"] if not r["exists"]),
            "objections": sum(r["objections"] for it in items for r in it["review"]),
            "undone_or_cancelled": len(items) - len(live)}


def report_payload(ids: list[str], start: str | None, end: str | None) -> dict:
    from api.review_docs import ReviewDocStore, history_state
    from api.routes.review_docs import _history, _names_view
    records = all_records()
    st, stored = statuses(records), _stored()
    want = set(ids)
    picked = [r for r in records if r.get("id") in want]               # 오래된 것부터(보고서 읽는 순서)
    missing = sorted(want - {r.get("id") for r in picked})
    if missing:
        raise HTTPException(status_code=404, detail=f"변경 기록에 없는 항목: {', '.join(missing[:5])}")
    hist_entries = _history().entries()
    items = []
    for r in picked:
        it = item_view(r, st, stored)
        docs = []
        for row in it["review"]:
            d = ReviewDocStore().get(str(r.get("version")), row["project_id"]) if row["exists"] else None
            if d is not None:
                state = history_state(hist_entries, d["confirm"])
                docs.append({"project_id": row["project_id"], "doc": _names_view(d, history=state, active=False)})
            else:
                docs.append({"project_id": row["project_id"], "name": row["name"], "doc": None})
        items.append({**it, "review_docs": docs})
    return {"kind": "changes", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "period": {"start": start, "end": end}, "items": items, "totals": totals(items)}


@router.post("/api/changes/report", dependencies=[Depends(require_admin)])
async def change_report_pdf(body: ReportIn, request: Request) -> Response:
    payload = await anyio.to_thread.run_sync(report_payload, body.ids, body.start, body.end)
    from api.routes.report import render_review_pdf
    pdf = await render_review_pdf(request, payload)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    name = f"change-report-{stamp}-{len(body.ids)}.pdf"
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})
