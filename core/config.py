import json, os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures"

def load_review_items() -> list[str]:
    return json.loads((FIXTURES_DIR / "review_items.json").read_text("utf-8"))

def load_pricing() -> dict:
    return json.loads((FIXTURES_DIR / "pricing.json").read_text("utf-8"))

def load_env() -> None:
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
