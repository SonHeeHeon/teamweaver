"""시험용: 연말형 시연 묶음 원문(파일 이름 → 글)."""
from pathlib import Path

YEAREND = Path(__file__).resolve().parents[2] / "demo" / "org-n100-yearend"


def YEAREND_FILES() -> dict[str, str]:
    return {p.name: p.read_text("utf-8") for p in sorted(YEAREND.iterdir()) if p.is_file()}
