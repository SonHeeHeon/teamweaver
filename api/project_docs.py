"""발주 자료 보관(2026-10-23 사용자 "신규 사업 등록 할 때 첨부 할 수 있어야하고 … 그 자료를 다운 받을 수 있는 버튼", claude-b).

발주 자료 = 발주처가 입찰 전후에 낸 사업 설명(사전규격 · 제안요청서(RFP) · 과업지시서) 또는 영업이 정리한 사업 개요.
이름은 사용자 결정("발주 자료" + 종류) -- RFP만으로는 발주 예정(사전규격)·수주 뒤(과업지시서)와 맞지 않고,
"공고 자료"는 공고가 없는 민간(초청 RFP) 사업과 맞지 않는다.

- **계산과 분리한다**: 자료는 계산 입력이 아니다. 작업 데이터 판(CSV 묶음·manifest)에 넣으면 올릴 때마다 계산 버전이 바뀌어
  캐시·미리 계산이 버려진다 -- 데이터 폴더 `project_docs/<데이터 id 해시>/`에 index.json + `<자료 id>.<확장자>`로 따로 둔다(0600, 원자적 쓰기).
- **사업 번호는 다시 쓰일 수 있다**(등록 되돌리기·떨어짐 뒤 새 등록이 max+1 번호를 받는다): 올릴 때 그 사업의 등록 시각(manifest pipeline
  `registered_at`, 없으면 None)을 적고, 지금 그 번호의 등록 시각과 **똑같을 때만** 보인다(None == None 포함). 시연 자료도 데이터별 등록 시각을
  목록에 적는다(폴백 리뷰 M1 -- "없으면 보인다"였을 때 떨어뜨린 J020 자리에 새로 등록한 사업에 J020 가상 자료가 붙었다).
  등록 시각은 초 단위다 -- 같은 초 안의 되돌리기 + 재등록은 가르지 못한다(화면 조작으로는 생기기 어렵다).
- **보관 폴더 키 = (가상 여부, 데이터 id)**: 같은 id를 쓰는 가상 데이터와 실데이터가 자료를 나눠 쓰지 않게.
- **형식**: 공공 RFP는 한글(HWP) 파일·여러 파일 묶음이 많다 -- PDF·HWP·HWPX·DOCX·XLSX·PPTX·ZIP. 확장자와 파일 첫 바이트를 함께 본다
  (이름만 바꾼 다른 파일을 받지 않게 -- 내용이 안전하다는 뜻은 아니다). 받을 때는 내려받기로만 준다(라우트가 attachment·nosniff·CSP sandbox).
- **시연 자료**: 저장소 `demo/project_docs/index.json`(scripts/make_demo_project_docs.py)을 가상 데이터이고 데이터 id가 맞을 때만 함께 보인다.
  지울 수 없다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from api.storage import atomic_write, data_dir, ensure_private_dir

log = logging.getLogger(__name__)

KINDS = ("사전규격", "제안요청서(RFP)", "과업지시서", "사업 개요", "기타")
MAX_BYTES = 20 * 1024 * 1024
MAX_PER_PROJECT = 10
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"      # HWP 5.x(복합 문서)
_ZIP = b"PK\x03\x04"                             # HWPX·DOCX·XLSX·PPTX·ZIP
# 확장자 → (받을 때 형식, 허용하는 첫 바이트)
FORMATS: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "pdf": ("application/pdf", (b"%PDF-",)),
    "hwp": ("application/x-hwp", (_OLE,)),
    "hwpx": ("application/hwp+zip", (_ZIP,)),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", (_ZIP,)),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", (_ZIP,)),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", (_ZIP,)),
    "zip": ("application/zip", (_ZIP,)),
}
DEMO_ROOT = Path(__file__).resolve().parent.parent / "demo" / "project_docs"
_DOC_ID = re.compile(r"^[0-9a-f]{16}$")
_lock = threading.Lock()


class DocError(Exception):
    """받을 수 없는 자료(형식·크기·개수) -- 라우트가 422로 바꾼다."""


def clean_filename(raw: str) -> str:
    """사람이 보는 파일 이름: 경로·제어 문자를 떼고 120자로 줄인다(확장자는 남긴다). 저장 이름은 자료 id다."""
    name = unicodedata.normalize("NFC", str(raw)).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip().strip(".")
    if not name:
        raise DocError("파일 이름이 비었다")
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 119 - len(ext)] + dot + ext) if dot and len(ext) <= 8 else name[:120]
    return name


def ext_of(filename: str) -> str:
    stem, dot, ext = filename.rpartition(".")
    ext = ext.lower()
    if not dot or not stem or ext not in FORMATS:
        raise DocError(f"받는 형식은 {', '.join(e.upper() for e in FORMATS)}이다")
    return ext


def check_bytes(ext: str, data: bytes) -> None:
    """형식 혼동만 막는다(이름만 바꾼 다른 형식) -- 내용이 안전하다는 뜻은 아니다. 받을 때 attachment·nosniff·CSP sandbox로 연다."""
    if not data:
        raise DocError("빈 파일이다")
    if len(data) > MAX_BYTES:
        raise DocError(f"한 파일은 {MAX_BYTES // (1024 * 1024)}MiB까지다")
    ok = (b"%PDF-" in data[:1024]) if ext == "pdf" else data.startswith(FORMATS[ext][1])   # PDF 머리는 첫 1024바이트 안이면 된다(규격)
    if not ok:
        raise DocError(f"파일 내용이 {ext.upper()} 형식이 아니다(이름만 바꾼 파일은 받지 않는다)")


def media_type(ext: str) -> str:
    return FORMATS.get(ext, ("application/octet-stream",))[0]


def visible(doc: dict, projects: dict[str, str | None]) -> bool:
    """지금 데이터에 그 사업이 있고, 올릴 때의 등록 시각과 지금 등록 시각이 어긋나지 않는다(번호를 다시 쓴 다른 사업이 아님)."""
    pid = doc.get("project_id")
    if pid not in projects:
        return False
    return doc.get("registered_at") == projects[pid]


def public(doc: dict) -> dict:
    """화면에 주는 칸(저장 경로·해시는 주지 않는다)."""
    return {"id": doc["id"], "project_id": doc["project_id"], "kind": doc["kind"], "filename": doc["filename"],
            "size": doc["size"], "uploaded_at": doc.get("uploaded_at"), "demo": bool(doc.get("demo"))}


# --- 올린 자료 ------------------------------------------------------------------------------------------------------

def store_key(dataset_id: str, synthetic: bool | None) -> str:
    """보관 폴더 키: 가상 데이터와 실데이터가 같은 데이터 id를 써도 자료를 나눠 쓰지 않는다(폴백 리뷰)."""
    return f"{'synthetic' if synthetic is True else 'real'}:{dataset_id}"


def _folder(key: str) -> Path:
    # 데이터 id는 사용자가 정한 글자라 경로에 그대로 쓰지 않는다
    return data_dir() / "project_docs" / hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _read_index(folder: Path, *, strict: bool) -> list[dict]:
    path = folder / "index.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data.get("docs") if isinstance(data, dict) else None
        if not isinstance(docs, list):
            raise ValueError("docs가 목록이 아니다")
        return [d for d in docs if isinstance(d, dict) and _DOC_ID.match(str(d.get("id", "")))
                and str(d.get("ext")) in FORMATS]
    except (OSError, ValueError) as exc:
        if strict:   # 올리기·지우기는 읽지 못한 목록을 덮어쓰지 않는다(남은 자료를 잃지 않게)
            raise DocError(f"자료 목록 파일을 읽지 못했다: {exc}") from exc
        log.warning("발주 자료 목록을 읽지 못했다(%s): %s", folder, exc)
        return []


def _write_index(folder: Path, key: str, docs: list[dict]) -> None:
    body = json.dumps({"key": key, "docs": docs}, ensure_ascii=False, indent=1) + "\n"
    atomic_write(folder / "index.json", body.encode("utf-8"))


def stored(key: str) -> list[dict]:
    return _read_index(_folder(key), strict=False)


def add(key: str, project_id: str, kind: str, filename: str, data: bytes, *,
        registered_at: str | None, projects: dict[str, str | None]) -> dict:
    if kind not in KINDS:
        raise DocError(f"종류는 {' · '.join(KINDS)} 중 하나다")
    name = clean_filename(filename)
    ext = ext_of(name)
    check_bytes(ext, data)
    folder = _folder(key)
    with _lock:
        docs = _read_index(folder, strict=True)
        mine = [d for d in docs if d.get("project_id") == project_id and visible(d, projects)]
        if len(mine) >= MAX_PER_PROJECT:
            raise DocError(f"사업 하나에 자료는 {MAX_PER_PROJECT}개까지다 -- 필요 없는 자료를 지우고 올린다")
        doc = {"id": secrets.token_hex(8), "project_id": project_id, "kind": kind, "filename": name, "ext": ext,
               "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
               "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "registered_at": registered_at}
        ensure_private_dir(folder)
        atomic_write(folder / f"{doc['id']}.{ext}", data)       # 파일 먼저 -- 목록이 없는 파일을 가리키지 않게
        _write_index(folder, key, docs + [doc])
    return doc


def remove(key: str, project_id: str, doc_id: str) -> dict | None:
    folder = _folder(key)
    with _lock:
        docs = _read_index(folder, strict=True)
        doc = next((d for d in docs if d["id"] == doc_id and d.get("project_id") == project_id), None)
        if doc is None:
            return None
        _write_index(folder, key, [d for d in docs if d is not doc])   # 목록 먼저 -- 파일 지우기가 실패해도 안 보인다
        (folder / f"{doc['id']}.{doc['ext']}").unlink(missing_ok=True)
    return doc


def stored_path(key: str, doc: dict) -> Path:
    return _folder(key) / f"{doc['id']}.{doc['ext']}"


# --- 시연 자료 ------------------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _demo_index() -> tuple[dict, ...]:
    path = DEMO_ROOT / "index.json"
    if not path.exists():
        return ()
    try:
        docs = json.loads(path.read_text(encoding="utf-8"))["docs"]
        if not isinstance(docs, list):
            raise TypeError("docs가 목록이 아니다")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("시연 발주 자료 목록을 읽지 못했다: %s", exc)
        return ()
    out = []
    for d in docs:
        try:
            f = str(d["file"])
            reg = d["registered_at"]          # {데이터 id: 그 데이터에서 그 사업의 등록 시각 또는 null}
            if not isinstance(reg, dict) or not all(isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in reg.items()):
                raise TypeError("registered_at은 {데이터 id: 시각|null}")
            # 파일은 demo/project_docs 바로 아래 이름만 -- 목록을 고쳐도 다른 곳 파일을 내주지 않게
            if "/" in f or "\\" in f or f.startswith(".") or f.rpartition(".")[2] not in FORMATS or d["kind"] not in KINDS:
                raise ValueError(f"받지 않는 항목: {f}")
            out.append({"id": f"demo-{d['id']}", "project_id": str(d["project_id"]), "kind": d["kind"], "filename": str(d["filename"]),
                        "ext": f.rpartition(".")[2], "size": int(d["size"]), "uploaded_at": None, "registered": dict(reg),
                        "file": f, "demo": True})
        except (KeyError, TypeError, ValueError) as exc:     # 항목 하나가 깨져도 나머지는 보인다
            log.warning("시연 발주 자료 항목을 건너뛴다: %s", exc)
    return tuple(out)


def demo_docs(dataset_id: str, synthetic: bool | None) -> list[dict]:
    """그 데이터의 시연 자료. 등록 시각은 데이터마다 다르다(연말형 J018은 2026-10-01, 운영 중 J018은 없음)."""
    if synthetic is not True:      # 가상 데이터에만 -- 실데이터가 같은 id를 써도 가짜 자료가 붙지 않게
        return []
    return [{**d, "registered_at": d["registered"][dataset_id]} for d in _demo_index() if dataset_id in d["registered"]]


def demo_path(doc: dict) -> Path:
    return DEMO_ROOT / doc["file"]
