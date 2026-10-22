"""사업 요구 기술 · 발주 자료 API(2026-10-23 사용자 "신규 사업 편성화면에 각 사업에 어떤 기술을 요하는지" · "그 자료를 다운 받을 수 있는 버튼", claude-b).

GET    /api/project-requirements              사업별 요구 기술(등록한 이름·경력 개월·레벨·인원)
GET    /api/project-docs                      사업별 발주 자료(올린 것 + 가상 데이터의 시연 자료)
POST   /api/project-docs/{사업}?kind&filename  원본 본문 = 파일(multipart 의존성 없음 -- 데이터 업로드와 같은 방식), 관리자
GET    /api/project-docs/{사업}/{자료}           내려받기(attachment · nosniff)
DELETE /api/project-docs/{사업}/{자료}           올린 자료 지우기(시연 자료는 못 지움), 관리자
POST   /api/project-requirements/read?filename  원본 본문 = 등록 전 자료 파일 → 필요 기술 제안(글자 뽑기 b + 읽기 함수 claude-a `api/rag/rfp_read`), 관리자

설계 근거는 api/project_docs.py. 요구 기술의 레벨은 계산과 같은 함수(core.ingest.convert.level_from_months)로 붙이고,
이름은 등록한 글자 그대로 + 사전 대표 이름이 다르면 함께(`canonical`) -- 계산은 대표 이름으로 합친 값을 쓴다.
"""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import FileResponse

from api import project_docs as docs_store
from api.admin import require_admin
from api.datasets import ActiveDataset
from api.deps import check_dataset_version, get_dataset, get_openai_client_or_none
from api.workspace import WorkspaceUnavailable, current_files
from api.workspace_edits import EditError, SKILL_REQS, pipeline_of, read_manifest, read_table

router = APIRouter()

_PID = PathParam(pattern=r"^[A-Za-z0-9_.-]{1,32}$")
_DOC = PathParam(pattern=r"^(demo-)?[0-9a-z-]{1,40}$")


def _files(request: Request, dataset: ActiveDataset):
    try:
        return current_files(request.app, dataset)
    except WorkspaceUnavailable:
        return None      # 고정 JSON fixture 등 -- 사업 목록은 그래프에서, 등록 시각·원문 요구는 없다


def _projects(request: Request, dataset: ActiveDataset) -> dict[str, str | None]:
    """지금 데이터의 사업 번호 → 등록 시각(신규 사업 등록으로 생긴 사업만, 없으면 None)."""
    files = _files(request, dataset)
    reg: dict[str, str] = {}
    if files is not None:
        try:
            reg = {pid: str(e["registered_at"]) for pid, e in pipeline_of(read_manifest(files)).items() if e.get("registered_at")}
        except EditError:
            reg = {}
    return {j.id: reg.get(j.id) for j in dataset.graph.projects}


def _key(dataset: ActiveDataset) -> str:
    return docs_store.store_key(dataset.info.dataset_id, dataset.info.synthetic)


def _all_docs(request: Request, dataset: ActiveDataset) -> list[dict]:
    projects = _projects(request, dataset)
    info = dataset.info
    every = docs_store.demo_docs(info.dataset_id, info.synthetic) + docs_store.stored(_key(dataset))
    return [d for d in every if docs_store.visible(d, projects)]


