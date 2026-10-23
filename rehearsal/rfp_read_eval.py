"""발주 자료 필요 기술 읽기 측정(2026-10-23, claude-a -- claude-b 요청 `docs/requests/2026-10-23-rfp-skill-reading.md`, 사용자 "openai api,glm5.3-flash 각각 성능 확인 필요").

같은 자료를 세 방법으로 읽어 비교한다(`api.rag.rfp_read.read_requirements`, 서버 검사 포함):
  - 규칙: 사전 별칭 찾기 + 문장 말 판정(AI 없음, client=None) -- 기준선
  - OpenAI: 서비스 모델(`fixtures/pricing.json` briefing_model, 그 추론 강도)
  - GLM-5.3-Flash: 사내 LLM 대리(Z.ai, 추론 high -- 사용자 지시 2026-10-11 "flash에서 low로 하면 성과가 잘 안나온다는걸 확인했으니 high로"). 가상 자료만 외부로 보낸다.

자료
  - 쉬움: claude-b 시연 자료 7편(`demo/project_docs/index.json`, 표 + 문장) -- 정답 = 시연 연말형 묶음의 그 사업 요구 기술(`project_skill_requirements.csv`).
    과업 범위에 적힌 다른 기술은 정답에 없어 정밀도는 세지 않고 "그 밖에 뽑은 기술"로만 보고한다(재현율·경력·인원만 채점).
  - 어려움: 가상 자료 10편(`rehearsal/data/rfp_docs/` -- 짧은 8편 + 현행 장비 목록·일반 요구사항이 섞인 장문 2편, 정답 `rehearsal/data/rfp_gold.json` -- 필수·우대·요구 아님·어느 쪽도 괜찮음).

사전 등록(이 docstring·GATES·정답은 AI 측정 전에 커밋한다). 관문은 **OpenAI(서비스)**에만 판정한다:
  G1 필수 재현율(쉬움 + 어려움) ≥ 0.90
  G2 필수 정밀도(어려움 -- 정답이 완전한 세트) ≥ 0.85
  G3 요구 아닌 언급을 필수로 뽑은 비율(어려움) ≤ 0.15, 그리고 규칙보다 낮다
  G4 사전 밖 id로 나간 기술 0건(서버 검사 뒤)
  G5 경력 ±12개월 일치 ≥ 0.80, 인원 일치 ≥ 0.80(정답에 값이 있는 필수 기술 중 맞게 뽑은 것만)
  L1(2026-10-23 추가 -- 나눠 읽기, AI 측정 전에 커밋) 긴 자료(`rfp_gold.json` "long", 투입 인력 기준이 4만 자 뒤)의 필수 재현율 = 1.0이고 경력·인원도 모두 맞다.
     긴 자료는 본 지표(G1~G5)에 섞지 않고 `methods.<방법>.long`에 따로 채점한다.
GLM-5.3-Flash와 규칙은 같은 표에 보고만 한다(판정 없음). 그 밖 보고: 우대를 우대로(재현율), 필수+우대 합친 재현율, 한 편 시간 p50·최대, 비용, 버린 칸.
가설(판정 아님): 규칙은 현행 시스템 언급까지 필수로 뽑고(G3에서 높음), GLM은 문맥 판정이 OpenAI보다 약하다.

  uv run python -m rehearsal.rfp_read_eval [--rule-only] [--docs-dir demo/project_docs]   → rehearsal/results/rfp-read.json
  (긴 자료 다시 만들기: uv run python -m rehearsal.make_long_rfp)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "rehearsal" / "data" / "rfp_gold.json"
HARD_DIR = ROOT / "rehearsal" / "data" / "rfp_docs"
EASY_DIR = ROOT / "demo" / "project_docs"
EASY_BUNDLE = ROOT / "demo" / "org-n100-yearend"
OUT = ROOT / "rehearsal" / "results" / "rfp-read.json"
GLM_MODEL, GLM_EFFORT, GLM_URL = "glm-5.3-flash", "high", "https://api.z.ai/api/paas/v4"
GATES = {"recall_min": 0.90, "precision_min": 0.85, "nonreq_fp_max": 0.15, "months_min": 0.80, "headcount_min": 0.80, "long_recall_min": 1.0}


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 3), round(c + h, 3)]


def _text_of(path: Path) -> str:
    """b의 extract_text가 있으면 그것(서비스와 같은 글자), 없으면 pypdf로 같은 방식."""
    data = path.read_bytes()
    try:
        from api.project_docs import extract_text            # claude-b (발주 자료 첨부)
        t = extract_text(path.suffix.lstrip("."), data)
        if t is not None:
            return t
    except ImportError:
        pass
    from io import BytesIO

    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(data)).pages)[:60_000]


def load_sets(docs_dir: Path) -> list[dict]:
    sd_label = None                                   # 쉬운 세트 정답: 묶음 요구 기술 이름 → 사전 대표 이름
    from core.ingest.skills import load_dictionary
    sd = load_dictionary()
    sd_label = lambda n: (sd.lookup(n) or {}).get("label", n)   # noqa: E731
    items = []
    idx = docs_dir / "index.json"
    if idx.exists():
        reqs: dict[str, list[dict]] = {}
        with open(EASY_BUNDLE / "project_skill_requirements.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                reqs.setdefault(r["project_id"], []).append({"skill": sd_label(r["skill_name"]), "months": int(r["min_experience_months"]),
                                                            "headcount": int(r["headcount"])})
        for d in json.loads(idx.read_text("utf-8"))["docs"]:
            items.append({"set": "easy", "id": d["id"], "style": d["kind"], "text": _text_of(docs_dir / d["file"]),
                          "required": reqs.get(d["project_id"], []), "preferred": [], "not_required": [], "ok": [], "complete": False})
    gold = json.loads(GOLD.read_text("utf-8"))
    for d in gold["docs"]:
        items.append({"set": "hard", "id": d["id"], "style": d["style"], "text": (HARD_DIR / d["file"]).read_text("utf-8"),
                      "required": d["required"], "preferred": d["preferred"], "not_required": d["not_required"], "ok": d["ok"],
                      "complete": True})
    for d in gold.get("long") or []:
        items.append({"set": "long", "id": d["id"], "style": d["style"], "text": (HARD_DIR / d["file"]).read_text("utf-8"),
                      "required": d["required"], "preferred": d["preferred"], "not_required": d["not_required"], "ok": d["ok"],
                      "complete": True})
    return items


def score_split(items: list[dict], outs: list[dict], sd) -> dict:
    """본 지표(쉬움·어려움)와 긴 자료를 따로 채점한다."""
    main = [(i, o) for i, o in zip(items, outs) if i["set"] != "long"]
    long_ = [(i, o) for i, o in zip(items, outs) if i["set"] == "long"]
    res = score([i for i, _ in main], [o for _, o in main], sd)
    if long_:
        lg = score([i for i, _ in long_], [o for _, o in long_], sd)
        res["long"] = {**{k: lg[k] for k in ("totals", "recall", "precision_hard", "nonreq_fp_rate", "pref_recall", "months_match",
                                            "headcount_match", "latency_p50", "cost_usd")},
                       "chunks": [len(o.get("chunks") or []) for _, o in long_], "warnings": [o.get("warnings") for _, o in long_],
                       "rows": lg["rows"]}
    return res


def score(items: list[dict], outs: list[dict], sd) -> dict:
    t = {k: 0 for k in ("req_gold", "req_tp", "req_fp", "pred_req_complete", "nonreq_gold", "nonreq_as_req", "pref_gold", "pref_as_pref",
                        "any_tp", "months_n", "months_ok", "head_n", "head_ok", "out_of_dict", "dropped", "extras_easy")}
    rows = []
    for it, out in zip(items, outs):
        lab = lambda s: sd.concepts[s["concept_id"]]["label"] if s["concept_id"] in sd.concepts else s["concept_id"]   # noqa: E731
        t["out_of_dict"] += sum(1 for s in out["skills"] if s["concept_id"] not in sd.concepts)
        t["dropped"] += len(out.get("dropped") or [])
        pred_req = {lab(s): s for s in out["skills"] if s["kind"] == "required"}
        pred_pref = {lab(s): s for s in out["skills"] if s["kind"] == "preferred"}
        gold_req = {g["skill"]: g for g in it["required"]}
        ok, nonreq, pref = set(it["ok"]), set(it["not_required"]), set(it["preferred"])
        tp = set(pred_req) & set(gold_req)
        t["req_gold"] += len(gold_req)
        t["req_tp"] += len(tp)
        t["any_tp"] += len(set(gold_req) & (set(pred_req) | set(pred_pref)))
        if it["complete"]:
            fp = set(pred_req) - set(gold_req) - ok
            t["req_fp"] += len(fp)
            t["pred_req_complete"] += len(set(pred_req) - ok)
            t["nonreq_gold"] += len(nonreq)
            t["nonreq_as_req"] += len(set(pred_req) & nonreq)
            t["pref_gold"] += len(pref)
            t["pref_as_pref"] += len(set(pred_pref) & pref)
        else:
            fp = set()
            t["extras_easy"] += len(set(pred_req) - set(gold_req))
        for name in tp:
            g, p = gold_req[name], pred_req[name]
            if g.get("months") is not None:
                t["months_n"] += 1
                t["months_ok"] += p["months"] is not None and abs(p["months"] - g["months"]) <= 12
            if g.get("headcount") is not None:
                t["head_n"] += 1
                t["head_ok"] += p["headcount"] == g["headcount"]
        rows.append({"set": it["set"], "id": it["id"], "style": it["style"], "method": out["method"], "fallback": out.get("fallback_reason"),
                     "missed": sorted(set(gold_req) - set(pred_req)), "false_required": sorted(fp),
                     "nonreq_as_req": sorted(set(pred_req) & nonreq) if it["complete"] else [],
                     "pref_ok": sorted(set(pred_pref) & pref), "pref_missed": sorted(pref - set(pred_pref)),
                     "extras": [] if it["complete"] else sorted(set(pred_req) - set(gold_req)),
                     "skills": [{"name": lab(s), "kind": s["kind"], "months": s["months"], "headcount": s["headcount"], "quote": s["quote"]}
                                for s in out["skills"]],
                     "mentioned_not_required": [m["name"] for m in out.get("mentioned_not_required") or []],
                     "unmapped": out.get("unmapped_terms") or [], "dropped": out.get("dropped") or [], "usage": out.get("usage")})
    r = lambda a, b: round(a / b, 3) if b else None                    # noqa: E731
    lat = [x["usage"]["latency_s"] for x in rows if x.get("usage")]
    cost = sum((x["usage"] or {}).get("cost_usd", 0) for x in rows if x.get("usage"))
    return {"totals": t,
            "recall": r(t["req_tp"], t["req_gold"]), "recall_ci": wilson(t["req_tp"], t["req_gold"]),
            "precision_hard": r(t["pred_req_complete"] - t["req_fp"], t["pred_req_complete"]),
            "precision_ci": wilson(t["pred_req_complete"] - t["req_fp"], t["pred_req_complete"]),
            "nonreq_fp_rate": r(t["nonreq_as_req"], t["nonreq_gold"]), "pref_recall": r(t["pref_as_pref"], t["pref_gold"]),
            "any_kind_recall": r(t["any_tp"], t["req_gold"]),
            "months_match": r(t["months_ok"], t["months_n"]), "headcount_match": r(t["head_ok"], t["head_n"]),
            "latency_p50": round(statistics.median(lat), 2) if lat else None, "latency_max": max(lat) if lat else None,
            "cost_usd": round(cost, 4), "rows": rows}


def gates(ai: dict, rule: dict) -> dict:
    g = GATES
    out = {"G1_recall": ai["recall"] is not None and ai["recall"] >= g["recall_min"],
           "G2_precision": ai["precision_hard"] is not None and ai["precision_hard"] >= g["precision_min"],
           "G3_nonreq": ai["nonreq_fp_rate"] is not None and ai["nonreq_fp_rate"] <= g["nonreq_fp_max"]
                        and (rule["nonreq_fp_rate"] is None or ai["nonreq_fp_rate"] < rule["nonreq_fp_rate"]),
           "G4_dict": ai["totals"]["out_of_dict"] == 0,
           "G5_numbers": (ai["months_match"] or 0) >= g["months_min"] and (ai["headcount_match"] or 0) >= g["headcount_min"]}
    lg = ai.get("long")
    if lg:
        out["L1_long"] = (lg["recall"] or 0) >= g["long_recall_min"] and lg["months_match"] == 1.0 and lg["headcount_match"] == 1.0
    out["all"] = all(out.values())
    return out


def run(items, sd, *, client=None, model=None, effort=None, price=None) -> list[dict]:
    from api.rag.rfp_read import read_requirements
    outs = []
    for it in items:
        o = read_requirements(it["text"], sd, client=client, model=model, reasoning_effort=effort, price=price)
        if client is not None and o["method"] != "ai":
            # 한 번만 다시(네트워크 오류) -- 그래도 실패면 그 편은 규칙 값으로 남기고 기록한다
            o = read_requirements(it["text"], sd, client=client, model=model, reasoning_effort=effort, price=price)
        outs.append(o)
        print(f"  {it['set']}/{it['id']}: {o['method']} {len(o['skills'])}개 {(o.get('usage') or {}).get('latency_s', '')}", flush=True)
    return outs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule-only", action="store_true")
    ap.add_argument("--docs-dir", default=str(EASY_DIR), help="쉬운 세트(claude-b 시연 자료) 폴더")
    a = ap.parse_args()
    from core.config import load_env, load_pricing
    from core.ingest.skills import load_dictionary
    load_env()
    sd = load_dictionary()
    items = load_sets(Path(a.docs_dir))
    pricing = load_pricing()
    res = {"experiment": "rfp-read", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "api/rag", "rehearsal/rfp_read_eval.py",
                                         "rehearsal/data"], capture_output=True, text=True).stdout.strip()),
           "docs": {"easy": sum(1 for i in items if i["set"] == "easy"), "hard": sum(1 for i in items if i["set"] == "hard"),
                    "long": sum(1 for i in items if i["set"] == "long"),
                    "easy_dir": str(Path(a.docs_dir))}, "gates_def": GATES, "methods": {}}
    print("규칙", flush=True)
    rule = score_split(items, run(items, sd), sd)
    res["methods"]["rule"] = rule
    if not a.rule_only:
        from openai import OpenAI
        model = pricing["briefing_model"]
        effort = pricing["models"].get(model, {}).get("reasoning_effort")
        print(f"OpenAI {model} ({effort})", flush=True)
        ai = score_split(items, run(items, sd, client=OpenAI(timeout=180, max_retries=1), model=model, effort=effort,
                              price=pricing["models"].get(model)), sd)
        res["methods"]["openai"] = {**ai, "model": model, "reasoning_effort": effort}
        res["gates"] = gates(ai, rule)
        if os.environ.get("ZAI_API_KEY"):
            print(f"GLM {GLM_MODEL} ({GLM_EFFORT})", flush=True)
            glm = score_split(items, run(items, sd, client=OpenAI(base_url=GLM_URL, api_key=os.environ["ZAI_API_KEY"], timeout=600, max_retries=1),
                                   model=GLM_MODEL, effort=GLM_EFFORT, price=pricing["models"].get(GLM_MODEL)), sd)
            res["methods"]["glm"] = {**glm, "model": GLM_MODEL, "reasoning_effort": GLM_EFFORT}
            res["gates_glm_report_only"] = gates(glm, rule)
        else:
            res["methods"]["glm"] = {"skipped": "ZAI_API_KEY 없음"}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    brief = {k: {**{m: v.get(m) for m in ("recall", "precision_hard", "nonreq_fp_rate", "pref_recall", "any_kind_recall", "months_match",
                                            "headcount_match", "latency_p50", "cost_usd")},
                 "long": {m: (v.get("long") or {}).get(m) for m in ("recall", "months_match", "headcount_match", "pref_recall", "nonreq_fp_rate",
                                                                    "chunks")}}
             for k, v in res["methods"].items() if "rows" in v}
    print(json.dumps({"brief": brief, "gates": res.get("gates")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
