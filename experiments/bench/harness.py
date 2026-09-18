"""실험 공통 측정 유틸. 모든 러너가 동일한 방법론(반복/웜업/중앙값·p95)을 쓴다."""
import json
import os
import platform
import shutil
import statistics as st
import subprocess
import sys
import tempfile
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


# 이번 프로세스가 이미 쓴 결과 경로. exp3처럼 스케일마다 체크포인트를 같은 이름으로
# 덮어쓰는 러너는 "자기 자신"을 덮어쓰는 것이므로 보존 대상이 아니다 — 여기 없으면
# 체크포인트 한 번에 백업 파일이 하나씩 쌓여 정작 지켜야 할 이전 실행의 원자료가
# 노이즈에 묻힌다.
_WRITTEN_THIS_RUN: set[Path] = set()


def preserve_existing(path: Path) -> Path | None:
    """덮어쓰기 전에 **이전 실행**의 결과 파일을 복사해 보존한다.

    태스크 4에서 exp4 스윕을 재실행했다가 save_result가 고정 경로에 그대로 쓰는
    바람에 첫 스윕의 원자료를 통째로, **복구 불가능하게** 잃었다. 그 뒤 exp5만
    자기 main()에 로컬 가드를 달고 있었는데, 다음 실험이 같은 손실을 반복하지
    못하도록 가드를 러너 하나가 아니라 이 공용 함수로 올린다.

    **거부가 아니라 보존이다.** 러너를 못 돌게 만드는 가드는 --force로 우회되거나
    러너를 안 고치게 만든다. 잃으면 안 되는 것은 실행 가능성이 아니라 이전 실행의
    원자료이므로, 새 결과는 그대로 쓰되 이전 파일을 `<name>.superseded-N.json`으로
    복사해 남긴다(N은 비어 있는 가장 작은 번호 — 기존 백업도 덮어쓰지 않는다).
    """
    if path in _WRITTEN_THIS_RUN or not path.exists():
        return None
    n = 1
    while True:
        prev = path.with_name(f"{path.stem}.superseded-{n}{path.suffix}")
        if not prev.exists():
            shutil.copy2(path, prev)
            with prev.open("rb") as preserved:
                os.fsync(preserved.fileno())
            return prev
        n += 1


def save_result(name: str, payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    doc = {"environment": environment(),
           "generated_at_note": "타임스탬프는 커밋 시각으로 갈음한다(재현성 유지)",
           "data": payload}
    serialized = json.dumps(doc, ensure_ascii=False, indent=1)
    temporary_path: Path | None = None
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=RESULTS_DIR,
        prefix=f".{name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(serialized)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    # 면제가 발동한 경우를 조용히 넘기지 않는다. 가드가 일부러 건너뛰는 유일한
    # 경우이므로, REPL에서 스윕 셀을 두 번 돌리거나 한 프로세스에서 main()을 두 번
    # 부르는 작성자는 이 줄을 보고 자기가 무엇을 덮어썼는지 알아야 한다.
    same_run = path in _WRITTEN_THIS_RUN
    preserved = preserve_existing(path)
    if preserved is not None:
        print(f"보존: 이전 실행의 {path.name} → {preserved.name} "
              "(원자료를 덮어쓰지 않는다 — 커밋 전에 어느 쪽이 증거인지 확인할 것)")
    elif same_run:
        print(f"덮어씀: {path.name} 은(는) 이번 실행이 이미 쓴 파일이라 보존하지 않는다 "
              "(체크포인트는 정상. 스윕을 한 프로세스에서 두 번 돌렸다면 "
              "앞선 결과는 복구할 수 없다)")
    try:
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    _WRITTEN_THIS_RUN.add(path)
    return path


def load_result(name: str) -> dict:
    return json.loads((RESULTS_DIR / f"{name}.json").read_text("utf-8"))
