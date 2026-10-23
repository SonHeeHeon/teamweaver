"""사업 요구 기술 · 발주 자료 API(2026-10-23 사용자 "신규 사업 편성화면에 각 사업에 어떤 기술을 요하는지" · "그 자료를 다운 받을 수 있는 버튼", claude-b).

GET    /api/project-requirements              사업별 요구 기술(등록한 이름·경력 개월·레벨·인원)
GET    /api/project-docs                      사업별 발주 자료(올린 것 + 가상 데이터의 시연 자료)
POST   /api/project-docs/{사업}?kind&filename  원본 본문 = 파일(multipart 의존성 없음 -- 데이터 업로드와 같은 방식), 관리자
GET    /api/project-docs/{사업}/{자료}           내려받기(attachment · nosniff)
DELETE /api/project-docs/{사업}/{자료}           올린 자료 지우기(시연 자료는 못 지움), 관리자
POST   /api/project-requirements/read?filename  원본 본문 = 등록 전 자료 파일 → 필요 기술 제안(글자 뽑기 b + 읽기 함수 claude-a `api/rag/rfp_read`), 관리자
POST   /api/project-meta/read?filename          원본 본문 = 등록 전 자료 파일 → 사업명·기간·예산 등 양식 칸 제안(`api/rag/doc_brief`, 2026-10-23), 관리자
GET    /api/project-docs/{사업}/{자료}/summary    저장된 부서장용 요약(없으면 null) + 보내는 곳
POST   /api/project-docs/{사업}/{자료}/summary    부서장용 요약 만들기(AI, 저장), 관리자

설계 근거는 api/project_docs.py. 요구 기술의 레벨은 계산과 같은 함수(core.ingest.convert.level_from_months)로 붙이고,
이름은 등록한 글자 그대로 + 사전 대표 이름이 다르면 함께(`canonical`) -- 계산은 대표 이름으로 합친 값을 쓴다.
"""
from __future__ import annotations

import hashlib

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


_UNREADABLE = "ZIP 묶음은 글자를 읽지 못한다 -- 묶음을 풀어 PDF·HWP·HWPX·DOCX 파일을 올린다"


async def _text_of(filename: str, data: bytes, ext: str | None = None) -> str:
    """자료 글자(별도 프로세스 + 시간 제한). 못 읽으면 422."""
    try:
        ext = ext or docs_store.ext_of(docs_store.clean_filename(filename))
        text = await anyio.to_thread.run_sync(lambda: docs_store.extract_text_limited(ext, data))
    except docs_store.DocError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if text is None:
        raise HTTPException(status_code=422, detail=_UNREADABLE)
    return text


def _ai_args(dataset: ActiveDataset, client) -> tuple[object, str | None, dict]:
    """(쓸 클라이언트 또는 None, 안 쓴 이유, 읽기 함수 인자) -- 실데이터는 llm_client_for의 외부 차단 규칙, 모델·추론은 다른 AI 기능과 같은 설정."""
    from api.deps import llm_client_for
    from api.rag.justification import justification_settings
    from api.routes.justification import ai_model, ai_timeout_s
    from core.config import load_pricing
    use, why = llm_client_for(dataset, client)
    model = ai_model()
    c = use.with_options(timeout=ai_timeout_s(model), max_retries=0) if use is not None else None
    return use, why, {"client": c, "model": model, "reasoning_effort": justification_settings(model)["reasoning_effort"],
                      "price": load_pricing().get("models", {}).get(model, {})}


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
    text = await _text_of(filename, await _read_capped(request))
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
    from api.rag.rfp_read import MAX_TEXT
    if len(text) > MAX_TEXT:
        # 글자 뽑기는 20만 자까지 주지만 읽기 함수(claude-a)는 앞 MAX_TEXT자에서 말없이 자른다 -- 나눠 읽기가 들어오기 전까지 알린다(폴백 리뷰 N1)
        size = f"{len(text):,}자" + (" 이상" if len(text) >= docs_store.MAX_TEXT_CHARS else "")     # 글자 뽑기 상한에서 잘렸으면 실제는 더 길다(2차 폴백 리뷰 NIT)
        out["warnings"] = [*out.get("warnings", []),
                           f"자료가 길어({size}) 앞 {MAX_TEXT:,}자까지만 읽었다 -- 그 뒤의 요구는 찾지 않았다(직접 확인해 넣는다)."]
    from api.deps import ai_destination
    # 보낸 곳도 싣는다 -- 설정에서 AI 주소만 바꾸면 미리 읽어 둔 안내가 낡는다(2차 폴백 리뷰 NIT)
    return {**out, "dataset_version": dataset.info.version, "ai_available": use is not None, "chars": len(text),
            "ai_destination": ai_destination(dataset, client)}


