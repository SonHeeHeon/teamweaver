"""인사팀 소명 글 저장본(2026-10-18, claude-b -- 야간 배치 2번 "소명 글 미리 쓰기", 사용자 승인 "1,2,3번 좋네. 진행해봐줘").

AI가 쓴 소명 글(검사를 통과한 graphrag)을 저장해 두고, 같은 조건의 요청이 오면 AI를 다시 부르지 않고 돌려준다.
밤 배치(api.nightly)가 미리 채우고, 낮에 화면·PDF에서 성공한 AI 글도 넣는다. 정해진 틀(템플릿)은 즉시 만들어지므로 넣지 않는다.

같은 조건 = 키가 같다:
- 데이터 버전(리뷰 글 판정값·기술 사전 포함 계산 버전), 사업, **배치 명단 전체**(사람·사업·투입률·달별 투입률),
  가중치, 설정 전체(MilpParams), AI(모델·주소·추론 강도),
- **AI에게 넘기는 소명 재료 자체의 해시**(`core.kg.justify.JustificationInput` -- 사실 문구·불리한 사실·팀·근거).
  평가기·점수·지식 그래프 코드가 바뀌어 사실이 달라지면 키도 바뀐다(Codex 리뷰 2026-10-18: 코드 지문만으로는
  core/evaluate·core/scoring 변경을 놓쳤다).
- **생성기 코드 지문**(`api/rag/justification.py`·`api/routes/justification.py`·`core/kg/*.py` 내용 해시 -- 같은 재료라도
  지시문·검사·글 조립이 바뀌면 옛 글은 쓰이지 않는다). 지문은 프로세스가 처음 쓸 때 한 번 잰다(코드를 바꾸면 서버를 다시 띄우므로).

저장: `<데이터 폴더>/justifications/<데이터 버전>.json`(버전마다 한 파일). 옛 데이터의 글은 `prune(지금 버전)`이 지운다 --
부팅 때·밤 실행 시작 때(api/nightly). 실데이터라면 소명 글에도 사번·평가 사실이 들어가므로 업로드 데이터와 같은 비공개 폴더에 둔다.
저장(put)은 다른 버전 파일을 지우지 않는다 -- 데이터 전환 중 늦게 끝난 옛 요청이 지금 데이터의 저장본을 지우던 결함(Codex 리뷰
2026-10-18). 물러난 데이터셋의 요청은 아예 저장하지 않는다(api.routes.justification.save_ai_text).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from api.storage import atomic_write, data_dir, ensure_private_dir

DIRNAME = "justifications"
MAX_PER_VERSION = 400
_ROOT = Path(__file__).resolve().parents[1]
_CODE_FILES = ("api/rag/justification.py", "api/routes/justification.py")
_CODE_GLOBS = ("core/kg/*.py",)

_lock = threading.Lock()
_code_fp: str | None = None


def code_fingerprint() -> str:
    """소명 글을 만드는 코드의 지문(16자). 파일이 없으면 'missing'을 넣어 잰다(지문이 바뀌는 쪽으로)."""
    global _code_fp
    if _code_fp is None:
        h = hashlib.sha256()
        files = [_ROOT / f for f in _CODE_FILES] + sorted(p for g in _CODE_GLOBS for p in _ROOT.glob(g))
        for p in files:
            h.update(p.relative_to(_ROOT).as_posix().encode("utf-8") + b"\0")
            try:
                h.update(p.read_bytes())
            except OSError:
                h.update(b"missing")
        _code_fp = h.hexdigest()[:16]
    return _code_fp


def ai_fingerprint(client, model: str, effort: str | None) -> dict:
    return {"model": model, "base_url": str(getattr(client, "base_url", "") or ""), "effort": effort}


def _entry_tuple(e) -> tuple:
    d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
    monthly = d.get("monthly_alloc") or {}
    return (str(d["person_id"]), str(d["project_id"]), round(float(d["alloc"]), 6),
            tuple(sorted((int(k), round(float(v), 6)) for k, v in monthly.items())))


def _plain(o):
    if isinstance(o, (set, frozenset)):
        return sorted(str(x) for x in o)
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)


def input_fingerprint(inp) -> str:
    """소명 재료(JustificationInput dataclass)의 정규화 해시. 집합은 정렬해 넣는다(순서가 해시를 바꾸지 않게)."""
    import dataclasses
    data = dataclasses.asdict(inp) if dataclasses.is_dataclass(inp) else dict(getattr(inp, "__dict__", {}))
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=_plain).encode("utf-8")).hexdigest()


def key_for(version: str, project_id: str, entries, weights: dict, params, ai: dict, inputs: str = "") -> str:
    payload = {"version": version, "project": project_id, "inputs": inputs,
               "entries": sorted(_entry_tuple(e) for e in entries),
               "weights": dict(sorted((weights or {}).items())),
               "params": params.model_dump() if hasattr(params, "model_dump") else dict(params),
               "ai": ai, "code": code_fingerprint()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _safe(version: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "", version)[:64] or "unknown"


class JustificationCache:
    """한 데이터 폴더의 저장본. 스레드 안전(모듈 잠금). 파일을 매번 읽는다 -- 한 버전 최대 수백 편이라 작다."""

    def __init__(self, root: Path | None = None):
        self.dir = (root if root is not None else data_dir()) / DIRNAME

    def _path(self, version: str) -> Path:
        return self.dir / f"{_safe(version)}.json"

    def _load(self, version: str) -> dict:
        try:
            data = json.loads(self._path(version).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("dataset_version") != version or not isinstance(data.get("items"), dict):
            return {}
        return data["items"]

    def get(self, version: str, key: str) -> dict | None:
        """저장된 글(화면에 보낼 모양) + 언제·어디서 만들었는지. 없으면 None."""
        with _lock:
            rec = self._load(version).get(key)
        if not isinstance(rec, dict) or not isinstance(rec.get("out"), dict):
            return None
        return {**rec["out"], "prepared_at": rec.get("at"), "prepared_by": rec.get("source")}

    def has(self, version: str, key: str) -> bool:
        with _lock:
            return key in self._load(version)

    def put(self, version: str, key: str, out: dict, source: str) -> bool:
        """AI가 쓴 글(graphrag)만 넣는다. 넣었으면 True."""
        if out.get("method") != "graphrag":
            return False
        clean = {k: v for k, v in out.items() if k not in ("prepared_at", "prepared_by")}
        with _lock:
            items = self._load(version)
            items[key] = {"out": clean, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source}
            if len(items) > MAX_PER_VERSION:            # 오래된 것부터 버린다
                for old in sorted(items, key=lambda k: items[k].get("at") or "")[:len(items) - MAX_PER_VERSION]:
                    items.pop(old, None)
            ensure_private_dir(self.dir)
            atomic_write(self._path(version), json.dumps({"dataset_version": version, "items": items},
                                                         ensure_ascii=False).encode("utf-8"))
        return True

    def prune(self, keep_version: str | None) -> int:
        """지금 데이터(keep_version)가 아닌 버전의 글을 지운다. None이면 전부. 지운 파일 수."""
        keep = self._path(keep_version).name if keep_version else None
        n = 0
        with _lock:
            for path in self.dir.glob("*.json") if self.dir.exists() else []:
                if path.name != keep:
                    path.unlink(missing_ok=True)
                    n += 1
        return n

    def count(self, version: str) -> int:
        with _lock:
            return len(self._load(version))
