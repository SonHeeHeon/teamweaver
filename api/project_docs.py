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

# 입찰공고문(2026-10-23): 사용자가 사전규격 → 입찰공고 순서로 실제 자료를 받아 왔다 -- 공고문에는 사업명·기간·예산·마감이 있다(정보 채우기에 쓴다)
KINDS = ("사전규격", "입찰공고문", "제안요청서(RFP)", "과업지시서", "사업 개요", "기타")
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
        _summary_file(key, doc["id"]).unlink(missing_ok=True)
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
                        "file": f, "demo": True, "sha256": str(d.get("sha256") or "")})
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


def doc_file(key: str, doc: dict) -> Path:
    return demo_path(doc) if doc.get("demo") else stored_path(key, doc)


# --- 부서장용 요약 저장(2026-10-23 사용자 "각각의 자료를 llm으로 잘 압축 요약한 … 각각 보여줄수있는 화면? 팝업?") ---------------------------
# 요약은 처음 한 번 사람이 눌러 만들고(AI, api/rag/doc_brief), 자료 보관 폴더 summaries/에 자료마다 한 파일로 둔다 -- 다시 열면 돈 들지 않는다.
# 자료 해시와 지시문 판(doc_brief.PROMPT_VERSION)이 같을 때만 다시 쓴다. 시연 자료(저장소, 읽기 전용)의 요약도 데이터 폴더에 둔다.
def _summary_file(key: str, doc_id: str) -> Path:
    return _folder(key) / "summaries" / f"{hashlib.sha256(doc_id.encode('utf-8')).hexdigest()[:24]}.json"


def load_summary(key: str, doc_id: str, sha256: str, version: str) -> dict | None:
    path = _summary_file(key, doc_id)
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("자료 요약 파일을 읽지 못했다(%s): %s", path, exc)
        return None
    if not isinstance(rec, dict) or rec.get("sha256") != sha256 or rec.get("version") != version or not isinstance(rec.get("summary"), dict):
        return None
    return rec


