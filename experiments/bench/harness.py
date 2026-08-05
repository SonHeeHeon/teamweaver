"""실험 공통 측정 유틸. 모든 러너가 동일한 방법론(반복/웜업/중앙값·p95)을 쓴다."""
import json
import os
import platform
import statistics as st
import sys
import time
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

from core.config import REPO_ROOT

RESULTS_DIR = REPO_ROOT / "experiments" / "results"
_TRACKED = ("numpy", "scipy", "pulp", "neo4j", "tiktoken", "openai", "pandas")


def measure(fn, repeats: int = 20, warmup: int = 3) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    idx95 = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
    return {"median_ms": st.median(samples), "p95_ms": samples[idx95],
            "min_ms": samples[0], "max_ms": samples[-1],
            "repeats": repeats, "warmup": warmup}


def environment() -> dict:
    pkgs = {}
    for name in _TRACKED:
        try:
            pkgs[name] = version(name)
        except PackageNotFoundError:
            pkgs[name] = "not installed"
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "packages": pkgs}


def save_result(name: str, payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    doc = {"environment": environment(),
           "generated_at_note": "타임스탬프는 커밋 시각으로 갈음한다(재현성 유지)",
           "data": payload}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")
    return path


def load_result(name: str) -> dict:
    return json.loads((RESULTS_DIR / f"{name}.json").read_text("utf-8"))
