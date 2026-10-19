"""소명 요약 채택률(2026-10-20, 사용자 "인사팀 소명은 너무 길어서 읽을수가 없어 … 출처에서 나온 사실만 … 결과를 검증").

E7 시연 묶음(rehearsal.justify_scale.BUNDLES)의 배치 있는 사업마다 요약을 RUNS번 쓰게 해(api.rag.justification.generate_summary, 서비스와 같은 모델·추론)
검사(core.kg.justify.verify_summary)를 통과해 AI 요약을 쓴 비율을 잰다. 통과하지 못하면 화면은 규칙 요약을 쓴다 -- 그 비율도 같이.
채택된 요약은 무결성을 따로 확인한다: 중괄호가 남지 않고, 맺음말 빼고 3문장 이하이며, 불리한 사실이 있으면 그중 하나 이상의 문구가 들어 있다.

  uv run python -m rehearsal.justify_summary      → rehearsal/results/justify-summary.json
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rehearsal import justify_scale as e7b

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rehearsal" / "results" / "justify-summary.json"
RUNS, WORKERS, COST_CAP_USD = 1, 6, 2.0


def integrity(summary: str, inp) -> list[str]:
    from core.kg.justify import SUMMARY_MAX_SENTENCES
    probs = []
    if "{" in summary or "}" in summary:
        probs.append("중괄호")
    body = [s for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s and "이상의 근거로" not in s]
    if len(body) > SUMMARY_MAX_SENTENCES:
        probs.append(f"{len(body)}문장")
    adv = [f for f in inp.facts if f.adverse]
    if adv and not any((f.phrase_full or f.phrase) in summary or f.phrase in summary for f in adv):
        probs.append("불리한 사실 없음")
    return probs


def main():
    from openai import OpenAI
    from api.rag.justification import generate_summary
    from core.config import load_env, load_pricing
    load_env()
    model = load_pricing()["briefing_model"]
    client = OpenAI(timeout=180, max_retries=1)
    res = {"experiment": "justify-summary", "when": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "model": model, "runs": RUNS, "calls": []}
    spent, lock, stop = 0.0, threading.Lock(), threading.Event()
    with tempfile.TemporaryDirectory(prefix="tw-sum-") as td:
        src = Path.home() / ".teamweaver" / "review_judgments_synthetic.json"
        cache = None
        if src.exists():                                   # 실제 데이터 폴더는 읽기만 -- 임시 복사본
            cache = Path(td) / "judge.json"
            shutil.copy(src, cache)
        jobs = []
        for name in e7b.BUNDLES:
            inps, _ = e7b.inputs_for(name, Path(td), cache)
            jobs += [(name, inp, r) for inp in inps for r in range(RUNS)]
            print(f"{name}: {len(inps)}개 사업", flush=True)

        def one(job):
            nonlocal spent
            name, inp, r = job
            if stop.is_set():
                return None
            out = generate_summary(client, model, inp)
            cost = (out.get("summary_usage") or {}).get("cost_usd") or 0.0
            with lock:
                spent += cost
                if spent > COST_CAP_USD:
                    stop.set()
            return {"bundle": name, "project": inp.project_id, "run": r, "team": len(inp.team),
                    "adverse": sum(f.adverse for f in inp.facts), "method": out["summary_method"],
                    "fallback": out["summary_fallback_reason"], "chars": len(out["summary"] or ""),
                    "latency_s": (out.get("summary_usage") or {}).get("latency_s"), "cost_usd": cost,
                    "integrity": integrity(out["summary"], inp) if out["summary"] else ["요약 없음"],
                    "summary": out["summary"]}
        with ThreadPoolExecutor(WORKERS) as pool:
            res["calls"] = [c for c in pool.map(one, jobs) if c]
    calls = res["calls"]
    n = len(calls)
    ai = [c for c in calls if c["method"] == "graphrag"]
    res["summary"] = {
        "planned": len(jobs), "done": n, "complete": n == len(jobs), "cost_usd": round(spent, 4),
        "ai_accepted": len(ai), "ai_rate": round(len(ai) / n, 4) if n else None,
        "rule_used": sum(c["method"] == "rule" for c in calls), "none": sum(c["method"] is None for c in calls),
        "fallback_reasons": Counter(c["fallback"] for c in calls if c["method"] != "graphrag").most_common(),
        "integrity_fail": [c for c in calls if c["method"] and c["integrity"]][:20],
        "integrity_checked": sum(1 for c in calls if c["method"]),
        "chars_p50": sorted(c["chars"] for c in calls)[n // 2] if n else None,
        "latency_p50": sorted(c["latency_s"] or 0 for c in calls)[n // 2] if n else None,
    }
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
