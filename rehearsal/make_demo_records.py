"""시연 묶음 6개 → 편성 기록 파일(demo/records/*.json)(2026-10-18, claude-a).

사용자 지시: "시연 데이터 고르기"는 없애고, 이미 만든 시연 데이터를 편성 기록(JSON)으로 불러와 볼 수 있게. 기록은 서비스의
내보내기(api/records.export_record)와 같은 형식이다. 원천은 `demo/<묶음>`, 편성안·운영 중 K는 `demo/precomputed/<묶음>.json`
(미리 계산 때 서버 평가기로 확인한 값), 평가 글 판정값은 시연 묶음을 켤 때와 같은 동봉 값(`demo/review_judgments.json`).
만든 뒤 불러오기 검증(build_and_verify)을 그대로 돌려 통과해야 저장한다 -- 하나라도 실패하면 아무것도 쓰지 않는다.

    TEAMWEAVER_DATA_DIR=<임시 폴더> uv run python -m rehearsal.make_demo_records
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT = DEMO / "records"
BUNDLES = ("org-n100", "org-n100-operating", "org-n200", "org-n200-operating", "org-n300", "org-n300-operating")


def _files(root: Path) -> dict[str, str]:
    from api.datasets import _ALLOWED, extract_bundle_zip
    if root.suffix == ".zip":
        tmp = tempfile.mkdtemp(prefix="tw-demo-record-")
        root = extract_bundle_zip(root.read_bytes(), Path(tmp) / "bundle")
    return {p.name: p.read_text("utf-8") for p in sorted(root.iterdir())
            if p.is_file() and (p.name in _ALLOWED or p.name == "manifest.json")}


def _title(manifest: dict, people: int) -> str:
    y, m = str(manifest.get("horizon_start", "")).split("-") if manifest.get("horizon_start") else ("", "")
    when = f"{y}년 {int(m)}월 " if y else ""
    kind = "운영 중 편성" if manifest.get("scenario") == "operating" else "연초 편성"
    return f"{when}{kind} · {people}명"


def make(name: str) -> tuple[str, dict]:
    from api.demos import build_demo
    from api.records import FORMAT, VERSION, request_fields
    from core.optimize.milp import MilpParams
    root = DEMO / name if (DEMO / name).is_dir() else DEMO / f"{name}.zip"
    active = build_demo(root)
    pre = json.loads((DEMO / "precomputed" / f"{name}.json").read_text("utf-8"))
    if pre["dataset_version"] != active.info.version:
        raise SystemExit(f"{name}: 미리 계산의 데이터 버전이 지금과 다르다 -- rehearsal.precompute_demo부터")
    files = _files(root)
    manifest = json.loads(files["manifest.json"])
    parsed = active.parsed_reviews
    opt = pre["optimize"]
    op = pre.get("operating")
    rec = {
        "format": FORMAT, "version": VERSION, "title": _title(manifest, active.info.people),
        "kind": manifest.get("scenario") or "planning", "exported_at": pre["computed_at"],
        "dataset": {"dataset_id": active.info.dataset_id, "content_version": active.info.content_version,
                    "synthetic": active.info.synthetic, "people": active.info.people, "projects": active.info.projects,
                    "horizon_start": manifest.get("horizon_start"), "horizon_months": manifest.get("horizon_months"),
                    "files": files},
        "review_judgments": {"model": active.info.judge_model, "polarity": [float(p.text_polarity) for p in parsed]},
        "request": {"weights": opt["weights"], "milp_params": request_fields(MilpParams(**opt["milp_params"])),
                    "n_alternatives": opt["n_alternatives"]},
        "optimize": {"plans": opt["plans"], "stop_reason": opt.get("stop_reason"), "computed_at": pre["computed_at"]},
        "operating": ({"milp_params": op["milp_params"], "ks": op["ks"], "rows": op["rows"],
                       "computed_at": pre["computed_at"]} if op else None),
    }
    active.retire()
    return rec["title"], rec


def main() -> None:
    from api.records import build_and_verify, parse_record
    made = []
    for name in BUNDLES:
        title, rec = make(name)
        raw = json.dumps(rec, ensure_ascii=False).encode("utf-8")
        active, pre, check = build_and_verify(parse_record(raw))
        active.retire()
        if check["score_changed"] or check["skipped"] or (rec["operating"] and not check["operating"]):
            raise SystemExit(f"{name}: 불러오기 검증이 기록과 다르다 -- {check}")
        made.append((name, title, raw, check))
        print(f"{name}: {title} · 안 {check['plans']}개 · 운영 중 {check['operating']} · {len(raw) / 1e6:.1f}MB", flush=True)
    OUT.mkdir(exist_ok=True)
    for name, _title_, raw, _ in made:
        (OUT / f"{name}.json").write_bytes(raw)
    print("저장:", OUT)


if __name__ == "__main__":
    main()