@router.get("/api/project-docs")
async def list_docs(request: Request, dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    out: dict[str, list[dict]] = {}
    for d in _all_docs(request, dataset):
        out.setdefault(d["project_id"], []).append(docs_store.public(d))
    return {"dataset_version": dataset.info.version, "kinds": list(docs_store.KINDS), "formats": list(docs_store.FORMATS),
            "max_bytes": docs_store.MAX_BYTES, "max_per_project": docs_store.MAX_PER_PROJECT, "docs": out}


def _octet_stream(request: Request) -> None:
    # 원본 본문은 application/octet-stream만 -- text/plain 같은 "단순 요청"은 다른 사이트 페이지도 보낼 수 있다(datasets·records 업로드와 같은 규칙)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/octet-stream":
        raise HTTPException(status_code=415, detail="자료는 application/octet-stream 본문으로 올린다")


async def _read_capped(request: Request) -> bytes:
    """본문을 상한까지만 읽는다(Content-Length를 믿지 않고 센다). 본문 상한 미들웨어는 이 경로를 건너뛴다(report.OWN_LIMIT_PREFIXES)."""
    cap = docs_store.MAX_BYTES
    declared = request.headers.get("content-length")
    too_big = HTTPException(status_code=413, detail=f"한 파일은 {cap // (1024 * 1024)}MiB까지다")
    if declared is not None and declared.isdigit() and int(declared) > cap:
        raise too_big
    chunks, seen = [], 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > cap:
            raise too_big
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/project-docs/{project_id}", dependencies=[Depends(require_admin)])
async def upload_doc(request: Request, project_id: str = _PID,
                     kind: str = Query(..., max_length=20), filename: str = Query(..., min_length=1, max_length=300),
                     dataset_version: str | None = Query(None, max_length=128),
                     dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    _octet_stream(request)                      # 폴백 리뷰 S1
    check_dataset_version(dataset_version, dataset)
    if project_id not in _projects(request, dataset):
        raise HTTPException(status_code=404, detail=f"지금 데이터에 없는 사업: {project_id}")
    data = await _read_capped(request)
    # 본문(최대 20MiB)을 읽는 사이 판이 바뀌었으면(되돌리기·재등록) 옛 등록 시각으로 저장하지 않는다(폴백 리뷰 S4)
    if request.app.state.dataset is not dataset:
        check_dataset_version(dataset_version or "", request.app.state.dataset)
    projects = _projects(request, dataset)
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"지금 데이터에 없는 사업: {project_id}")
    try:
        doc = await anyio.to_thread.run_sync(lambda: docs_store.add(
            _key(dataset), project_id, kind, filename, data,
            registered_at=projects[project_id], projects=projects))
    except docs_store.DocError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"doc": docs_store.public(doc)}


def _find(request: Request, dataset: ActiveDataset, project_id: str, doc_id: str) -> dict:
    doc = next((d for d in _all_docs(request, dataset) if d["id"] == doc_id and d["project_id"] == project_id), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="없는 자료다(지워졌거나 다른 데이터의 자료)")
    return doc


@router.get("/api/project-docs/{project_id}/{doc_id}")
async def download_doc(request: Request, project_id: str = _PID, doc_id: str = _DOC,
                       dataset: ActiveDataset = Depends(get_dataset)) -> FileResponse:
    doc = _find(request, dataset, project_id, doc_id)
    path = docs_store.demo_path(doc) if doc.get("demo") else docs_store.stored_path(_key(dataset), doc)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="자료 파일이 없다(서버 폴더에서 지워졌다)")
    # attachment = 브라우저가 열지 않고 저장한다 · nosniff = 형식을 짐작해 실행하지 않는다 · sandbox = 혹시 열려도 스크립트·같은 출처 권한 없음
    return FileResponse(path, media_type=docs_store.media_type(doc["ext"]), filename=doc["filename"],
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                                 "Content-Security-Policy": "sandbox"})


@router.delete("/api/project-docs/{project_id}/{doc_id}", dependencies=[Depends(require_admin)])
async def delete_doc(request: Request, project_id: str = _PID, doc_id: str = _DOC,
                     dataset_version: str | None = Query(None, max_length=128),
                     dataset: ActiveDataset = Depends(get_dataset)) -> dict:
    check_dataset_version(dataset_version, dataset)
    doc = _find(request, dataset, project_id, doc_id)
    if doc.get("demo"):
        raise HTTPException(status_code=409, detail="시연 자료는 지울 수 없다")
    try:
        gone = await anyio.to_thread.run_sync(lambda: docs_store.remove(_key(dataset), project_id, doc_id))
    except docs_store.DocError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if gone is None:
        raise HTTPException(status_code=404, detail="없는 자료다")
    return {"deleted": doc_id}