def save_summary(key: str, doc_id: str, sha256: str, version: str, summary: dict, *, uploaded: bool) -> dict | None:
    """요약 저장. 올린 자료는 목록에 아직 있을 때만 쓴다 -- AI를 기다리는 사이 자료를 지웠으면 그 인용문을 남기지 않는다(None, 폴백 리뷰 S9)."""
    rec = {"doc_id": doc_id, "sha256": sha256, "version": version,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summary}
    folder = _folder(key)
    path = _summary_file(key, doc_id)
    with _lock:
        if uploaded and not any(d["id"] == doc_id for d in _read_index(folder, strict=False)):
            return None
        ensure_private_dir(folder)                     # 보관 폴더를 처음 만드는 곳이 여기일 수 있다 -- 0700(폴백 리뷰 NIT 4)
        ensure_private_dir(path.parent)
        atomic_write(path, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
    return rec


# --- 글자 뽑기(AI 필요 기술 읽기용, 2026-10-23 사용자 "그 자료를 넣으면 llm … 을 통해서 분석해서 알아서 필요 기술에 대해 정의") -----------
# 읽기 함수는 claude-a `api/rag/rfp_read`(사용자 결정 "a·b 나눠서") -- 여기는 파일 형식만 다룬다. 계약: 깨진 파일은 DocError만 낸다.
# 옛 한글(HWP 5.x)은 `api/hwp5`가 읽는다(2026-10-23 사용자 결정 "HWP 읽기 추가" -- 나라장터 사전규격 첨부 대부분이 HWP, 배포용·암호 문서는 DocError).
# ZIP 묶음은 None -- 화면이 "묶음을 풀어 올린다"고 안내한다.
# 남이 만든 파일이다: 서버는 extract_text_limited(별도 프로세스 + 시간 제한 + 동시 2개)로 부른다. 첫 판의 폴백 리뷰 지적(2026-10-23)을 고쳤다 --
# 문단마다 하위 트리를 다시 돌던 O(n²)(4,000셀 표 5.7초) → 한 번만 도는 iterparse, 큰 데이터를 spawn 시작 인자로 넘겨 시간 제한 밖에서 멈춤 →
# 시작 뒤 파이프로 보내고 타이머로 끝낸다(시드 포트폴리오와 같은 함정), 글자 없는 PDF 한 편이 9초·736MB → 동시 개수 상한.
# 상한(2026-10-23 사용자 결정 "나눠 동시에 읽기" -- 긴 RFP는 claude-a가 조각으로 나눠 읽는다): 6만 자·80쪽 → 20만 자·200쪽.
# 실측: 글자가 빽빽한 220쪽 한글 PDF에서 20만 자 2.2초·36.5만 자 3.9초(시간 제한 20초 안). AI가 읽는 양은 rfp_read가 정한다.
MAX_TEXT_CHARS = 200_000
MAX_PDF_PAGES = 200
EXTRACT_SECONDS = 20.0
EXTRACT_PARALLEL = 2
SLOT_WAIT_SECONDS = 5.0
REPLY_MAX_BYTES = 6 * MAX_TEXT_CHARS + 4096   # 자식 답(JSON) 상한 -- 글자 하나가 \uXXXX(6바이트)로 바뀌어도 들어가게
_MAX_XML_BYTES = 16 * 1024 * 1024         # 압축 안 한 XML 합계 상한(압축 폭탄 막기) -- 실제 HWPX는 글자당 약 24바이트(6천 자 15만 바이트), 20만 자 ≈ 5MB
_CHILD_MEMORY = 1536 * 1024 * 1024         # Linux에서만 자식 메모리 한도(macOS는 RLIMIT_AS가 강제되지 않는다)
# 확장자 → (본문 XML 이름, 글자 태그, 문단 태그). hp:t·w:t·a:t 안에는 탭·줄바꿈 같은 자식이 섞인다(혼합 내용)
_XML_PARTS = {
    "hwpx": (r"Contents/section[0-9]+\.xml", "t", "p"),
    "docx": (r"word/document\.xml", "t", "p"),
    "pptx": (r"ppt/slides/slide[0-9]+\.xml", "t", "p"),
    "xlsx": (r"xl/sharedStrings\.xml", "t", "si"),
}
_BREAKS = {"tab": " ", "lineBreak": "\n", "br": "\n", "cr": "\n"}
# 글자로 치지 않는 하위 요소: XLSX 후리가나(rPh) -- 'ETLイーティーエル'처럼 붙어 나왔다(폴백 리뷰 NIT)
_SKIP = {"rPh"}
_extract_slots = threading.BoundedSemaphore(EXTRACT_PARALLEL)
TEXT_EXTS = ("pdf", "hwp", *_XML_PARTS)        # 글자를 꺼내는 형식(화면 projectDocsApi.TEXT_EXTS와 같다)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _t_text(el) -> str:
    """글자 요소 안의 글자 -- 자식(탭·줄바꿈·형광펜 등) 뒤에 이어지는 글자(tail)까지(첫 판은 el.text만 읽어 본문을 잃었다)."""
    out = [el.text or ""]
    for child in el:
        out.append(_BREAKS.get(_local(child.tag), ""))
        out.append("".join(child.itertext()))
        out.append(child.tail or "")
    return "".join(out)


def _part_key(name: str) -> list:
    # isdigit()는 "²"도 참이라 int()가 터진다 -- 십진 숫자만 숫자로 본다
    return [int(x) if re.fullmatch(r"[0-9]+", x) else x for x in re.split(r"([0-9]+)", name)]


def _xml_lines(raw: bytes, tag: str, para: str) -> list[str]:
    """한 번만 돈다(iterparse). 문단마다 버퍼를 쌓고, 안쪽 문단(표 칸)이 시작되면 바깥 문단의 앞 글을 먼저 내보낸다(읽는 순서)."""
    import io
    from xml.etree import ElementTree as ET
    lines: list[str] = []
    stack: list[list[str]] = []
    in_t = skip = 0

    def flush(buf: list[str]) -> None:
        line = "".join(buf)
        if line.strip():
            lines.append(line)
        buf.clear()

    try:
        for event, el in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
            name = _local(el.tag)
            if event == "start":
                if name in _SKIP:
                    skip += 1
                elif name == para:
                    if stack:
                        flush(stack[-1])
                    stack.append([])
                elif name == tag:
                    in_t += 1
                continue
            if name in _SKIP:
                skip -= 1
            elif name == tag:
                in_t -= 1
                if in_t == 0 and not skip:    # 글자 요소 안의 글자 요소는 바깥이 한 번에 읽는다
                    text = _t_text(el)
                    if stack:
                        stack[-1].append(text)
                    elif text.strip():        # 문단 밖 글자도 버리지 않는다
                        lines.append(text)
            elif name in _BREAKS and in_t == 0 and stack:
                # DOCX w:tab·w:br, PPTX a:br은 글자 요소의 **형제**다 -- 빠뜨리면 'Java5년 이상Oracle'처럼 붙어 규칙 읽기가 기술을 놓친다(폴백 리뷰 S1)
                stack[-1].append(_BREAKS[name])
            elif name == para and stack:
                flush(stack.pop())
                el.clear()
    except ET.ParseError:
        pass                                  # 잘린 XML이어도 그때까지 읽은 문단은 남긴다(폴백 리뷰 NIT)
    while stack:
        flush(stack.pop())
    return lines


def _xml_text(data: bytes, pattern: str, tag: str, para: str) -> str:
    import io
    import zipfile
    import zlib
    rx = re.compile(pattern)
    out, used = [], 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = sorted((n for n in zf.namelist() if rx.fullmatch(n)), key=_part_key)
        for n in names:
            used += zf.getinfo(n).file_size
            if used > _MAX_XML_BYTES:
                break
            with zf.open(n) as f:
                raw = f.read(_MAX_XML_BYTES + 1)
            if len(raw) > _MAX_XML_BYTES:
                break
            out += _xml_lines(raw, tag, para)       # 외부 엔티티를 풀지 않는다(표준 라이브러리), 엔티티 폭증은 expat 2.4.1+가 막는다
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error, ValueError,
            OSError, KeyError, RecursionError, LookupError) as exc:     # LookupError: XML 선언의 낯선 encoding
        # 깨진 압축(CRC)·암호 걸린 항목·지원 안 하는 압축 방식 -- 읽기 실패로만 알린다(호출자는 DocError만 잡는다)
        raise DocError(f"문서를 읽지 못했다: {type(exc).__name__}") from exc
    return "\n".join(out)