@router.post("/api/project-meta/read", dependencies=[Depends(require_admin)])
async def read_meta_route(request: Request, filename: str = Query(..., min_length=1, max_length=300),
                          dataset_version: str | None = Query(None, max_length=128),
                          dataset: ActiveDataset = Depends(get_dataset), client=Depends(get_openai_client_or_none)) -> dict:
    """등록 전 발주 자료 → 등록 양식 칸 **제안**(2026-10-23 사용자 "자료에서 메타 정보 따와서 … 프로젝트 이름, 기간, 예산 등을 대신 작성해줄수있나?
    물론 사용자가 보고 고칠수 있도록"). 칸마다 근거 말(원문 확인), 데이터를 바꾸지 않는다 -- 화면이 고른 칸만 양식에 넣는다."""
    from api.deps import ai_destination
    from api.rag.doc_brief import read_project_meta
    _octet_stream(request)
    check_dataset_version(dataset_version, dataset)
    text = await _text_of(filename, await _read_capped(request))
    use, why, kw = _ai_args(dataset, client)
    out = await anyio.to_thread.run_sync(lambda: read_project_meta(text, **kw))
    if use is None:
        out["fallback_reason"] = why
    return {**out, "dataset_version": dataset.info.version, "ai_available": use is not None, "chars": len(text),
            "ai_destination": ai_destination(dataset, client)}


def _doc_bytes(dataset: ActiveDataset, doc: dict) -> bytes:
    path = docs_store.doc_file(_key(dataset), doc)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="자료 파일이 없다(서버 폴더에서 지워졌다)")
    return path.read_bytes()


@router.get("/api/project-docs/{project_id}/{doc_id}/summary")
async def get_summary(request: Request, project_id: str = _PID, doc_id: str = _DOC,
                      dataset: ActiveDataset = Depends(get_dataset), client=Depends(get_openai_client_or_none)) -> dict:
    """저장된 부서장용 요약(자료 해시·지시문 판이 맞을 때만, 없으면 null). 요약을 만들지는 않는다(돈·외부 전송은 사람이 눌러서)."""
    from api.deps import ai_destination
    from api.rag.doc_brief import PROMPT_VERSION
    doc = _find(request, dataset, project_id, doc_id)
    sha = doc.get("sha256")                     # 목록에 적힌 해시(열 때마다 최대 20MiB를 다시 읽지 않게, 폴백 리뷰 NIT 3)
    if not sha:
        data = await anyio.to_thread.run_sync(lambda: _doc_bytes(dataset, doc))
        sha = hashlib.sha256(data).hexdigest()
    rec = docs_store.load_summary(_key(dataset), doc_id, sha, PROMPT_VERSION)
    return {"doc": docs_store.public(doc), "readable": doc["ext"] in docs_store.TEXT_EXTS,
            "summary": rec["summary"] if rec else None, "created_at": rec["created_at"] if rec else None,
            "ai_destination": ai_destination(dataset, client)}


@router.post("/api/project-docs/{project_id}/{doc_id}/summary", dependencies=[Depends(require_admin)])
async def make_summary(request: Request, project_id: str = _PID, doc_id: str = _DOC,
                       dataset: ActiveDataset = Depends(get_dataset), client=Depends(get_openai_client_or_none)) -> dict:
    """부서장용 요약 만들기(2026-10-23 사용자 "각각의 자료를 llm으로 잘 압축 요약한(이 서비스를 쓰는 부서장입장에서 필요로 하는 정보를 잘 요약해서 보여주기)").
    항목마다 근거 말(원문 확인)·숫자 검사(`api/rag/doc_brief`). 만든 요약은 저장해 다시 열면 돈이 들지 않는다. 데이터 판은 바꾸지 않는다."""
    from api.deps import ai_destination
    from api.rag.doc_brief import PROMPT_VERSION, summarize_document
    doc = _find(request, dataset, project_id, doc_id)
    data = await anyio.to_thread.run_sync(lambda: _doc_bytes(dataset, doc))
    text = await _text_of(doc["filename"], data, doc["ext"])
    use, why, kw = _ai_args(dataset, client)
    out = await anyio.to_thread.run_sync(lambda: summarize_document(text, doc["kind"], **kw))
    if use is None:
        out["fallback_reason"] = why
    out["chars"] = len(text)
    rec = None
    if out["method"] == "ai":                   # AI 요약만 저장한다 -- 일시 오류로 생긴 규칙 요약이 다시 열 때마다 나오지 않게(폴백 리뷰 NIT 1)
        rec = await anyio.to_thread.run_sync(lambda: docs_store.save_summary(
            _key(dataset), doc_id, hashlib.sha256(data).hexdigest(), PROMPT_VERSION, out, uploaded=not doc.get("demo")))
    return {"doc": docs_store.public(doc), "readable": True, "summary": out, "created_at": rec["created_at"] if rec else None,
            "ai_destination": ai_destination(dataset, client)}
