"""시연 묶음 목록·전환(2026-10-06, claude-a -- 사용자 지시 "시연 기본 데이터로 둘 다", 화면은 claude-b).

시연은 두 장면을 보여 준다: 연초 계획(전원을 처음부터 배치, demo/org-n100)과 운영 중(90%가 이미 진행 사업에 있고
막 풀린 인력으로 신규 제안을 편성, demo/org-n100-operating). 서버의 활성 데이터셋은 하나라(캐시·교체 기록·PDF 서명이
데이터셋 버전에 묶여 있다) 두 묶음을 동시에 켜지 않고 목록에서 골라 바꿔 끼운다.

- 시연 묶음 = TEAMWEAVER_DEMO_DIR(없으면 TEAMWEAVER_DEMO_BUNDLE의 부모 폴더) 안의 CSV 묶음 폴더·zip(manifest.json이
  있는 것). 이름에 "-broken"이 든 것은 업로드 검증 시연용(일부러 깨진 파일)이라, 점으로 시작하는 것은 작업 중 폴더라 뺀다.
- 기본 묶음 = TEAMWEAVER_DEMO_BUNDLE(부팅·되돌리기).
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from api.datasets import ActiveDataset, build_active, bundle_version, extract_bundle_zip
from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle

DEMO_DIR_ENV = "TEAMWEAVER_DEMO_DIR"
DEMO_BUNDLE_ENV = "TEAMWEAVER_DEMO_BUNDLE"


@dataclass(frozen=True)
class Preset:
    id: str                 # 폴더 이름 또는 zip 파일 이름(확장자 뺌)
    path: Path
    manifest: dict

    @property
    def is_zip(self) -> bool:
        return self.path.suffix.lower() == ".zip"

    def read_text(self, name: str) -> str | None:
        try:
            if self.is_zip:
                with zipfile.ZipFile(self.path) as zf:
                    return zf.read(name).decode("utf-8-sig")
            return (self.path / name).read_text("utf-8-sig")
        except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile):
            return None


def demo_dir() -> Path | None:
    raw = os.environ.get(DEMO_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    bundle = os.environ.get(DEMO_BUNDLE_ENV, "").strip()
    return Path(bundle).expanduser().parent if bundle else None


def _manifest(path: Path) -> dict | None:
    try:
        if path.is_dir():
            return json.loads((path / "manifest.json").read_text("utf-8"))
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                return json.loads(zf.read("manifest.json").decode("utf-8"))
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return None
    return None


def list_presets() -> list[Preset]:
    root = demo_dir()
    if root is None or not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        name = p.stem if p.suffix.lower() == ".zip" else p.name
        if name.startswith(".") or "-broken" in name or not (p.is_dir() or p.suffix.lower() == ".zip"):
            continue
        m = _manifest(p)
        if isinstance(m, dict) and m.get("dataset_id"):
            out.append(Preset(id=name, path=p, manifest=m))
    return sorted(out, key=lambda pr: (_rows(pr, "people.csv"), pr.manifest.get("scenario") == "operating", pr.id))


def find_preset(preset_id: str) -> Preset | None:
    return next((p for p in list_presets() if p.id == preset_id), None)


def _rows(preset: Preset, name: str, unique: str | None = None) -> int:
    text = preset.read_text(name)
    if text is None:
        return 0
    rows = list(csv.DictReader(io.StringIO(text)))
    return len({r.get(unique) for r in rows}) if unique else len(rows)


def describe(preset: Preset) -> dict:
    m = preset.manifest
    scenario = "operating" if m.get("scenario") == "operating" else "planning"
    people, projects = _rows(preset, "people.csv"), _rows(preset, "projects.csv")
    current = _rows(preset, "current_assignments.csv", unique="person_id")
    bench, proposals = len(m.get("bench") or []), len(m.get("proposals") or [])
    if scenario == "operating":
        title = f"운영 중 · {current}명 배치 중, 대기 {bench}명"
        text = (f"진행 사업 {projects - proposals}개에 {current}명이 일하는 중이다. 막 일이 끝난 {bench}명으로 "
                f"신규 제안 {proposals}개를 편성한다(필요하면 진행 사업에서 몇 명을 옮긴다).")
    else:
        title = f"연초 계획 · {people}명 전체 배치"
        text = f"사업 {projects}개를 처음부터 편성한다(지금 배치된 사람 {current}명)."
    return {"id": preset.id, "dataset_id": m.get("dataset_id"), "scenario": scenario, "title": title,
            "description": text, "people": people, "projects": projects, "current": current,
            "bench": bench, "proposals": proposals, "synthetic": m.get("synthetic") is True}


def build_demo_active(path: Path) -> ActiveDataset:
    """묶음 폴더 또는 zip → 활성 데이터셋(source="demo-bundle"). 검증에 실패하면 ValueError(리포트 요약)."""
    path = Path(path).expanduser()
    with tempfile.TemporaryDirectory(prefix="teamweaver-demo-") as tmp:
        root = extract_bundle_zip(path.read_bytes(), Path(tmp) / "bundle") if path.suffix.lower() == ".zip" else path
        bundle, report = load_bundle(root)
        ds, parsed = to_dataset(bundle, report)          # ValueError with the report if it does not validate
        return build_active(ds, parsed, dataset_id=str(bundle.manifest.get("dataset_id", path.name)),
                            version=bundle_version(root), source="demo-bundle",
                            synthetic=bundle.manifest.get("synthetic") is True)


def _same(a: Path | None, b: Path | None) -> bool:
    try:
        return a is not None and b is not None and Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except OSError:
        return False


def default_bundle_path() -> Path | None:
    raw = os.environ.get(DEMO_BUNDLE_ENV, "").strip()
    return Path(raw).expanduser() if raw else None


def default_preset_id(presets: list[Preset] | None = None) -> str | None:
    target = default_bundle_path()
    return next((p.id for p in (presets if presets is not None else list_presets()) if _same(p.path, target)), None)


def active_preset_id(app, presets: list[Preset] | None = None) -> str | None:
    """지금 켜진 데이터가 시연 묶음이면 그 id. 업로드·예전 fixture면 None."""
    if app.state.dataset.info.source != "demo-bundle":
        return None
    path = getattr(app.state, "demo_active_path", None) or default_bundle_path()
    return next((p.id for p in (presets if presets is not None else list_presets()) if _same(p.path, path)), None)