def extract_text(ext: str, data: bytes) -> str | None:
    """자료의 글자(앞 MAX_TEXT_CHARS자). 읽지 못하는 형식(ZIP 묶음)은 None. 깨진 파일·배포용 한글은 DocError(그 밖의 예외는 내지 않는다).
    서버 요청 경로에서는 extract_text_limited를 쓴다."""
    ext = ext.lower()
    if ext not in FORMATS:
        return None
    check_bytes(ext, data)
    if ext == "pdf":
        import io
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(data))
            parts = []
            for page in reader.pages[:MAX_PDF_PAGES]:
                parts.append(page.extract_text() or "")
                if sum(map(len, parts)) > MAX_TEXT_CHARS:
                    break
        except Exception as exc:       # 남이 만든 파일이라 pypdf가 어떤 예외든 낼 수 있다 -- 읽기 실패로만 알린다
            raise DocError(f"PDF를 읽지 못했다: {type(exc).__name__}") from exc
        text = "\n".join(parts)
    elif ext in _XML_PARTS:
        text = _xml_text(data, *_XML_PARTS[ext])
    elif ext == "hwp":
        from api.hwp5 import HwpError, hwp_text
        try:
            text = hwp_text(data, MAX_TEXT_CHARS)
        except HwpError as exc:
            raise DocError(str(exc)) from exc
    else:
        return None
    return clean_text(text)


_SURROGATE = re.compile(r"[\ud800-\udfff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(text: str) -> str:
    """뽑은 글자 정리: 짝 없는 대리 문자 → U+FFFD(pypdf는 잘못된 ToUnicode를 surrogatepass로 풀어 남긴다 -- UTF-8로 못 바꿔 한 편이 통째로 실패했다),
    줄바꿈·탭 밖의 제어 문자 제거(ToUnicode 없는 CID 글꼴 PDF의 NUL 등), 공백 정리, 앞 MAX_TEXT_CHARS자(2차 폴백 리뷰 SHOULD)."""
    text = _CONTROL.sub("", _SURROGATE.sub("\ufffd", unicodedata.normalize("NFC", text)))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r" *\n *", "\n", text).strip()[:MAX_TEXT_CHARS]         # 줄 앞뒤 공백은 뗀다


