"""인사팀 검토 자료 API(2026-10-19, claude-a -- 사용자 지시, 설계 .omc/plan/2026-10-19-review-doc.md 5절).

- GET  /api/review-docs                                확정(신규 사업 편성·긴급 투입)마다 대상 사업과 자료 상태
- POST /api/review-docs/{version}/{project_id}         만들기(그 확정이 지금 켜진 판일 때만, 이미 있으면 그대로)
- GET  /api/review-docs/{version}/{project_id}         자료
- PUT  /api/review-docs/{version}/{project_id}         부서장 편집(본문·해명/이의·판단/합의·상황 메모) -- 시스템 사실은 받지 않는다(422)
- POST /api/review-docs/{version}/{project_id}/draft   AI 본문 초안(저장본이 있으면 다시 부르지 않는다, force면 다시)
- POST /api/review-docs/{version}/{project_id}/pdf     그 사업 한 편의 PDF(내보낸 사실을 자료에 남긴다)
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.admin import admin_id, require_admin
from api.deps import get_openai_client_or_none
from api.review_docs import (AGREEMENT, ReviewDocError, ReviewDocStore, build_doc, confirm_entries, confirms, history_state, now,
                             view)

log = logging.getLogger(__name__)
router = APIRouter()


def _history():
    from api.workspace import History
    return History()


def _find_confirm(version: str) -> dict | None:
    return next((c for c in confirms(_history().entries()) if c["version"] == version), None)


def _names_view(doc: dict, *, history: str, active: bool) -> dict:
    from api.routes.justification import with_names
    names = doc.get("names") or {}
    out = view(doc, reverted=history == "reverted", active=active, history=history)
    out["facts"] = [{**f, "text": with_names(f["text"], names)} for f in out["facts"]]
    if out.get("exported_facts"):
        out["exported_facts"] = [{**f, "text": with_names(f["text"], names)} for f in out["exported_facts"]]
    for k in ("body_ai", "body"):
        out[k] = with_names(out.get(k), names)
    return out


def _load(version: str, project_id: str) -> tuple[dict, str]:
    """(자료, 이력 상태: in_history·reverted·out_of_history). 이력 밖으로 밀려났을 뿐인 확정은 되돌린 것이 아니다(폴백 리뷰 P1)."""
    try:
        doc = ReviewDocStore().get(version, project_id)
    except ReviewDocError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="아직 만들지 않은 검토 자료다.")
    return doc, history_state(_history().entries(), doc["confirm"])


@router.get("/api/review-docs")
def list_review_docs(request: Request) -> dict:
    hist = _history()
    entries = hist.entries()
    active_version = request.app.state.dataset.info.version
    graph = request.app.state.dataset.graph
    stored = {(d["confirm"]["version"], d["project_id"]): d for d in ReviewDocStore().all()}
    out = []
    seen_versions = set()
    for c in reversed(confirms(entries)):
        seen_versions.add(c["version"])
        try:
            before, after = confirm_entries(hist, c, graph if c["version"] == active_version else None)
            from core.evaluate.review_moves import target_changes
            targets = target_changes(before, after)
        except ReviewDocError:
            targets = []
        projects = []
        for pid in sorted(set(targets) | {p for (v, p) in stored if v == c["version"]}):
            d = stored.get((c["version"], pid))
            name = graph.projects[graph.project_index[pid]].name if pid in graph.project_index else (d or {}).get("project_name", pid)
            projects.append({"project_id": pid, "name": name, "exists": d is not None, "status": (d or {}).get("status"),
                             "movers": sum(1 for j in (d or {}).get("moves", {}).get("joined", []) if j["origin"] == "moved"),
                             "objections": sum(1 for n in (d or {}).get("notes", []) if n.get("type") == "objection")})
        out.append({**{k: c[k] for k in ("version", "kind", "at", "note")}, "active": c["version"] == active_version,
                    "reverted": False, "projects": projects})
    # 되돌려진 확정의 자료도 목록에 남긴다(열 수는 있고 내보내기는 잠김)
    for (v, pid), d in sorted(stored.items(), key=lambda kv: kv[1]["confirm"].get("at") or "", reverse=True):
        if v in seen_versions:
            continue
        c = d["confirm"]
        row = next((r for r in out if r["version"] == v), None)
        if row is None:
            row = {**{k: c.get(k) for k in ("version", "kind", "at", "note")}, "active": False,
                   "reverted": history_state(entries, c) == "reverted", "projects": []}
            out.append(row)
        row["projects"].append({"project_id": pid, "name": d.get("project_name", pid), "exists": True, "status": d.get("status"),
                                "movers": sum(1 for j in d.get("moves", {}).get("joined", []) if j["origin"] == "moved"),
                                "objections": sum(1 for n in d.get("notes", []) if n.get("type") == "objection")})
    return {"confirms": out}


def _create(request: Request, version: str, project_id: str) -> dict:
    store = ReviewDocStore()
    try:
        existing = store.get(version, project_id)
    except ReviewDocError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if existing is not None:
        return existing
    confirm = _find_confirm(version)
    if confirm is None:
        raise HTTPException(status_code=404, detail="이 확정을 찾을 수 없다(되돌렸거나 이력 밖).")
    dataset = request.app.state.dataset
    if dataset.info.version != version:
        raise HTTPException(status_code=409, detail="이후 다른 변경이 있어 새로 만들 수 없다(그 확정이 지금 켜진 판일 때만 만든다).")
    kg = dataset.knowledge_graph()
    if kg is None:
        raise HTTPException(status_code=409, detail="검토 자료는 CSV 묶음 데이터에서만 만들 수 있다.")
    try:
        before, after = confirm_entries(_history(), confirm, dataset.graph)
    except ReviewDocError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    from core.evaluate.review_moves import target_changes
    if project_id not in target_changes(before, after):
        raise HTTPException(status_code=404, detail="이 확정으로 사람이 새로 들어간 사업이 아니다.")
    from api.routes.workspace import dataset_params
    doc = build_doc(dataset=dataset, kg=kg, params=dataset_params(request.app, dataset), confirm=confirm,
                    project_id=project_id, before=before, after=after)
    return store.create(doc)


@router.post("/api/review-docs/{version}/{project_id}", dependencies=[Depends(require_admin)])
async def create_review_doc(version: str, project_id: str, request: Request) -> dict:
    doc = await anyio.to_thread.run_sync(_create, request, version, project_id)
    return _names_view(doc, history="in_history", active=True)


@router.get("/api/review-docs/{version}/{project_id}")
def get_review_doc(version: str, project_id: str, request: Request) -> dict:
    doc, state = _load(version, project_id)
    return _names_view(doc, history=state, active=request.app.state.dataset.info.version == version)


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=64)
    type: Literal["reply", "objection"]
    text: str = Field(max_length=2000)
    author: str | None = Field(default=None, max_length=64)    # 받지만 서버가 덮어쓴다(새 글이면 로그인 아이디)
    at: str | None = Field(default=None, max_length=40)
    orphan: bool | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("해명·이의에는 글이 있어야 한다(이의는 사유 필수)")
        return v.strip()


class AgreementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal[AGREEMENT] | None = None      # type: ignore[valid-type]
    memo: str = Field(default="", max_length=500)


class JudgmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=3000)
    agreement: AgreementIn = AgreementIn()


class EditIn(BaseModel):
    """부서장이 고칠 수 있는 것만. 시스템 사실(facts·moves 등)을 보내면 422(extra=forbid)."""
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    body: str | None = Field(default=None, max_length=20000)
    notes: Annotated[list[NoteIn], Field(max_length=300)] | None = None
    judgments: dict[str, JudgmentIn] | None = None
    situation_memo: str | None = Field(default=None, max_length=2000)


@router.put("/api/review-docs/{version}/{project_id}", dependencies=[Depends(require_admin)])
def edit_review_doc(version: str, project_id: str, body: EditIn, request: Request) -> dict:
    _, state = _load(version, project_id)
    if state == "reverted":
        raise HTTPException(status_code=409, detail="되돌려진 확정의 자료는 고칠 수 없다.")
    user = admin_id()

    def apply(doc: dict) -> None:
        if doc["revision"] != body.revision:
            raise ReviewDocError("conflict")
        if body.body is not None:
            doc["body"] = body.body if body.body.strip() else None
        if body.notes is not None:
            old = {(n["key"], n["type"], n["text"]): n for n in doc.get("notes", [])}
            doc["notes"] = [old.get((n.key, n.type, n.text)) or {"key": n.key, "type": n.type, "text": n.text,
                                                                  "author": user, "at": now()} for n in body.notes]
        if body.judgments is not None:
            people = {j["person_id"] for j in doc["moves"]["joined"]}
            bad = sorted(set(body.judgments) - people)
            if bad:
                raise ReviewDocError(f"이 사업에 새로 들어온 사람이 아니다: {', '.join(bad[:5])}")
            old_j = doc.get("judgments", {})

            def stamp(p: str, j: JudgmentIn) -> dict:
                new = {"text": j.text, "agreement": j.agreement.model_dump()}
                prev = old_j.get(p) or {}
                same = {"text": prev.get("text"), "agreement": prev.get("agreement")} == new
                # 내용이 그대로면 처음 쓴 사람·시각을 남긴다(저장마다 덮지 않게, 폴백 리뷰 P3)
                return {**new, "author": prev.get("author") if same else user, "at": prev.get("at") if same else now()}
            doc["judgments"] = {p: stamp(p, j) for p, j in body.judgments.items()}
        if body.situation_memo is not None:
            doc["situation_memo"] = body.situation_memo
        doc["revision"] += 1
        doc["updated_at"] = now()

    try:
        doc = ReviewDocStore().update(version, project_id, apply)
    except ReviewDocError as exc:
        if str(exc) == "conflict":
            raise HTTPException(status_code=409, detail="다른 곳에서 먼저 고쳤다 -- 다시 불러와 고친다.") from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _names_view(doc, history=state, active=request.app.state.dataset.info.version == version)


def _draft(request: Request, version: str, project_id: str, client, force: bool) -> dict:
    from api.routes.justification import AI_SLOTS, _client_with_timeout, build_input
    from api.routes.workspace import dataset_params
    from core.kg.justify import focus_input
    from core.scoring.engine import ScoringEngine
    store = ReviewDocStore()
    doc = store.get(version, project_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="아직 만들지 않은 검토 자료다.")
    if doc.get("body_ai") and not force:
        return doc                                       # 저장된 초안을 다시 쓴다(AI 비용 없음)
    dataset = request.app.state.dataset
    if dataset.info.version != version:
        raise HTTPException(status_code=409, detail="이후 다른 변경이 있어 초안을 새로 만들 수 없다(이미 쓴 본문은 고칠 수 있다).")
    kg = dataset.knowledge_graph()
    _, after = confirm_entries(_history(), doc["confirm"], dataset.graph)
    from core.optimize.milp import MilpParams
    params = MilpParams(**doc["milp_params"]) if doc.get("milp_params") else dataset_params(request.app, dataset)
    eng = ScoringEngine(dataset.graph)
    S, C = eng.skill_matrix({}), eng.synergy_matrix()
    inp = build_input(kg, project_id, after, dataset.graph, S, C, params)
    if doc["confirm"]["kind"] == "emergency":
        inp = focus_input(inp, {j["person_id"] for j in doc["moves"]["joined"]})
    use_ai = client is not None and AI_SLOTS.acquire(blocking=False)
    try:
        summary = draft_summary(dataset, inp, _client_with_timeout(client) if use_ai else None,
                                busy=client is not None and not use_ai)
        out = None if summary["text"] else _rule_body(dataset, inp)
    finally:
        if use_ai:
            AI_SLOTS.release()

    def apply(d: dict) -> None:
        # 응답 글은 표시 이름으로 바꿔 저장한다 -- 부서장이 고치는 본문과 같은 꼴
        if summary["text"]:
            d["body_ai"], d["body_ai_method"], d["body_ai_check"] = summary["text"], summary["method"], summary["check"]
            d.pop("body_ai_addendum", None)
        else:                               # 요약을 만들 사실이 모자라면(규칙 요약도 검사에 걸림) 예전 규칙 본문 -- 요약 실패 사유는 남긴다
            d["body_ai"], d["body_ai_method"] = out.get("text") or "", out.get("method")
            d["body_ai_check"] = summary["check"]
            if out.get("addendum"):
                d["body_ai_addendum"] = out["addendum"]
        d["revision"] += 1
    return store.update(version, project_id, apply)


def _rule_body(dataset, inp) -> dict:
    """요약을 만들 수 없을 때의 예전 규칙 본문(AI 없음, 요약을 다시 부르지 않는다)."""
    from api.rag.justification import generate_justification
    from api.routes.justification import ai_model, apply_names, display_names, public_view
    return apply_names(public_view(generate_justification(None, ai_model(), inp)), display_names(dataset))


CHECK_RULES = "S1 없는 사실 · S2 허용 밖 말 · 3문장 이하 · 근거 2~4개 · 불리한 사실 포함"


def draft_summary(dataset, inp, client, *, busy: bool = False) -> dict:
    """검토 자료 AI 초안 = 짧은 요약 + 사실 검사(2026-10-20 사용자 "간단 명료하게 요약하여 제공하되 사실 외 정보를 쓰면 안되고 요약 작성된걸 다시
    사실에 근거한 정보만 있는지 검토하는 프로세스까지"). What-if 소명 요약과 같은 코드(claude-a `api.rag.justification.generate_summary`):
    AI는 사실 번호 {F#}와 정해진 연결 말만 쓰고 숫자·충족 여부 문구는 서버가 채운다. 생성 단계에서 `verify_summary`가 문장마다 검사해 걸리면
    규칙 요약으로 바꾼다(거르기). 저장 전에 **저장할 글 자체**를 다시 확인한다(기록): 검사를 통과한 원문을 다시 채우면 저장 글과 똑같은지,
    채운 글에 자리표시가 남거나 근거 칩 수가 다르지 않은지(`render_ok`), 원문이 검사를 통과하는지 -- 셋 다 맞아야 ok(대체 리뷰 S1).
    긴 본문은 부르지 않는다(AI 한 번). busy = AI 자리가 없어 AI 없이 만든 경우(사유 ai_busy).
    반환: {text(표시 이름) 또는 None, method: graphrag|rule|None, check: 검사 기록}."""
    from api.rag.justification import generate_summary
    from api.routes.justification import ai_model, display_names, with_names
    from core.kg.justify import render, render_ok, rule_summary_raw, verify_summary
    out = generate_summary(client, ai_model(), inp)
    reason = "ai_busy" if busy and out.get("summary_fallback_reason") == "no_client" else out.get("summary_fallback_reason")
    facts = {f.id: f for f in inp.facts}
    names = display_names(dataset)
    check = {"ok": False, "method": out.get("summary_method"), "fallback_reason": reason, "cited": [],
             "facts_total": len(inp.facts), "rules": CHECK_RULES}
    text = out.get("summary")
    if not text:                                          # 규칙 요약도 검사를 통과하지 못했다(사실이 모자람 등) -- 사유만 남긴다
        return {"text": None, "method": None, "check": check}
    raw = out.get("summary_raw") or rule_summary_raw(inp) or ""
    ver = verify_summary(raw, inp)
    same = render(raw, inp) == text and render_ok(raw, text)          # 검사한 원문이 저장할 글을 만든 원문인가
    check.update(ok=bool(ver["ok"] and same),
                 cited=[{"id": fid, "text": with_names(facts[fid].text, names), "adverse": facts[fid].adverse}
                        for fid in ver["cited"] if fid in facts])
    if not check["ok"]:                                   # 저장할 글이 검사와 맞지 않으면 쓰지 않는다
        return {"text": None, "method": None, "check": {**check, "fallback_reason": "recheck_failed"}}   # 재확인 실패가 먼저(리뷰 NIT)
    return {"text": with_names(text, names), "method": out.get("summary_method"), "check": check}


@router.post("/api/review-docs/{version}/{project_id}/draft", dependencies=[Depends(require_admin)])
async def draft_review_doc(version: str, project_id: str, request: Request, force: bool = False,
                           client=Depends(get_openai_client_or_none)) -> dict:
    _, state = _load(version, project_id)
    if state == "reverted":
        raise HTTPException(status_code=409, detail="되돌려진 확정의 자료다.")
    doc = await anyio.to_thread.run_sync(_draft, request, version, project_id, client, force)
    return _names_view(doc, history=state, active=request.app.state.dataset.info.version == version)


@router.post("/api/review-docs/{version}/{project_id}/pdf", dependencies=[Depends(require_admin)])
async def pdf_review_doc(version: str, project_id: str, request: Request) -> Response:
    doc, state = _load(version, project_id)
    if state == "reverted":
        raise HTTPException(status_code=409, detail="되돌려진 확정의 자료는 내보낼 수 없다.")
    from api.routes.report import render_review_pdf
    payload = {"kind": "review", "doc": _names_view(doc, history=state, active=False)}
    pdf = await render_review_pdf(request, payload)
    _, state = _load(version, project_id)               # 그리는 동안 되돌렸으면 내보내지 않는다(Codex 리뷰)
    if state == "reverted":
        raise HTTPException(status_code=409, detail="PDF를 만드는 동안 이 확정이 되돌려졌다 -- 내보내지 않았다.")

    def mark(d: dict) -> None:
        d["status"], d["exported_at"], d["exported_facts"] = "exported", now(), d["facts"]
        d["revision"] += 1
    await anyio.to_thread.run_sync(ReviewDocStore().update, version, project_id, mark)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="review-{project_id}.pdf"'})
