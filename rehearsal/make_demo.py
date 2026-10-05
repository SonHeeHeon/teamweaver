"""Build the demo datasets in the real-system CSV shape (no LLM involved).

    uv run python -m rehearsal.make_demo

demo/org-n100/        data platform group, 100 people -- the server boots from it when
                      TEAMWEAVER_DEMO_BUNDLE=demo/org-n100 (scripts/run_poc.sh sets this)
demo/org-n200.zip     + AI group            -- upload on the 데이터 tab to show scale
demo/org-n300.zip     + business automation group
All values are synthetic (core/ingest/org_profile.py); the files carry the real system's columns.
"""
import io
import shutil
import zipfile
from pathlib import Path

from core.ingest.org_profile import generate_org_bundle
from rehearsal.run import SEED

DEMO = Path(__file__).resolve().parents[1] / "demo"


def main() -> None:
    DEMO.mkdir(exist_ok=True)
    target = DEMO / "org-n100"
    if target.exists():
        shutil.rmtree(target)
    generate_org_bundle(target, 100, seed=SEED)
    for n in (200, 300):
        tmp = DEMO / f".tmp-n{n}"
        if tmp.exists():
            shutil.rmtree(tmp)
        generate_org_bundle(tmp, n, seed=SEED)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(tmp.iterdir()):
                info = zipfile.ZipInfo(p.name, date_time=(2026, 1, 1, 0, 0, 0))   # same bytes on every rebuild
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, p.read_bytes())
        (DEMO / f"org-n{n}.zip").write_bytes(buf.getvalue())
        shutil.rmtree(tmp)
    print(f"demo data written to {DEMO}")


if __name__ == "__main__":
    main()
