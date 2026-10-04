import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts import extract_intake_images as mod
from scripts.extract_intake_images import extract


def _image(fmt: str, color=(0, 128, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format=fmt)
    return buf.getvalue()


PNG, JPEG = _image("PNG"), _image("JPEG")
MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp", "BMP": "image/bmp"}


def _url(raw: bytes, mime="image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _write(tmp_path, images, parts=None):
    src = tmp_path / "in.json"
    data = {"schema": "teamweaver-schema-intake", "version": 2, "parts": parts or {
        "skill": {"answers": {
            "screens": {"question": "입력 화면 캡처", "answer": images},
            "overview": {"question": "시스템 개요", "answer": "텍스트 답은 건너뛴다"}}}}}
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return src


def _shot(raw=PNG, mime="image/png", caption=""):
    return {"id": "x", "name": "a.png", "caption": caption, "data_url": _url(raw, mime)}


def test_writes_each_image_with_captions(tmp_path):
    src = _write(tmp_path, [_shot(caption="기술 등록 화면"), _shot(JPEG, "image/jpeg", "숙련도 드롭다운")])
    written = extract(src, tmp_path / "out")
    assert [w["file"] for w in written] == ["skill-screens-01.png", "skill-screens-02.jpg"]
    assert (tmp_path / "out" / "skill-screens-01.png").read_bytes() == PNG
    captions = json.loads((tmp_path / "out" / "captions.json").read_text(encoding="utf-8"))
    assert captions[1]["caption"] == "숙련도 드롭다운"


@pytest.mark.parametrize("fmt", list(MIMES))
def test_every_supported_format_round_trips(tmp_path, fmt):
    raw = _image(fmt)
    assert extract(_write(tmp_path, [_shot(raw, MIMES[fmt])]), tmp_path / "out")


def test_user_supplied_names_never_choose_the_path(tmp_path):
    src = _write(tmp_path, [{**_shot(), "name": "../../escape.png"}])
    assert extract(src, tmp_path / "out")[0]["file"] == "skill-screens-01.png"
    assert not (tmp_path / "escape.png").exists()


@pytest.mark.parametrize("data_url", [
    "data:image/png;base64,@@not-base64@@",
    "data:image/png,broken",
    _url(b"not a png at all"),                       # valid base64, wrong signature
    _url(PNG, "image/jpeg"),                         # declared type does not match content
    "data:image/svg+xml;base64,PHN2Zz4=",
])
def test_invalid_images_are_rejected(tmp_path, data_url):
    with pytest.raises(ValueError):
        extract(_write(tmp_path, [{**_shot(), "data_url": data_url}]), tmp_path / "out")


@pytest.mark.parametrize("fmt", list(MIMES))
def test_truncated_images_with_valid_headers_are_rejected(tmp_path, fmt):
    raw = _image(fmt)
    with pytest.raises(ValueError):
        extract(_write(tmp_path, [_shot(raw[: len(raw) // 2], MIMES[fmt])]), tmp_path / "out")


def test_rerun_with_fewer_images_leaves_no_stale_files(tmp_path):
    out = tmp_path / "out"
    extract(_write(tmp_path, [_shot(), _shot()]), out)
    extract(_write(tmp_path, [_shot()]), out)
    assert sorted(p.name for p in out.iterdir() if not p.name.startswith(".")) == [
        "captions.json", "skill-screens-01.png"]
    extract(_write(tmp_path, []), out)
    assert sorted(p.name for p in out.iterdir() if not p.name.startswith(".")) == ["captions.json"]
    assert json.loads((out / "captions.json").read_text()) == []


def test_failure_mid_way_keeps_the_previous_output_intact(tmp_path):
    out = tmp_path / "out"
    extract(_write(tmp_path, [_shot(caption="이전")]), out)
    with pytest.raises(ValueError):
        extract(_write(tmp_path, [_shot(caption="새것"), {**_shot(), "data_url": "data:image/png,broken"}]), out)
    assert json.loads((out / "captions.json").read_text(encoding="utf-8"))[0]["caption"] == "이전"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".out")] == []


def test_failed_swap_restores_the_previous_output(tmp_path, monkeypatch):
    out = tmp_path / "out"
    extract(_write(tmp_path, [_shot(caption="이전")]), out)
    real_rename = Path.rename

    def flaky_rename(self, target):
        if Path(target) == out and self.name.startswith(".out-") and "-prev-" not in str(self):
            raise OSError("simulated failure installing the new output")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    with pytest.raises(OSError):
        extract(_write(tmp_path, [_shot(caption="새것")]), out)
    monkeypatch.undo()
    assert json.loads((out / "captions.json").read_text(encoding="utf-8"))[0]["caption"] == "이전"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".out")] == []


def test_unrelated_sibling_with_a_predictable_name_is_untouched(tmp_path):
    sibling = tmp_path / ".out-old"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("x")
    out = tmp_path / "out"
    extract(_write(tmp_path, [_shot()]), out)
    extract(_write(tmp_path, [_shot()]), out)
    assert (sibling / "keep.txt").exists()


def test_name_collisions_after_normalization_get_distinct_files(tmp_path):
    parts = {"skill": {"answers": {
        "a.b": {"answer": [_shot(caption="첫째")]},
        "a_b": {"answer": [_shot(caption="둘째")]}}}}
    written = extract(_write(tmp_path, [], parts=parts), tmp_path / "out")
    assert [w["file"] for w in written] == ["skill-a_b-01.png", "skill-a_b-01-2.png"]
    assert len(list((tmp_path / "out").glob("*.png"))) == 2


@pytest.mark.parametrize("contents", [{"keep.txt": "x"}, {"captions.json": "[]"}])
def test_refuses_to_replace_a_directory_it_did_not_create(tmp_path, contents):
    other = tmp_path / "precious"
    other.mkdir()
    for name, text in contents.items():
        (other / name).write_text(text)
    with pytest.raises(ValueError):
        extract(_write(tmp_path, [_shot()]), other)
    assert all((other / name).exists() for name in contents)
    assert mod.MARKER not in {p.name for p in other.iterdir()}
