"""긴급 투입 "상황을 말로" 측정(2026-10-22, claude-a -- 설계 `.omc/plan/2026-10-22-ai-value.md` 4절).

같은 가상 요청 문장 48개(`rehearsal/data/emergency_requests.json`, 8유형 × 6, 정답은 측정 전에 커밋)를
  - 규칙: 사전 별칭 찾기 + 숫자·등급 정규식(AI 없음 -- `api.rag.request_parse.parse_request(client=None)`)
  - AI: 서비스와 같은 모델·추론 강도로 한 번씩(같은 함수, 서버 검사 포함)
으로 읽어 비교한다. 기술은 **언급 단위**로 센다(문장 48개로는 구간이 넓다 -- 조사 보고서 "평가" 절): TP = 뽑은 것 ∩ must, FP = 뽑은 것 − must − ok, FN = must − 뽑은 것.

사전 등록(측정 전에 커밋) -- 관문: (1) 화면에 나가는 기술은 모두 사전 id(사전 밖 0건), (2) 사람 특성 말을 근거로 한 기술 0건,
(3) 사람 특성 문장 6개 모두 경고. 그 밖(정밀도·재현율·인원·등급 일치, 유형별)은 판정 없이 보고하고 Wilson 95% 구간을 함께 적는다.
가설(판정 아님): AI는 "미루어 봄" 유형의 재현율을 규칙보다 크게 올리고, 명시·별칭 유형의 정밀도를 떨어뜨리지 않는다.

  uv run python -m rehearsal.request_parse_eval [--rule-only]      → rehearsal/results/request-parse.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import subprocess
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "rehearsal" / "data" / "emergency_requests.json"
OUT = ROOT / "rehearsal" / "results" / "request-parse.json"


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 3), round(c + h, 3)]


def score(items: list[dict], outs: list[dict], sd) -> dict:
    tot = defaultdict(int)
    by_type: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    rows = []
    for it, out in zip(items, outs):
        got = {s["id"] for s in out["skills"]}
        must, ok = set(it["must"]), set(it["ok"])
        tp, fp, fn = len(got & must), len(got - must - ok), len(must - got)
        hc_ok = out["headcount"] == it["headcount"]
        gr_ok = out["grade"] == it["grade"]
        prot = bool(it.get("protected"))
        warned = bool(out["warnings"])
        for d in (tot, by_type[it["type"]]):
            d["tp"] += tp
            d["fp"] += fp
            d["fn"] += fn
            d["items"] += 1
            d["headcount_ok"] += hc_ok
            d["grade_ok"] += gr_ok
        tot["out_of_dict"] += sum(1 for s in out["skills"] if s["id"] not in sd.concepts)
        tot["protected_items"] += prot
        tot["protected_warned"] += prot and warned
        tot["false_warnings"] += (not prot) and warned
        from api.rag.request_parse import PROTECTED
        tot["skill_from_protected"] += sum(1 for s in out["skills"] if PROTECTED.search(s["quote"]))
        rows.append({"text": it["text"], "type": it["type"], "got": sorted(got), "must": sorted(must), "tp": tp, "fp": fp, "fn": fn,
                     "headcount": out["headcount"], "grade": out["grade"], "warned": warned, "method": out["method"],
                     "dropped": out.get("dropped") or [], "unmapped": out.get("unmapped_terms") or []})

    def pr(d):
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else None
        r = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else None
        f1 = 2 * p * r / (p + r) if p and r else None
        return {"precision": round(p, 3) if p is not None else None, "precision_ci": wilson(d["tp"], d["tp"] + d["fp"]),
                "recall": round(r, 3) if r is not None else None, "recall_ci": wilson(d["tp"], d["tp"] + d["fn"]),
                "f1": round(f1, 3) if f1 is not None else None, "tp": d["tp"], "fp": d["fp"], "fn": d["fn"],
                "headcount_acc": round(d["headcount_ok"] / d["items"], 3), "grade_acc": round(d["grade_ok"] / d["items"], 3)}
    gates = {"out_of_dict_zero": tot["out_of_dict"] == 0, "no_skill_from_protected": tot["skill_from_protected"] == 0,
             "all_protected_warned": tot["protected_warned"] == tot["protected_items"]}
    return {"overall": pr(tot), "by_type": {t: pr(d) for t, d in sorted(by_type.items())},
            "protected": {"items": tot["protected_items"], "warned": tot["protected_warned"], "false_warnings": tot["false_warnings"],
                          "skills_from_protected": tot["skill_from_protected"]},
            "out_of_dict": tot["out_of_dict"], "gates": gates, "gates_pass": all(gates.values()), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule-only", action="store_true")
    args = ap.parse_args()
    from api.rag.justification import justification_settings
    from api.rag.request_parse import parse_request
    from core.config import load_env, load_pricing
    from core.ingest.skills import load_dictionary
    load_env()
    sd = load_dictionary()
    items = json.loads(DATA.read_text("utf-8"))["items"]
    pricing = load_pricing()
    model = pricing["briefing_model"]
    res = {"experiment": "request-parse", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "items": len(items), "model": model, "dictionary_version": sd.version}
    rule = [parse_request(it["text"], sd) for it in items]
    res["rule"] = score(items, rule, sd)
    if not args.rule_only:
        from openai import OpenAI
        client = OpenAI(timeout=120, max_retries=1)
        effort = justification_settings(model)["reasoning_effort"]
        price = pricing.get("models", {}).get(model, {})
        outs, t0 = [], time.monotonic()
        for it in items:
            outs.append(parse_request(it["text"], sd, client=client, model=model, reasoning_effort=effort, price=price))
        res["ai"] = score(items, outs, sd)
        us = [o["usage"] for o in outs if o.get("usage")]
        lat = sorted(u["latency_s"] for u in us)
        res["ai"]["cost"] = {"calls": len(us), "fallbacks": sum(1 for o in outs if o["method"] != "ai"),
                             "cost_usd": round(sum(u["cost_usd"] for u in us), 4), "avg_in_tokens": round(sum(u["in"] for u in us) / max(1, len(us))),
                             "latency_p50_s": lat[len(lat) // 2] if lat else None, "latency_max_s": lat[-1] if lat else None,
                             "wall_s": round(time.monotonic() - t0, 1)}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    for k in ("rule", "ai"):
        if k in res:
            o = res[k]["overall"]
            print(k, {x: o[x] for x in ("precision", "recall", "f1", "headcount_acc", "grade_acc")}, "gates", res[k]["gates_pass"],
                  {t: (v["recall"], v["precision"]) for t, v in res[k]["by_type"].items()})
    if "ai" in res:
        print("cost", res["ai"]["cost"])


if __name__ == "__main__":
    main()
