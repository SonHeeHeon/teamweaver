"""실험 공통 측정 유틸. 모든 러너가 동일한 방법론(반복/웜업/중앙값·p95)을 쓴다."""
import json
import os
import platform
import statistics as st
import subprocess
import sys
import time
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

from core.config import REPO_ROOT

RESULTS_DIR = REPO_ROOT / "experiments" / "results"
_TRACKED = ("numpy", "scipy", "pulp", "neo4j", "tiktoken", "openai", "pandas")


def _cbc_version() -> str:
    """Defensively get CBC binary version. Returns string suitable for environment record."""
    try:
        import pulp
        solver = pulp.PULP_CBC_CMD(msg=0)
        path = getattr(solver, "path", None)
        if not path:
            return "unknown (no solver path)"
        out = subprocess.run([path, "-quit"], capture_output=True, text=True, timeout=10)
        for line in (out.stdout or "").splitlines():
            if "Version" in line:
                return line.strip()
        return f"unknown (path={path})"
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


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


def _cpu_model() -> str:
    """Defensively get a human-readable CPU model string. Best-effort only --
    never raises, never blocks a run (final review Important 9: the report's
    headline is "Neo4j is 5x slower", which is exactly the kind of claim that
    needs the hardware it was measured on disclosed)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True, timeout=5)
            name = out.stdout.strip()
            if name:
                return name
        elif sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        return platform.processor() or "unknown"
    except Exception as exc:                       # noqa: BLE001 -- defensive probe
        return f"unavailable ({type(exc).__name__})"


def _ram_gb() -> str:
    """Defensively get total RAM in GiB via POSIX sysconf. Best-effort only."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return f"{pages * page_size / (1024 ** 3):.1f} GiB"
    except (ValueError, OSError, AttributeError) as exc:
        return f"unavailable ({type(exc).__name__})"


def _neo4j_server_version() -> str:
    """Defensively query the running Neo4j server's own version (read-only) --
    distinct from the `neo4j` Python driver version already tracked in
    `packages`. Never lets a probe failure (server down, auth, etc.) break a
    benchmark run; degrades to an "unavailable" string instead."""
    try:
        from core.graph.neo4j_store import get_driver
        driver = get_driver()
        try:
            driver.verify_connectivity()
            with driver.session() as s:
                rec = s.run("CALL dbms.components() YIELD name, versions, edition "
                            "RETURN name, versions, edition").single()
                if rec is None:
                    return "unknown (empty dbms.components() result)"
                return f"{rec['name']} {rec['versions'][0]} ({rec['edition']})"
        finally:
            driver.close()
    except Exception as exc:                        # noqa: BLE001 -- defensive probe
        return f"unavailable ({type(exc).__name__})"


def environment() -> dict:
    pkgs = {}
    for name in _TRACKED:
        try:
            pkgs[name] = version(name)
        except PackageNotFoundError:
            pkgs[name] = "not installed"
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "cpu_model": _cpu_model(), "ram": _ram_gb(),
            "cbc": _cbc_version(), "neo4j_server": _neo4j_server_version(),
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
