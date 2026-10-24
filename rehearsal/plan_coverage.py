"""1차 설계서 지표 둘을 미리 계산한 안 A에서 잰다(2026-10-24, 보고서 1.2·3.1·8.2).

- 요구 기술 충족률: 사업 요구(기술·최소 레벨·인원) 중 안 A 팀이 레벨 이상 인원을 채운 비율. 판정은 검토 자료·화면 충족 표시와
  같은 기준(`core.evaluate.skill_coverage` -- 경력 개월을 레벨로 바꾼 값, 하위 기술 인정분 포함)이다.
  화면 카드의 "요구 기술 충족률"(`core.optimize.metrics.matching_fulfillment` -- 한 사람을 여러 요구에 나눠 세는 보수적 값)도 함께 적는다.
- AI에 보내는 글자 비율: 300명 묶음 CSV 전체 글자 수 대비 평가 글(positive_text·negative_text) 글자 수 -- 설계서의
  "이력서까지 모두 LLM 처리 대비 절감"은 그 AS-IS를 만들지 않아 비용으로 재지 못하고, 글자 비율로만 어림한다(추정).

실행: `uv run python -m rehearsal.plan_coverage` → `rehearsal/results/plan-coverage.json`
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
OUT = ROOT / "rehearsal" / "results" / "plan-coverage.json"
BUNDLES = {"org-n100": "org-n100", "org-n200": "org-n200.zip", "org-n300": "org-n300.zip", "org-n100-annual": "org-n100-annual"}
REVIEW_TEXT_COLUMNS = ("positive_text", "negative_text")


def _graph(root: Path):
    from api.datasets import build_active, bundle_version, extract_bundle_zip
    from core.ingest.convert import to_dataset
    from core.ingest.loader import load_bundle

    if root.suffix == ".zip":
        root = extract_bundle_zip(root.read_bytes(), Path(tempfile.mkdtemp(prefix="tw-plan-cov-")) / "bundle")
    bundle, report = load_bundle(root)
    ds, parsed = to_dataset(bundle, report)
    kw = dict(dataset_id=str(bundle.manifest["dataset_id"]), version=bundle_version(root), source="demo-bundle",
              synthetic=True, manifest=bundle.manifest, bundle=bundle)
    return build_active(ds, parsed, judge=False, **kw).graph


def coverage(name: str) -> dict:
    from core.evaluate.skill_coverage import skill_coverage
    from core.optimize.metrics import matching_fulfillment
    from core.optimize.types import AssignEntry, PlanAssignment

    graph = _graph(DEMO / BUNDLES[name])
    pre = json.loads((DEMO / "precomputed" / f"{name}.json").read_text("utf-8"))
    plan_a = next(p for p in pre["optimize"]["plans"] if p["label"] == "A")
    entries = [AssignEntry(**e) for e in plan_a["entries"]]
    rows = [r for j in graph.projects for r in skill_coverage(graph, j.id, entries)]
    met = sum(1 for r in rows if r["met"] >= r["headcount"])
    plan = PlanAssignment(entries=entries, objective=plan_a["objective"], unfilled=plan_a["unfilled"], violations=[])
    return {
        "dataset_version": pre["dataset_version"],
        "plan_objective": plan_a["objective"],
        "requirements": len(rows),
        "met": met,
        "coverage": round(met / len(rows), 4),
        "fulfillment_ui": round(matching_fulfillment(graph, plan, {}), 4),
    }


def ai_text_share(zip_path: Path) -> dict:
    z = zipfile.ZipFile(zip_path)
    total = review = 0
    files = 0
    for n in z.namelist():
        if not n.endswith(".csv"):
            continue
        text = z.read(n).decode("utf-8")
        total += len(text)
        files += 1
        if os.path.basename(n) == "reviews.csv":
            for row in csv.DictReader(io.StringIO(text)):
                review += sum(len(row.get(c) or "") for c in REVIEW_TEXT_COLUMNS)
    return {"bundle": zip_path.name, "csv_files": files, "total_chars": total, "review_text_chars": review,
            "share": round(review / total, 4)}


def main() -> None:
    os.environ.setdefault("TEAMWEAVER_DATA_DIR", tempfile.mkdtemp(prefix="tw-plan-cov-data-"))
    os.environ.setdefault("TEAMWEAVER_LOGIN", "off")
    os.environ.setdefault("TEAMWEAVER_NIGHTLY", "off")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {
        "experiment": "plan-coverage",
        "when": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "commit": commit,
        "definition": {
            "coverage": "사업 요구(기술·최소 레벨·인원) 중 안 A 팀이 레벨 이상 인원을 채운 비율(core.evaluate.skill_coverage)",
            "fulfillment_ui": "화면 카드의 요구 기술 충족률(core.optimize.metrics.matching_fulfillment, 한 사람을 여러 요구에 나눠 셈)",
            "ai_text_share": "묶음 CSV 전체 글자 수 대비 평가 글(positive_text·negative_text) 글자 수 -- AI에 보내는 글자의 어림(추정)",
        },
        "bundles": {name: coverage(name) for name in BUNDLES},
        "ai_text_share": ai_text_share(DEMO / "org-n300.zip"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    for name, c in out["bundles"].items():
        print(f"{name}: 요구 {c['requirements']}개 중 충족 {c['met']} ({c['coverage']:.1%}) · 화면 충족률 {c['fulfillment_ui']:.1%}")
    s = out["ai_text_share"]
    print(f"AI 글자 비율 {s['share']:.1%} ({s['review_text_chars']:,} / {s['total_chars']:,}, CSV {s['csv_files']}개)")
    print("saved", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
