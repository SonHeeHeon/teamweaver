"""python -m core.ingest generate --people 50 --projects 10 --seed 1 --out DIR
python -m core.ingest check DIR        # validate + convert, print the report, exit 1 on errors"""
import argparse
import sys
from pathlib import Path

from core.ingest.convert import to_dataset
from core.ingest.loader import load_bundle
from core.ingest.synthetic import generate_bundle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write a synthetic CSV bundle")
    g.add_argument("--people", type=int, required=True)
    g.add_argument("--projects", type=int, required=True)
    g.add_argument("--seed", type=int, required=True)
    g.add_argument("--horizon-start", default="2026-10")
    g.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check", help="validate a bundle and convert it to the current Dataset")
    c.add_argument("bundle", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        try:
            out = generate_bundle(args.out, args.people, args.projects, args.seed, args.horizon_start)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {out}")
        return 0
    bundle, report = load_bundle(args.bundle)
    try:
        ds, _ = to_dataset(bundle, report)
    except ValueError:
        print(report.summary())
        return 1
    print(report.summary())
    print(f"OK: {len(ds.people)} people, {len(ds.projects)} projects, {len(ds.coworks)} cowork pairs, "
          f"{len(ds.reviews)} reviews")
    return 0


if __name__ == "__main__":
    sys.exit(main())