def _reply(conn, status: str, value) -> None:
    # 남이 만든 파일을 해석한 프로세스의 답은 pickle이 아니라 JSON 바이트로 받는다(폴백 리뷰 NIT -- 심층 방어)
    conn.send_bytes(json.dumps({"status": status, "value": value}, ensure_ascii=False).encode("utf-8"))


def _extract_child(conn) -> None:
    """자식 프로세스: 확장자와 파일을 파이프로 받는다(시작 인자로 받지 않는다 -- 시작 중 죽으면 부모가 영원히 기다린다)."""
    try:
        import sys
        if sys.platform.startswith("linux"):
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (_CHILD_MEMORY, _CHILD_MEMORY))
        ext = conn.recv_bytes(16).decode("ascii")
        data = conn.recv_bytes(MAX_BYTES + 16)
        _reply(conn, "ok", extract_text(ext, data))
    except DocError as exc:
        _reply(conn, "error", str(exc))
    except BaseException as exc:     # noqa: BLE001 -- 자식 프로세스: 무엇이든 부모에게 실패로 돌려준다
        try:
            _reply(conn, "error", f"문서를 읽지 못했다: {type(exc).__name__}")
        except Exception:            # noqa: BLE001 -- 부모가 이미 끊었다
            pass
    finally:
        conn.close()


def extract_text_limited(ext: str, data: bytes, *, seconds: float = EXTRACT_SECONDS) -> str | None:
    """extract_text를 별도 프로세스(spawn)에서 시간 제한과 함께 돈다. 시작·보내기·읽기 어디서 막혀도 `seconds` 뒤 프로세스를 끝내고 DocError.
    동시에 EXTRACT_PARALLEL개까지(남은 자리가 없으면 그 시간만큼 기다린 뒤 DocError). 부르는 쪽은 스레드에서 부른다(anyio.to_thread)."""
    import multiprocessing as mp
    import time
    ext = ext.lower()
    if ext not in FORMATS:
        return None
    check_bytes(ext, data)
    if ext not in TEXT_EXTS:
        return None                           # ZIP 묶음 -- 프로세스를 띄울 것 없다
    # 자리 기다림은 따로 잰다 -- 늦게 얻은 자리가 시간 제한을 다 써 버려 "넘겨 멈췄다"로 잘못 알리지 않게(폴백 리뷰 NIT)
    if not _extract_slots.acquire(timeout=min(seconds, SLOT_WAIT_SECONDS)):
        raise DocError("다른 자료를 읽는 중이다 -- 잠시 뒤 다시 한다")
    deadline = time.monotonic() + seconds
    proc = parent = None
    timer = None
    try:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe(duplex=True)
        proc = ctx.Process(target=_extract_child, args=(child,), daemon=True)
        try:
            proc.start()
        except (OSError, RuntimeError, ValueError) as exc:
            raise DocError(f"문서 글자 읽기 프로세스를 시작하지 못했다: {type(exc).__name__}") from exc
        child.close()
        # 보내기가 막혀도(자식이 시작 중 멈춤) 시간 제한 안에 끝나게 -- 시간이 되면 자식을 끝내 파이프를 끊는다
        timer = threading.Timer(max(0.0, deadline - time.monotonic()), proc.kill)
        timer.daemon = True
        timer.start()
        try:
            parent.send_bytes(ext.encode("ascii"))
            parent.send_bytes(data)
            ready = parent.poll(max(0.0, deadline - time.monotonic()))
            if not ready:
                raise DocError(f"문서 글자 읽기가 {seconds:g}초를 넘겨 멈췄다(쪽이 아주 많거나 복잡한 파일)")
            reply = json.loads(parent.recv_bytes(REPLY_MAX_BYTES).decode("utf-8"))
            status, value = reply["status"], reply["value"]
        except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
            if time.monotonic() >= deadline:
                raise DocError(f"문서 글자 읽기가 {seconds:g}초를 넘겨 멈췄다(쪽이 아주 많거나 복잡한 파일)") from exc
            raise DocError("문서 글자 읽기 프로세스가 결과 없이 끝났다") from exc
    finally:
        if timer is not None:
            timer.cancel()
        if proc is not None and proc.is_alive():
            proc.kill()
        if proc is not None and proc.pid is not None:
            proc.join(timeout=5)
        if parent is not None:
            parent.close()
        _extract_slots.release()
    if status != "ok":
        raise DocError(value)
    return value
