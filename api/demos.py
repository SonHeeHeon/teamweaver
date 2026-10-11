"""시연 데이터 묶음 고르기(2026-10-06, claude-a 요청 "데이터 탭에서 demo/org-n100-operating 을 고를 수 있게").

`demo/` 아래 manifest.json이 있는 폴더와 zip만 고를 수 있다(허용 목록 -- 경로를 요청으로 받지 않는다). 고른 이름은 데이터 폴더의
`demo_choice.json`에 기억해 재기동해도 그 묶음으로 뜬다. 고르지 않았으면 예전처럼 TEAMWEAVER_DEMO_BUNDLE(없으면 고정 fixture).

2026-10-07 claude-a 추가(사용자 결정 "claude-b 기준으로 합치기" -- claude-a의 같은 기능 구현을 이 모듈로 합침):
- zip 묶음(200·300명은 zip으로 둔다, rehearsal/make_demo). 이름에 "-broken"이 든 것은 업로드 검증 시연용이라 뺀다.
- 목록 칸 추가(기존 칸 그대로): title·description(장면 한 줄 설명), current·bench·proposals(운영 중 장면 규모).
- build_demo(root): 폴더·zip → 활성 데이터셋. 서버(api/main.build_demo_dataset)와 미리 계산(rehearsal/precompute_demo)이 같이 쓴다.
- active_demo_root(app): 지금 켜진 시연 묶음의 위치(미리 계산 결과 찾기, api/demo_precomputed).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
import zipfile
from pathlib import Path

from core.config import REPO_ROOT

log = logging.getLogger(__name__)

DEMO_DIR = REPO_ROOT / "demo"
CHOICE_FILE = "demo_choice.json"


def _demo_dir() -> Path:
    return Path(os.environ.get("TEAMWEAVER_DEMO_DIR", "") or DEMO_DIR)


def _read(bundle: Path, name: str) -> str | None:
    """묶음(폴더 또는 zip) 안 파일 하나를 글로 읽는다. 없거나 못 읽으면 None."""
    try:
        if bundle.suffix.lower() == ".zip":
            with zipfile.ZipFile(bundle) as zf:
                return zf.read(name).decode("utf-8-sig")
        return (bundle / name).read_text("utf-8-sig")
    except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile):
        return None


def _rows(bundle: Path, name: str, unique: str | None = None) -> int | None:
    text = _read(bundle, name)
    if text is None:
        return None
    rows = list(csv.DictReader(io.StringIO(text)))
    return len({r.get(unique) for r in rows}) if unique else len(rows)


def _name(p: Path) -> str:
    return p.stem if p.suffix.lower() == ".zip" else p.name


def _candidates(root: Path) -> list[Path]:
    """폴더와 zip. 같은 이름이 둘 다 있으면 폴더만(목록에 같은 이름이 두 번 나오지 않게)."""
    out: dict[str, Path] = {}
    for p in sorted(root.iterdir(), key=lambda p: (p.suffix.lower() == ".zip", p.name)):
        name = _name(p)
        if name.startswith(".") or "-broken" in name or name in out:
            continue
        if (p.is_dir() and (p / "manifest.json").is_file()) or (p.is_file() and p.suffix.lower() == ".zip"):
            out[name] = p
    return [out[k] for k in sorted(out)]


def _describe(scenario: str, people, projects, current, bench: int, proposals: int) -> tuple[str, str]:
    if scenario == "operating":
        return (f"운영 중 · {current}명 배치 중, 대기 {bench}명",
                f"진행 사업 {(projects or 0) - proposals}개에 {current}명이 일하는 중이다. 막 일이 끝난 {bench}명으로 "
                f"신규 제안 {proposals}개를 편성한다(필요하면 진행 사업에서 몇 명을 옮긴다).")
    return (f"연초 계획 · {people}명 전체 배치", f"사업 {projects}개를 처음부터 편성한다(지금 배치된 사람 {current}명).")


def list_demos() -> list[dict]:
    """고를 수 있는 시연 묶음: 이름·데이터셋 ID·인원·사업 수·시나리오·가상 여부 + 제목·설명·현재 배치·대기·신규 제안.
    순서: 인원 → 연초 계획 → 운영 중."""
    root = _demo_dir()
    out = []
    if not root.is_dir():
        return out
    for d in _candidates(root):
        try:
            m = json.loads(_read(d, "manifest.json") or "")
        except ValueError:
            continue
        if not isinstance(m, dict) or not m.get("dataset_id"):
            continue
        scenario = m.get("scenario") if isinstance(m.get("scenario"), str) else "planning"
        people, projects = _rows(d, "people.csv"), _rows(d, "projects.csv")
        current = _rows(d, "current_assignments.csv", unique="person_id") or 0
        bench, proposals = len(m.get("bench") or []), len(m.get("proposals") or [])
        title, description = _describe(scenario, people, projects, current, bench, proposals)
        out.append({"name": _name(d), "dataset_id": str(m.get("dataset_id", _name(d))),
                    "people": people, "projects": projects, "scenario": scenario,
                    "synthetic": m.get("synthetic") is True,
                    "title": title, "description": description, "current": current, "bench": bench,
                    "proposals": proposals})
    return sorted(out, key=lambda d: (d["people"] or 0, d["scenario"] == "operating", d["name"]))


JUDGMENTS_FILE = "review_judgments.json"


def demo_judgments_path() -> Path:
    """가상 시연 묶음의 리뷰 글 LLM 판정 동봉 파일(scripts/export_demo_judgments.py가 만든다, 2026-10-11).
    다른 기기에서도 미리 계산 때와 같은 판정값을 쓰게 한다 -- api.review_judge.judge_reviews(seed_path=)."""
    return _demo_dir() / JUDGMENTS_FILE


def demo_root(name: str) -> Path | None:
    """허용 목록에 있는 이름이면 그 폴더(또는 zip), 아니면 None(경로 탈출·없는 이름)."""
    root = _demo_dir()
    if not root.is_dir() or name not in {d["name"] for d in list_demos()}:
        return None
    return next((p for p in _candidates(root) if _name(p) == name), None)


def build_demo(root: Path):
    """시연 묶음(폴더·zip) → 활성 데이터셋(source="demo-bundle"). 검증에 실패하면 ValueError(대체하지 않는다)."""
    from api.datasets import build_active, bundle_version, extract_bundle_zip
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle
    root = Path(root).expanduser()
    if root.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="teamweaver-demo-") as tmp:
            return build_demo(extract_bundle_zip(root.read_bytes(), Path(tmp) / "bundle"))
    bundle, report = load_bundle(root)
    ds, parsed = to_dataset(bundle, report)          # ValueError with the report if it does not validate
    return build_active(ds, parsed, dataset_id=str(bundle.manifest.get("dataset_id", root.name)),
                        version=bundle_version(root), source="demo-bundle",
                        synthetic=bundle.manifest.get("synthetic") is True, manifest=bundle.manifest, bundle=bundle)


def current_demo_root() -> Path | None:
    """시연 묶음으로 뜰 때 쓰는 위치: 고른 묶음, 없으면 TEAMWEAVER_DEMO_BUNDLE(api/main.build_fixture_dataset과 같은 순서)."""
    from api.storage import data_dir
    chosen = load_choice(data_dir())
    root = demo_root(chosen) if chosen else None
    if root is not None:
        return root
    raw = os.environ.get("TEAMWEAVER_DEMO_BUNDLE", "").strip()
    return Path(raw).expanduser() if raw else None


def active_demo_root(app) -> Path | None:
    """지금 켜진 데이터가 시연 묶음이면 그 위치. 업로드·고정 fixture면 None.

    켤 때 기록한 위치(app.state.demo_active_root)를 쓴다 -- 요청마다 선택 파일을 다시 읽으면 고르기가 선택을 저장한 뒤
    바꿔 끼우기 전 사이에 옛 데이터를 새 이름으로 보일 수 있다(리뷰 SHOULD)."""
    if app.state.dataset.info.source != "demo-bundle":
        return None
    return getattr(app.state, "demo_active_root", None)


def active_demo_name(app) -> str | None:
    root = active_demo_root(app)
    return _name(root) if root is not None else None


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