@router.get("/api/project-requirements")
async def project_requirements(request: Request, dataset: ActiveDataset = Depends(get_dataset),
                               client=Depends(get_openai_client_or_none)) -> dict:
    """사업별 요구 기술. CSV 묶음이면 등록한 원문(이름·경력 개월), 아니면 계산에 쓰는 요구(대표 이름·레벨).
    `ai_destination`: 등록 양식의 "자료에서 필요 기술 읽기"가 자료 글자를 보낼 곳(external·onprem·none) -- 화면이 읽기 전에 알린다
    (폴백 리뷰 S2: 가상 데이터에서 비밀유지 대상 실제 RFP를 고르면 그대로 회사 밖 AI로 간다)."""
    from api.deps import ai_destination
    from core.ingest.convert import level_from_months
    from core.ingest.skill_mapping import dictionary_for
    ids = [j.id for j in dataset.graph.projects]
    out: dict[str, list[dict]] = {j: [] for j in ids}
    files = _files(request, dataset)
    rows = None
    if files is not None:
        try:
            _, rows = read_table(files, SKILL_REQS)
            sd = dictionary_for(read_manifest(files))
        except EditError:
            rows = None
    if rows is not None:
        for r in rows:
            pid = r.get("project_id")
            if pid not in out:
                continue
            try:
                months, head = int(r["min_experience_months"]), int(r["headcount"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(r.get("skill_name") or "").strip()
            canon = sd.canonical(name)
            out[pid].append({"skill": name, "canonical": canon if canon != name else None, "months": months,
                             "level": level_from_months(months), "headcount": head})
    else:
        for j in dataset.graph.projects:
            out[j.id] = [{"skill": r.skill, "canonical": None, "months": None, "level": r.min_level, "headcount": r.headcount}
                         for r in j.requirements]
    return {"dataset_version": dataset.info.version, "requirements": out, "ai_destination": ai_destination(dataset, client)}


@router.post("/api/project-requirements/read", dependencies=[Depends(require_admin)])
async def read_requirements_route(request: Request, filename: str = Query(..., min_length=1, max_length=300),
                                  dataset_version: str | None = Query(None, max_length=128),
                                  dataset: ActiveDataset = Depends(get_dataset), client=Depends(get_openai_client_or_none)) -> dict:
    """등록 전 발주 자료 → 필요 기술 **제안**(2026-10-23 사용자 "자료를 넣으면 llm … 분석해서 알아서 필요 기술에 대해 정의해서 등록해주고 사용자가 보고 …").
    글자 뽑기는 별도 프로세스 + 시간 제한(`extract_text_limited`), 읽기는 claude-a `read_requirements`(사전 개념 id 안에서만·근거 말 원문 확인).
    AI는 자료 글자만 받는다(사람 정보 없음). 실데이터는 `llm_client_for`의 외부 차단 규칙을 따르고, AI가 없으면 규칙으로 읽는다.
    데이터를 바꾸지 않는다 -- 화면이 고른 제안만 등록 양식에 넣는다."""
    _octet_stream(request)
    check_dataset_version(dataset_version, dataset)
    data = await _read_capped(request)
    try:
        ext = docs_store.ext_of(docs_store.clean_filename(filename))
        text = await anyio.to_thread.run_sync(lambda: docs_store.extract_text_limited(ext, data))
    except docs_store.DocError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if text is None:
        raise HTTPException(status_code=422, detail="이 형식(옛 한글 HWP·ZIP 묶음)은 글자를 읽지 못한다 -- PDF·HWPX·DOCX로 저장해 올린다")
    from api.deps import llm_client_for
    from api.rag.justification import justification_settings
    from api.rag.rfp_read import read_requirements
    from api.routes.justification import ai_model, ai_timeout_s
    from core.config import load_pricing
    from core.ingest.skill_mapping import dictionary_of_active
    use, why = llm_client_for(dataset, client)
    model = ai_model()
    sd = dictionary_of_active(dataset)

    def go():
        c = use.with_options(timeout=ai_timeout_s(model), max_retries=0) if use is not None else None
        return read_requirements(text, sd, client=c, model=model,
                                 reasoning_effort=justification_settings(model)["reasoning_effort"],
                                 price=load_pricing().get("models", {}).get(model, {}))
    out = await anyio.to_thread.run_sync(go)
    if use is None:
        out["fallback_reason"] = why
    from api.deps import ai_destination
    # 보낸 곳도 싣는다 -- 설정에서 AI 주소만 바꾸면 미리 읽어 둔 안내가 낡는다(2차 폴백 리뷰 NIT)
    return {**out, "dataset_version": dataset.info.version, "ai_available": use is not None, "chars": len(text),
            "ai_destination": ai_destination(dataset, client)}

