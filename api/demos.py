"""시연 데이터 묶음 고르기(2026-10-06, claude-a 요청 "데이터 탭에서 demo/org-n100-operating 을 고를 수 있게").

`demo/` 아래 manifest.json이 있는 폴더만 고를 수 있다(허용 목록 -- 경로를 요청으로 받지 않는다). 고른 이름은 데이터 폴더의
`demo_choice.json`에 기억해 재기동해도 그 묶음으로 뜬다. 고르지 않았으면 예전처럼 TEAMWEAVER_DEMO_BUNDLE(없으면 고정 fixture).
"""
from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path

from core.config import REPO_ROOT

log = logging.getLogger(__name__)

DEMO_DIR = REPO_ROOT / "demo"
CHOICE_FILE = "demo_choice.json"


def _demo_dir() -> Path:
    return Path(os.environ.get("TEAMWEAVER_DEMO_DIR", "") or DEMO_DIR)


def _count_rows(path: Path) -> int | None:
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return max(0, sum(1 for _ in csv.reader(f)) - 1)
    except OSError:
        return None


def list_demos() -> list[dict]:
    """고를 수 있는 시연 묶음: 이름·데이터셋 ID·인원·사업 수·시나리오·가상 여부."""
    root = _demo_dir()
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()):
        try:
            m = json.loads((d / "manifest.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"name": d.name, "dataset_id": str(m.get("dataset_id", d.name)),
                    "people": _count_rows(d / "people.csv"), "projects": _count_rows(d / "projects.csv"),
                    "scenario": m.get("scenario") if isinstance(m.get("scenario"), str) else "planning",
                    "synthetic": m.get("synthetic") is True})
    return out


def demo_root(name: str) -> Path | None:
    """허용 목록에 있는 이름이면 그 폴더, 아니면 None(경로 탈출·없는 이름)."""
    return _demo_dir() / name if name in {d["name"] for d in list_demos()} else None


def load_choice(data_dir: Path) -> str | None:
    try:
        name = json.loads((data_dir / CHOICE_FILE).read_text("utf-8")).get("name")
    except (OSError, ValueError, AttributeError):
        return None
    return name if isinstance(name, str) else None


def save_choice(data_dir: Path, name: str) -> None:
    from api.storage import atomic_write
    atomic_write(data_dir / CHOICE_FILE, json.dumps({"name": name}, ensure_ascii=False).encode("utf-8"))


def clear_choice(data_dir: Path) -> None:
    try:
        (data_dir / CHOICE_FILE).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("시연 데이터 선택 기록을 지우지 못했다: %s", exc)
