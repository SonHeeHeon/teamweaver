"""자료에서 사업 정보 채우기 · 부서장용 자료 요약 측정(2026-10-23, claude-b -- `api/rag/doc_brief`).

사전 등록: 정답과 관문을 AI를 돌리기 전에 이 파일에 고정한다(바꾸면 결과 파일의 commit·dirty로 드러난다).
- 자료: 시연 가상 자료 7편(`demo/project_docs`, 생성기 `scripts/make_demo_project_docs.py`). 정답은 그 자료를 만든 시연 묶음의 사업 표:
  사업명 = project_name, 기간 = 시작~끝 달 수(둘 다 넣음), 총 사업비 = 월 예산(점) × 개월 × 1만 원(생성기가 그렇게 적는다), 고객 구분 = sector.
- 관문(OpenAI, 서비스 모델·추론): G1 사업명 맞음 ≥ 6/7 · G2 기간 개월 맞음 ≥ 6/7 · G3 월 예산(총액 ÷ 개월 ÷ 1만 원)이 원래 월 예산과 같음 ≥ 6/7
  · G4 검사기 일관성 100%(살아남은 항목을 같은 검사기로 다시 잼 -- **구조상 0이 나오는 자기 검증이라 환각이 없다는 증거가 아니다**, 폴백 리뷰 S10)
  · G5 요약 "인력·역량 요구" 칸이 빈 자료 ≤ 1/7.
- 주의: 가상 자료는 표가 정돈되고 "(N개월)"·"월 M만 원"이 그대로 적혀 규칙만으로도 G1~G3를 넘는다 -- AI의 몫은 실제 공공 자료(--real-dir)에서 사람이 본다.
- 보고만: 고객 구분 맞음, 규칙(AI 없음) 같은 표, GLM-5.3-Flash high(사내 LLM 대리, 가상 자료만), 시간·비용·버린 칸 이유.
- `--real-dir`: 실제 공공 자료(저장소 밖) -- OpenAI만, 결과는 `--real-out`(기본 저장소 밖)으로. 정답 없음 -- 사람이 리포트에서 본다.

    uv run python scripts/eval_doc_brief.py [--rule-only] [--real-dir DIR --real-out FILE]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "demo" / "project_docs"
OUT = ROOT / "rehearsal" / "results" / "doc-brief.json"
GLM_MODEL, GLM_EFFORT, GLM_URL = "glm-5.3-flash", "high", "https://api.z.ai/api/paas/v4"
WON_PER_POINT = 10_000
GATES = {"G1_name": "사업명 맞음 >= 6/7", "G2_months": "기간 개월 맞음 >= 6/7", "G3_monthly": "월 예산(점) 맞음 >= 6/7",
         "G4_checker_consistency": "검사기 일관성 100%(자기 검증 -- 환각 없음의 증거 아님)", "G5_staffing": "인력·역량 요구 칸이 빈 자료 <= 1/7"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _months(a: str, b: str) -> int:
    (y0, m0), (y1, m1) = (map(int, x.split("-")) for x in (a, b))
    return (y1 - y0) * 12 + m1 - m0 + 1


def load_items() -> list[dict]:
    """시연 가상 자료 → 글자 + 정답."""
    from api.project_docs import extract_text
    bundles = {}
    for m in (ROOT / "demo").glob("*/manifest.json"):
        bundles[json.loads(m.read_text(encoding="utf-8"))["dataset_id"]] = m.parent
    items = []
    for d in json.loads((DOCS / "index.json").read_text(encoding="utf-8"))["docs"]:
        folder = next(bundles[k] for k in d["registered_at"] if k in bundles)
        row = next(r for r in csv.DictReader(open(folder / "projects.csv", encoding="utf-8")) if r["project_id"] == d["project_id"])
        months = _months(row["start_month"], row["end_month"])
        items.append({"id": d["id"], "kind": d["kind"], "text": extract_text("pdf", (DOCS / d["file"]).read_bytes()),
                      "gold": {"name": row["project_name"], "months": months, "monthly": int(row["monthly_budget"]),
                               "total": int(row["monthly_budget"]) * months * WON_PER_POINT, "sector": row["sector"]}})
    return items


def run(items, *, client=None, model=None, effort=None, price=None) -> list[dict]:
    from api.rag.doc_brief import read_project_meta, summarize_document
    out = []
    for it in items:
        m = read_project_meta(it["text"], client=client, model=model, reasoning_effort=effort, price=price)
        s = summarize_document(it["text"], it["kind"], client=client, model=model, reasoning_effort=effort, price=price)
        out.append({"id": it["id"], "meta": m, "sum": s})
        print(f"  {it['id']}: {m['method']}/{s['method']}", flush=True)
    return out


def score(items, rows) -> dict:
    from api.rag.doc_brief import numbers_supported
    per, lat, cost, dropped_meta, dropped_sum = [], [], 0.0, [], []
    ai_fields = {"name": 0, "duration_months": 0, "budget": 0, "sector": 0}      # 맞은 칸이 AI 값인지(검사를 못 넘으면 규칙 값으로 채워진다 -- AI 몫을 따로 센다)
    bad_items = total_items = empty_staffing = 0
    for it, r in zip(items, rows):
        g, f = it["gold"], r["meta"]["fields"]
        for k in ai_fields:
            ai_fields[k] += bool(f.get(k) and f[k].get("source") == "ai")
        months = (f.get("duration_months") or {}).get("value")
        total = (f.get("budget") or {}).get("value")
        monthly = round(total / months / WON_PER_POINT) if total and months else None
        name = (f.get("name") or {}).get("value") or ""
        row = {"id": it["id"], "name": _norm(g["name"]) == _norm(name) or (_norm(g["name"]) in _norm(name) and len(_norm(name)) <= len(_norm(g["name"])) + 12),
               "months": months == g["months"], "total": total == g["total"], "monthly": monthly == g["monthly"],
               "sector": (f.get("sector") or {}).get("value") == g["sector"], "got": {"name": name, "months": months, "total": total, "monthly": monthly,
                                                                                     "sector": (f.get("sector") or {}).get("value")}}
        s = r["sum"]
        text = it["text"][: 40_000]
        for sec in s["sections"]:
            for i in sec["items"]:
                total_items += 1
                if text[i["start"]:i["end"]] != i["quote"] or not numbers_supported(i["text"], i["quote"]):
                    bad_items += 1
        staffing = next(x for x in s["sections"] if x["key"] == "staffing")
        empty_staffing += not staffing["items"]
        row["summary_items"] = {x["key"]: len(x["items"]) for x in s["sections"]}
        per.append(row)
        for part in (r["meta"], s):
            u = part.get("usage") or {}
            if u:
                lat.append(u.get("latency_s", 0))
                cost += u.get("cost_usd", 0)
        dropped_meta += [d["why"] for d in r["meta"].get("dropped", [])]
        dropped_sum += [d["why"] for d in s.get("dropped", [])]
    n = len(per)
    count = lambda k: sum(1 for p in per if p[k])                       # noqa: E731
    return {"n": n, "name": count("name"), "months": count("months"), "total": count("total"), "monthly": count("monthly"),
            "sector": count("sector"), "ai_fields": ai_fields, "summary_items": total_items, "summary_bad_items": bad_items, "empty_staffing": empty_staffing,
            "latency_p50": statistics.median(lat) if lat else None, "latency_max": max(lat) if lat else None, "cost_usd": round(cost, 5),
            "dropped_meta": dropped_meta, "dropped_summary": dropped_sum, "rows": per}


def gates(s: dict) -> dict:
    n = s["n"]
    return {"G1_name": s["name"] >= n - 1, "G2_months": s["months"] >= n - 1, "G3_monthly": s["monthly"] >= n - 1,
            "G4_checker_consistency": s["summary_bad_items"] == 0, "G5_staffing": s["empty_staffing"] <= 1}


def real_docs(path: Path, out_path: Path, *, client, model, effort, price) -> None:
    from api.project_docs import extract_text
    from api.rag.doc_brief import read_project_meta, summarize_document
    res = {}
    for f in sorted(path.iterdir()):
        ext = f.suffix.lstrip(".").lower()
        if ext not in ("pdf", "hwp", "hwpx", "docx"):
            continue
        text = extract_text(ext, f.read_bytes()) or ""
        m = read_project_meta(text, client=client, model=model, reasoning_effort=effort, price=price)
        s = summarize_document(text, "", client=client, model=model, reasoning_effort=effort, price=price)
        res[f.name] = {"chars": len(text), "meta": {k: (v and {"value": v["value"], "source": v["source"]}) for k, v in m["fields"].items()},
                       "meta_dropped": m["dropped"], "summary": {"doc_type": s["doc_type"], "relevance": s["relevance"], "one_line": s["one_line"],
                                                                  "items": {x["key"]: [i["text"] for i in x["items"]] for x in s["sections"]},
                                                                  "dropped": s["dropped"]}, "usage": [m["usage"], s["usage"]]}
        print(f"  real {f.name}: {s['doc_type']} / {s['relevance']}", flush=True)
    out_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule-only", action="store_true")
    ap.add_argument("--real-dir")
    ap.add_argument("--real-out", help="실제 공공 자료 결과 파일 -- 저장소 밖 경로로(--real-dir와 함께 꼭 준다)")
    a = ap.parse_args()
    if a.real_dir and not a.real_out:
        ap.error("--real-dir에는 --real-out(저장소 밖 경로)을 함께 준다")
    from core.config import load_env, load_pricing
    load_env()
    items = load_items()
    pricing = load_pricing()
    res = {"experiment": "doc-brief", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "api/rag/doc_brief.py", "scripts/eval_doc_brief.py",
                                         "demo/project_docs"], capture_output=True, text=True).stdout.strip()),
           "docs": len(items), "gates_def": GATES, "methods": {}}
    print("규칙", flush=True)
    res["methods"]["rule"] = score(items, run(items))
    if not a.rule_only:
        from openai import OpenAI
        model = pricing["briefing_model"]
        effort = pricing["models"].get(model, {}).get("reasoning_effort")
        print(f"OpenAI {model} ({effort})", flush=True)
        oa = OpenAI(timeout=180, max_retries=1)
        ai = score(items, run(items, client=oa, model=model, effort=effort, price=pricing["models"].get(model)))
        res["methods"]["openai"] = {**ai, "model": model, "reasoning_effort": effort}
        res["gates"] = gates(ai)
        if os.environ.get("ZAI_API_KEY"):
            print(f"GLM {GLM_MODEL} ({GLM_EFFORT})", flush=True)
            glm_client = OpenAI(base_url=GLM_URL, api_key=os.environ["ZAI_API_KEY"], timeout=600, max_retries=1)
            glm = score(items, run(items, client=glm_client, model=GLM_MODEL, effort=GLM_EFFORT, price=pricing["models"].get(GLM_MODEL)))
            res["methods"]["glm"] = {**glm, "model": GLM_MODEL, "reasoning_effort": GLM_EFFORT}
            res["gates_glm_report_only"] = gates(glm)
        else:
            res["methods"]["glm"] = {"skipped": "ZAI_API_KEY 없음"}
        if a.real_dir:
            real_docs(Path(a.real_dir), Path(a.real_out), client=oa, model=model, effort=effort,
                      price=pricing["models"].get(model))
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    brief = {k: {m: v.get(m) for m in ("name", "months", "total", "monthly", "sector", "ai_fields", "summary_items", "summary_bad_items", "empty_staffing",
                                         "latency_p50", "cost_usd")} for k, v in res["methods"].items() if "rows" in v}
    print(json.dumps({"brief": brief, "gates": res.get("gates"), "gates_glm_report_only": res.get("gates_glm_report_only")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
