"""Write the screenshots embedded in the schema intake JSON to image files.

The intake form (docs/data-schema/schema-intake.html) stores uploaded screenshots as
data URLs inside private/schema-intake.json so a single file carries everything.
Agents need real image files to look at them, so this extracts them next to it:

    uv run python scripts/extract_intake_images.py [path/to/schema-intake.json] [out_dir]

Every image is validated (same rules as the form) before anything is written, and the
output directory is replaced as a whole, so it never mixes images from different runs.
File names come from the part/question/position only, never from user-supplied names.
"""
import base64
import binascii
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON = REPO_ROOT / "private" / "schema-intake.json"
MANIFEST = "captions.json"
MARKER = ".teamweaver-intake-images"          # proves a directory was created by this script
MAX_IMAGE_BYTES = 15 * 1024 * 1024
FORMATS = {  # mime -> (extension, [(offset, signature bytes)])
    "image/png": ("png", [(0, b"\x89PNG")]),
    "image/jpeg": ("jpg", [(0, b"\xff\xd8\xff")]),
    "image/gif": ("gif", [(0, b"GIF")]),
    "image/webp": ("webp", [(0, b"RIFF"), (8, b"WEBP")]),
    "image/bmp": ("bmp", [(0, b"BM")]),
}
DATA_URL = re.compile(r"^data:(image/[a-z]+);base64,([A-Za-z0-9+/]*={0,2})$")
SAFE = re.compile(r"[^a-z0-9_-]")


def _decode(where: str, data_url: object) -> tuple[str, bytes]:
    m = DATA_URL.match(data_url) if isinstance(data_url, str) else None
    if not m or len(m.group(2)) % 4:
        raise ValueError(f"{where}: not a base64 image data URL")
    if m.group(1) not in FORMATS:
        raise ValueError(f"{where}: unsupported image type {m.group(1)}")
    try:
        payload = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{where}: invalid base64 image data")
    ext, signatures = FORMATS[m.group(1)]
    if len(payload) > MAX_IMAGE_BYTES:
        raise ValueError(f"{where}: image larger than 15MB")
    if not all(payload[off:off + len(sig)] == sig for off, sig in signatures):
        raise ValueError(f"{where}: content does not match {m.group(1)} (corrupted file)")
    _require_decodable(where, payload)
    return ext, payload


def _require_decodable(where: str, payload: bytes) -> None:
    """A valid header does not mean a usable image (truncated files keep their header)."""
    try:
        from PIL import Image          # Pillow comes with the dev group (matplotlib)
    except ImportError:
        raise RuntimeError("Pillow is required to verify images: run with `uv run` (dev group)")
    try:
        with Image.open(io.BytesIO(payload)) as img:
            img.load()
    except Exception as exc:                            # noqa: BLE001 -- any decoder failure means unusable
        raise ValueError(f"{where}: image cannot be decoded ({exc.__class__.__name__})") from exc


def _collect(data: dict) -> list[tuple[str, bytes, dict]]:
    """Validate every image and pick collision-free file names before writing anything."""
    planned, used = [], set()
    for part_id, part in (data.get("parts") or {}).items():
        for key, entry in ((part or {}).get("answers") or {}).items():
            answer = entry.get("answer") if isinstance(entry, dict) else None
            if not isinstance(answer, list):
                continue
            for i, item in enumerate(answer, start=1):
                if not isinstance(item, dict) or "data_url" not in item:
                    continue
                ext, payload = _decode(f"{part_id}.{key}[{i}]", item["data_url"])
                stem = f"{SAFE.sub('_', str(part_id).lower())}-{SAFE.sub('_', str(key).lower())}-{i:02d}"
                name, n = f"{stem}.{ext}", 2
                while name in used:
                    name, n = f"{stem}-{n}.{ext}", n + 1
                used.add(name)
                planned.append((name, payload, {"file": name, "caption": str(item.get("caption", "")),
                                                "original_name": str(item.get("name", ""))}))
    return planned


def extract(json_path: Path, out_dir: Path) -> list[dict]:
    out_dir = Path(out_dir)
    if Path(json_path).resolve().is_relative_to(out_dir.resolve()):
        raise ValueError(f"{json_path} is inside {out_dir}; replacing the output would delete the input")
    if out_dir.exists() and any(out_dir.iterdir()) and not (out_dir / MARKER).is_file():
        raise ValueError(f"{out_dir} was not created by this script; refusing to replace it")
    planned = _collect(json.loads(Path(json_path).read_text(encoding="utf-8")))

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}-", dir=out_dir.parent))
    try:
        for name, payload, _ in planned:
            (staging / name).write_bytes(payload)
        manifest = [meta for _, _, meta in planned]
        (staging / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / MARKER).write_text("schema intake images extracted by scripts/extract_intake_images.py\n")
        _install(staging, out_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _install(staging: Path, out_dir: Path) -> None:
    """Swap staging in for out_dir; on failure the previous out_dir is put back."""
    if not out_dir.exists():
        staging.rename(out_dir)
        return
    holder = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}-prev-", dir=out_dir.parent))
    previous = holder / "prev"
    out_dir.rename(previous)
    try:
        staging.rename(out_dir)
    except BaseException:
        previous.rename(out_dir)
        shutil.rmtree(holder, ignore_errors=True)
        raise
    shutil.rmtree(holder, ignore_errors=True)


def main() -> None:
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSON
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else json_path.parent / "schema-intake-images"
    written = extract(json_path, out_dir)
    for w in written:
        print(f"{out_dir / w['file']}\t{w['caption']}")
    print(f"{len(written)} image(s) written to {out_dir}")


if __name__ == "__main__":
    main()
